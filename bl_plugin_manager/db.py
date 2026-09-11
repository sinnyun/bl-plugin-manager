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


def cached_load(path: str):
    """按 (mtime, size) 复用已解析数据；文件变了才重新读。"""
    try:
        st = os.stat(path)
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    hit = _CACHE.get(path)
    if hit and hit[0] == stamp:
        return copy.deepcopy(hit[1])
    return None


def cache_store(path: str, data) -> None:
    try:
        st = os.stat(path)
        # Never retain a live LibraryDB.data object in the process cache.  Callers
        # routinely keep mutating it after save(); a deep snapshot prevents those
        # unsaved mutations leaking into later LibraryDB instances.
        _CACHE[path] = ((st.st_mtime_ns, st.st_size), copy.deepcopy(data))
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
        self.load()

    # -- 磁盘 IO -----------------------------------------------------------
    def load(self) -> None:
        if self._use_cache:
            hit = cached_load(self.path)
            if hit is not None:
                # 复制一份，避免调用方就地修改污染缓存
                self.data = json.loads(json.dumps(hit, ensure_ascii=False))
                self._fresh = True
                return
        self._load_from_disk()
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
                return
        except FileNotFoundError:
            pass
        except Exception:
            # 损坏时保留原文件备份，避免用户数据被静默覆盖
            try:
                if os.path.exists(self.path):
                    shutil.copy2(self.path, self.path + ".corrupt")
            except Exception:
                pass
        self.data = _empty_db()

    def save(self) -> None:
        self.data["updated"] = now_iso()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            # 更新缓存，避免紧接着的读取又解析一遍
            if self._use_cache:
                cache_store(self.path, self.data)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

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
        if C.DEFAULT_CATEGORY not in cats:
            cats.insert(0, C.DEFAULT_CATEGORY)
        return cats

    def ensure_category(self, name: str) -> None:
        """确保某个分类存在于列表中（用户把插件归入该分类时调用）。"""
        name = (name or "").strip()
        if name and name not in self.categories:
            self.categories.append(name)

    def add_category(self, name: str) -> bool:
        name = (name or "").strip()
        if not name or name in self.categories:
            return False
        self.categories.append(name)
        return True

    def rename_category(self, old: str, new: str) -> None:
        new = (new or "").strip()
        if not new:
            return
        cats = self.categories
        if old in cats:
            cats[cats.index(old)] = new
        for rec in self.plugins.values():
            if rec.get("category") == old:
                rec["category"] = new

    def delete_category(self, name: str) -> None:
        """删除分类，其下插件回到「未分类」（不会删除插件）。"""
        cats = self.categories
        if name in cats and name != C.DEFAULT_CATEGORY:
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
