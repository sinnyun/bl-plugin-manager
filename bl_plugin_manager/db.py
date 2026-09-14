"""插件库元数据存储：分类、备注、标签、收藏、来源、更新信息。

数据保存在 <库>/.pm/library.json，与 Blender 版本无关，升级后继续可用。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import copy
from datetime import datetime

from . import constants as C


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _empty_db() -> dict:
    return {
        "schema": C.DB_SCHEMA,
        "updated": now_iso(),
        "categories": [],
        "plugins": {},  # key(相对路径, posix) -> record
    }


# 已解析缓存：path -> (mtime, size, data)。面板每次重绘都会新建 LibraryDB
# （分类计数、选中详情、自启统计等），若不缓存会反复解析同一个 JSON。
_CACHE: dict[str, tuple] = {}


class CorruptDatabaseError(RuntimeError):
    """The metadata source is malformed and must not be overwritten."""


_V2_RUNTIME_FIELDS = frozenset({
    "module", "enabled", "missing", "load_state", "load_error", "last_error",
    "restore_error", "residue", "compat", "compat_detail", "metadata_fingerprint",
    "last_checked", "update_available", "latest_version", "latest_url",
    "latest_hash", "latest_size", "latest_website",
})


def _file_signature(path: str):
    """Return replacement-sensitive metadata for a database file."""
    try:
        st = os.stat(path)
        return (getattr(st, "st_dev", 0), getattr(st, "st_ino", 0),
                st.st_ctime_ns, st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def cached_load(path: str):
    """按替换敏感签名复用已解析数据；文件变了才重新读。"""
    stamp = _file_signature(path)
    if stamp is None:
        return None
    hit = _CACHE.get(path)
    if hit and hit[0] == stamp:
        return copy.deepcopy(hit[1])
    return None


def cache_store(path: str, data) -> None:
    try:
        stamp = _file_signature(path)
        if stamp is None:
            return
        # Never retain a live LibraryDB.data object in the process cache.  Callers
        # routinely keep mutating it after save(); a deep snapshot prevents those
        # unsaved mutations leaking into later LibraryDB instances.
        _CACHE[path] = (stamp, copy.deepcopy(data))
    except OSError:
        pass


def cache_invalidate(path: str = "") -> None:
    if path:
        _CACHE.pop(path, None)
    else:
        _CACHE.clear()


class LibraryDB:
    """library.json 的读写封装。"""

    def __init__(self, root: str, use_cache: bool = True):
        self.root = root
        self.path = os.path.join(root, C.DIR_META, C.DB_FILENAME)
        self._use_cache = use_cache
        self.data = _empty_db()
        self.status = "MISSING"
        self.loaded_signature = None
        self.load()

    # -- 磁盘 IO -----------------------------------------------------------
    def load(self) -> None:
        if self._use_cache:
            hit = cached_load(self.path)
            if hit is not None:
                # 复制一份，避免调用方就地修改污染缓存
                self.data = json.loads(json.dumps(hit, ensure_ascii=False))
                self.loaded_signature = _file_signature(self.path)
                self._hydrate_runtime()
                self._fresh = True
                return
        self._load_from_disk()
        self.loaded_signature = _file_signature(self.path)
        self._hydrate_runtime()
        if self._use_cache:
            cache_store(self.path, self.data)

    def _load_from_disk(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self.data = data
                self.data.setdefault("plugins", {})
                self.data.setdefault("categories", [])
                self.data.setdefault("schema", C.DB_SCHEMA)
                self.status = "OK"
                return
            self.status = "CORRUPT"
        except FileNotFoundError:
            self.status = "MISSING"
        except Exception:
            self.status = "CORRUPT"
            # 损坏时保留原文件备份，避免用户数据被静默覆盖
            try:
                if os.path.exists(self.path):
                    shutil.copy2(self.path, self.path + ".corrupt")
            except Exception:
                pass
        self.data = _empty_db()

    def save(self) -> None:
        if self.status == "CORRUPT":
            raise CorruptDatabaseError(f"拒绝覆盖损坏数据库: {self.path}")
        runtime = self._split_runtime()
        self.data["updated"] = now_iso()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self.loaded_signature = _file_signature(self.path)
            # 更新缓存，避免紧接着的读取又解析一遍
            if self._use_cache:
                cache_store(self.path, self.data)
            if runtime is not None:
                self._save_runtime(runtime)
                for key, fields in runtime.get("_restore", {}).items():
                    if key in self.data["plugins"]:
                        self.data["plugins"][key].update(fields)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _state_store(self):
        from .storage.local_state import StateStore
        env = os.environ.get("BL_PLUGIN_MANAGER_ENVIRONMENT")
        if not env:
            env = f"{os.name}-python-{__import__('sys').version_info.major}.{__import__('sys').version_info.minor}"
        return StateStore(str(self.data.get("library_id")), env)

    def _hydrate_runtime(self) -> None:
        if not self.data.get("library_id") or self.data.get("schema") != 2:
            return
        state = self._state_store().load()
        for key, fields in state.get("plugins", {}).items():
            if key in self.plugins and isinstance(fields, dict):
                self.plugins[key].update(fields)

    def _split_runtime(self):
        if not self.data.get("library_id") or self.data.get("schema") != 2:
            return None
        from .storage.shared_db import SHARED_PLUGIN_FIELDS
        runtime = {"plugins": {}, "_restore": {}}
        for key, rec in list(self.plugins.items()):
            fields = {k: rec[k] for k in _V2_RUNTIME_FIELDS if k in rec}
            if fields:
                runtime["plugins"][key] = fields
                runtime["_restore"][key] = fields
            # The shared file is the synchronization contract.  A number of
            # older call sites build richer in-memory records (including
            # source paths); retain only explicitly portable fields before it
            # is written so a local path can never escape into synced data.
            self.plugins[key] = {
                field: value for field, value in rec.items()
                if field in SHARED_PLUGIN_FIELDS
            }
        return runtime

    def _save_runtime(self, runtime) -> None:
        payload = {"schema": 1, "plugins": runtime["plugins"]}
        self._state_store().save(payload)

    # -- 记录访问 ----------------------------------------------------------
    @property
    def plugins(self) -> dict:
        return self.data.setdefault("plugins", {})

    def get(self, key: str) -> dict | None:
        return self.plugins.get(key)

    def upsert(self, key: str, record: dict) -> dict:
        old = self.plugins.get(key, {})
        old.update(record)
        old["key"] = key
        old.setdefault("created_at", now_iso())
        old["updated_at"] = now_iso()
        self.plugins[key] = old
        return old

    def merge_scan(self, entries: list[dict]) -> dict:
        """Merge only portable scan fields for a schema 2 database."""
        if self.data.get("schema") != 2 or not self.data.get("library_id"):
            raise RuntimeError("portable scan merge requires schema 2 database")
        from .services.sync_v2 import merge_scan
        old = self.plugins
        merged = merge_scan(old, entries)
        for key, record in merged.items():
            if key in old:
                record.update({field: old[key][field] for field in _V2_RUNTIME_FIELDS if field in old[key]})
        self.data["plugins"] = merged
        self.data["revision"] = int(self.data.get("revision", 0)) + 1
        self.save()
        return self.plugins

    def remove(self, key: str) -> None:
        self.plugins.pop(key, None)

    def all(self) -> list[dict]:
        return list(self.plugins.values())

    # -- 分类 --------------------------------------------------------------
    @property
    def categories(self) -> list[str]:
        """用户分类列表：扁平结构、不嵌套。

        只包含你自己创建的（以及显式登记过的）分类，**不会**把插件自带的
        元数据分类自动灌进来——否则会出现几十个 "3D View"/"Animation" 之类的
        分类把列表挤乱。插件自带的分类保存在每条记录的 ``auto_category`` 里，
        需要时可用「采用自带分类」单独取用。
        """
        cats = self.data.setdefault("categories", [])
        # schema 2 stores stable category ids and labels; expose labels to the
        # legacy UI API while preserving the portable on-disk shape.
        if cats and isinstance(cats[0], dict):
            if not any(c.get("id") == "uncategorized" for c in cats):
                cats.insert(0, {"id": "uncategorized", "name": C.DEFAULT_CATEGORY, "order": 0})
            return [str(c.get("name") or C.DEFAULT_CATEGORY) for c in cats]
        if C.DEFAULT_CATEGORY not in cats:
            cats.insert(0, C.DEFAULT_CATEGORY)
        return cats

    def ensure_category(self, name: str) -> None:
        """确保某个分类存在于列表中（用户把插件归入该分类时调用）。"""
        name = (name or "").strip()
        if name and name not in self.categories:
            cats = self.data.setdefault("categories", [])
            if cats and isinstance(cats[0], dict):
                cats.append({"id": name, "name": name, "order": len(cats)})
            else:
                cats.append(name)

    def add_category(self, name: str) -> bool:
        name = (name or "").strip()
        if not name or name in self.categories:
            return False
        cats = self.data.setdefault("categories", [])
        if cats and isinstance(cats[0], dict):
            cats.append({"id": name, "name": name, "order": len(cats)})
        else:
            cats.append(name)
        return True

    def rename_category(self, old: str, new: str) -> None:
        new = (new or "").strip()
        if not new:
            return
        cats = self.data.setdefault("categories", [])
        if cats and isinstance(cats[0], dict):
            for item in cats:
                if item.get("name") == old:
                    item["name"] = new
                    item["id"] = new
        elif old in cats:
            cats[cats.index(old)] = new
        for rec in self.plugins.values():
            if rec.get("category") == old:
                rec["category"] = new

    def delete_category(self, name: str) -> None:
        """删除分类，其下插件回到「未分类」（不会删除插件）。"""
        cats = self.data.setdefault("categories", [])
        if cats and isinstance(cats[0], dict):
            if name != C.DEFAULT_CATEGORY:
                cats[:] = [c for c in cats if c.get("name") != name]
        elif name in cats and name != C.DEFAULT_CATEGORY:
            cats.remove(name)
        for rec in self.plugins.values():
            if rec.get("category") == name:
                rec["category"] = C.DEFAULT_CATEGORY

    def reset_auto_categories(self) -> dict:
        """把「仍是插件自带分类、你未改动过」的归类收回为未分类。

        只影响 category == auto_category 的记录（即从未被人为指定过的），
        你自己创建/指定的分类与归属会完整保留。
        """
        moved = 0
        for rec in self.plugins.values():
            auto = (rec.get("auto_category") or "").strip()
            cur = (rec.get("category") or "").strip()
            if cur and cur != C.DEFAULT_CATEGORY and cur == auto:
                rec["category"] = C.DEFAULT_CATEGORY
                moved += 1
        # 分类列表重建为：未分类 + 仍在使用的自定义分类
        remaining = {
            (rec.get("category") or "").strip()
            for rec in self.plugins.values()
            if (rec.get("category") or "").strip()
            and (rec.get("category") or "").strip() != C.DEFAULT_CATEGORY
        }
        if self.data.get("categories") and isinstance(self.data["categories"][0], dict):
            self.data["categories"] = [
                {"id": "uncategorized", "name": C.DEFAULT_CATEGORY, "order": 0}
            ] + [
                {"id": name, "name": name, "order": i + 1}
                for i, name in enumerate(sorted(remaining))
            ]
        else:
            self.data["categories"] = [C.DEFAULT_CATEGORY] + sorted(remaining)
        return {"moved": moved, "categories_left": len(self.data["categories"])}

    def category_counts(self) -> dict:
        counts = {name: 0 for name in self.categories}
        for rec in self.plugins.values():
            cat = rec.get("category") or C.DEFAULT_CATEGORY
            counts[cat] = counts.get(cat, 0) + 1
        return counts

    # -- 自启标记 ----------------------------------------------------------
    def startup_stats(self) -> dict:
        total = len(self.plugins)
        marked = sum(1 for r in self.plugins.values() if r.get("startup"))
        return {"total": total, "startup": marked, "not_startup": total - marked}
