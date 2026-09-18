"""库数据视图：把总资料库、本机绑定与本机运行状态合成一份记录。

数据分布在三个文件，职责严格分离——**总插件数据库**只保存所有电脑共享的
插件资料，**本机数据**保存这台电脑自己的安装位置与行为决策：

===============================  ===========================================
文件                              内容
===============================  ===========================================
``<库>/.pm/catalog.json``         **总插件数据库**（可同步）：插件身份、
                                 别名、分类、标签、备注、收藏等共享资料。
                                 绝不写入路径、模块名、启用状态或自启。
``<库>/.pm/devices/<id>.json``    本机启用意图、仓库与脚本目录。
``%LOCALAPPDATA%/.../state/...``  本机运行状态：``module`` 模块名、``enabled``
                                 是否启用、``startup`` 是否随启动加载、加载
                                 结果，以及 ``plugin_id -> 相对路径`` 绑定。
===============================  ===========================================

``LibraryDB`` 是这三者的**视图**：``plugins`` 以稳定 ``plugin_id`` 为键，记录里
既有共享字段，也有本机解析出的 ``rel`` / ``module`` / ``enabled`` / ``startup``。
写回时按字段归属拆分，因此本机路径与本机行为永远不会进入可同步的总资料库，
各电脑之间不会互相覆盖。

本模块不直接依赖 Blender。
"""

from __future__ import annotations

import os

from . import constants as C
from .storage import catalog as catalog_store
from .storage import json_cache
from .storage.catalog import PORTABLE_FIELDS, Catalog
from .storage.local_state import StateStore


def now_iso() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 本机运行状态字段：不进入总资料库，写入 LOCALAPPDATA 的环境状态文件。
# ``startup`` 属于本机行为决策（各电脑安装的插件不同，不应互相覆盖），
# 因此与 ``enabled`` 一样按机器保存，而不是随总插件数据库同步。
_V2_RUNTIME_FIELDS = frozenset({
    "module", "enabled", "startup", "missing", "load_state", "load_error",
    "last_error", "restore_error", "residue", "compat", "compat_detail",
    "metadata_fingerprint", "last_checked", "update_available", "latest_version",
    "latest_url", "latest_hash", "latest_size", "latest_website",
    # 来源与合规检查结果：origin_path 是本机绝对路径，只存本机。
    "origin", "origin_path", "imported_at", "issues",
})

# 本机绑定字段：plugin_id -> 相对路径，随运行状态一起保存在本机。
_BINDING_FIELDS = frozenset({"rel"})


class CorruptDatabaseError(RuntimeError):
    """The metadata source is malformed and must not be overwritten."""


def _file_signature(path):
    """Return replacement-sensitive metadata for a database file."""
    return json_cache.signature(str(path))


# 兼容旧接口：曾经的解析缓存入口，现在统一由 json_cache 承担。
def cached_load(path: str):
    _sig, data = json_cache.read(str(path))
    return data


def cache_store(path: str, data) -> None:
    json_cache.store(str(path), data)


def cache_invalidate(path: str = "") -> None:
    json_cache.invalidate(path)


def _empty_view() -> dict:
    return {
        "schema": C.DB_SCHEMA,
        "library_id": "",
        "revision": 0,
        "categories": [dict(catalog_store.DEFAULT_CATEGORY)],
        "plugins": {},
    }


def _environment() -> str:
    env = os.environ.get("BL_PLUGIN_MANAGER_ENVIRONMENT")
    if env:
        return env
    import sys
    return f"{os.name}-python-{sys.version_info.major}.{sys.version_info.minor}"


# 用户字段的缺省值。无论记录是由导入、扫描还是旧库迁移产生，视图都必须提供
# 这些字段：界面与调用方按字段直接读取（如 rec["note"]），缺失会直接报错。
_USER_DEFAULTS = {
    "display_name": "",
    "category": C.DEFAULT_CATEGORY,
    "tags": [],
    "note": "",
    "favorite": False,
    "startup": False,
}


def _with_user_defaults(record: dict) -> dict:
    for field, value in _USER_DEFAULTS.items():
        if record.get(field) is None:
            record[field] = list(value) if isinstance(value, list) else value
    return record


class LibraryDB:
    """总资料库 + 本机绑定 + 本机运行状态的读写视图。"""

    def __init__(self, root: str, use_cache: bool = True):
        self.root = root
        self.catalog = Catalog(root)
        self.path = str(self.catalog.path)
        self._use_cache = use_cache
        # 解析缓存由 json_cache 统一按文件签名处理；use_cache=False 用于必须
        # 直接读盘的受控流程（首次激活、切库）。
        if not use_cache:
            json_cache.invalidate()
        self.status = "MISSING"
        self.loaded_signature = None
        self._bindings: dict[str, str] = {}
        self._runtime: dict[str, dict] = {}
        # 最近一次 merge_scan 的统计（added/updated/missing/renamed…）与
        # 本次扫描的 rel -> plugin_id 映射。
        self.scan_stats: dict = {}
        self.assignments: dict[str, str] = {}
        self.data = _empty_view()
        self.load()

    # -- 读取 --------------------------------------------------------------
    def load(self) -> None:
        status = self.catalog.load()
        if status == "OK":
            self.status = "OK"
        elif status == "CORRUPT" or self.catalog.legacy_is_corrupt():
            # 资料库本身损坏，或旧库损坏：只读，绝不覆盖（保存会被拒绝）。
            self.status = "CORRUPT"
            self.data = _empty_view()
            self.loaded_signature = self.catalog.loaded_signature
            return
        else:
            self.status = "MISSING"
        self.loaded_signature = self.catalog.loaded_signature

        if status != "OK":
            self.data = _empty_view()
            return

        library_id = str(self.catalog.data.get("library_id") or "")
        self._bindings, self._runtime = self._load_local(library_id)

        plugins: dict[str, dict] = {}
        for plugin_id, portable in self.catalog.plugins.items():
            rel = self._bindings.get(plugin_id)
            # 只列出本机实际安装过（有绑定）的插件：总资料库里的其它条目在
            # 插件出现时会自动匹配上，不需要在列表里显示为“未安装”。
            if not rel:
                continue
            record = dict(portable)
            record["key"] = plugin_id
            record["plugin_id"] = plugin_id
            record["rel"] = rel
            # 目录名以本机实际绑定为准（各电脑目录名可能不同）。
            record["folder_name"] = os.path.basename(os.path.normpath(rel))
            runtime = self._runtime.get(plugin_id)
            if isinstance(runtime, dict):
                record.update(runtime)
            record.setdefault("missing", False)
            if not record.get("name"):
                record["name"] = record["folder_name"]
            plugins[plugin_id] = _with_user_defaults(record)

        self.data = {
            "schema": C.DB_SCHEMA,
            "library_id": library_id,
            "revision": int(self.catalog.data.get("revision", 0)),
            "categories": [dict(item) for item in self.catalog.categories],
            "plugins": plugins,
        }

    def _load_local(self, library_id: str) -> tuple[dict, dict]:
        state = self._state_store(library_id).load()
        raw_bindings = state.get("bindings")
        bindings = ({str(k): str(v) for k, v in raw_bindings.items()
                     if isinstance(v, str) and v}
                    if isinstance(raw_bindings, dict) else {})
        raw_runtime = state.get("plugins")
        runtime = ({str(k): dict(v) for k, v in raw_runtime.items() if isinstance(v, dict)}
                   if isinstance(raw_runtime, dict) else {})
        return bindings, runtime

    def _state_store(self, library_id: str) -> StateStore:
        return StateStore(library_id or "unknown", _environment())

    # -- 写入 --------------------------------------------------------------
    def save(self) -> None:
        if self.status == "CORRUPT":
            raise CorruptDatabaseError(f"拒绝覆盖损坏数据库: {self.path}")

        if self.catalog.status != "OK":
            # 显式写入即视为初始化（首次连接/导入前可能尚未调用 initialize）。
            report = self.catalog.initialize()
            if report.status == "CORRUPT":
                self.status = "CORRUPT"
                raise CorruptDatabaseError(f"拒绝覆盖损坏数据库: {self.path}")
        else:
            # 本进程内可能有多个句柄先后写入同一文件，同步软件也可能在外部替换
            # 文件。先重新读取最新内容，再把自己的记录合并上去，这样既不会误报
            # 冲突，也不会把对方新增的记录整体覆盖掉。
            self.catalog.refresh_if_stale()
            if self.catalog.status != "OK":
                self.status = "CORRUPT"
                raise CorruptDatabaseError(f"拒绝覆盖损坏数据库: {self.path}")

        catalog_plugins, bindings, runtime = self._split()
        self.catalog.data["plugins"] = catalog_plugins
        self.catalog.data["categories"] = [dict(item) for item in self.data["categories"]]
        self.catalog.save()

        library_id = str(self.catalog.data.get("library_id") or "")
        self._state_store(library_id).save(
            {"schema": 2, "bindings": bindings, "plugins": runtime})
        self._bindings, self._runtime = bindings, runtime
        self.status = "OK"
        self.loaded_signature = self.catalog.loaded_signature
        self.data["library_id"] = library_id
        self.data["revision"] = int(self.catalog.data.get("revision", 0))

    def _split(self) -> tuple[dict, dict, dict]:
        """把视图记录按归属拆成 总资料库 / 绑定 / 运行状态。

        以资料库既有条目为底，保证本机未安装的条目不会被这次写入抹掉。
        """
        catalog_plugins = {pid: dict(rec) for pid, rec in self.catalog.plugins.items()}
        bindings: dict[str, str] = {}
        runtime: dict[str, dict] = {}
        for plugin_id, record in self.data["plugins"].items():
            portable = {field: record[field] for field in PORTABLE_FIELDS if field in record}
            portable["plugin_id"] = plugin_id
            catalog_plugins[plugin_id] = portable
            rel = record.get("rel")
            if isinstance(rel, str) and rel:
                bindings[plugin_id] = rel
            fields = {field: record[field] for field in _V2_RUNTIME_FIELDS if field in record}
            if fields:
                runtime[plugin_id] = fields
        return catalog_plugins, bindings, runtime

    # -- 初始化与迁移 ------------------------------------------------------
    def prepare(self) -> str:
        """确保总资料库就绪，并把旧版 library.json 迁移进资料库。

        幂等；目录已由调用方确认是插件库（不是无关目录）时才能调用。返回
        ``OK`` / ``CORRUPT``。迁移完成后旧文件被归档为只读。
        """
        if self.status == "CORRUPT":
            return "CORRUPT"
        report = self.catalog.initialize()
        if report.status == "CORRUPT":
            self.status = "CORRUPT"
            return "CORRUPT"
        self.load()
        if self.catalog.legacy_path.exists():
            legacy = None
            try:
                legacy = self.catalog.read_legacy()
            except (OSError, ValueError):
                legacy = None
            if isinstance(legacy, dict):
                self.absorb_legacy(legacy)
                self.save()
            self.catalog.archive_legacy()
        return "OK"

    # -- 记录访问 ----------------------------------------------------------
    @property
    def plugins(self) -> dict:
        return self.data.setdefault("plugins", {})

    @property
    def bindings(self) -> dict:
        """本机绑定快照：plugin_id -> 库内相对路径。"""
        return dict(self._bindings)

    def get(self, key: str) -> dict | None:
        return self.plugins.get(key)

    def upsert(self, key: str, record: dict) -> dict:
        """按 plugin_id 写入一条记录（``key`` 必须是稳定 plugin_id）。"""
        if not key:
            raise ValueError("plugin key must be a non-empty plugin id")
        current = dict(self.plugins.get(key, {}))
        current.update(record)
        current["key"] = key
        current["plugin_id"] = key
        current.setdefault("created_at", now_iso())
        current["updated_at"] = now_iso()
        self.plugins[key] = current
        return current

    def merge_scan(self, entries: list[dict]) -> dict:
        """把本机扫描结果连接到总资料库，并写入本机绑定。"""
        if self.status == "CORRUPT":
            raise CorruptDatabaseError(f"拒绝写入损坏数据库: {self.path}")
        from .services.catalog_sync import link_scan, slug

        # 本机当前仍然存在、且已被既有绑定占用的目录名。用于区分“插件搬家”
        # 与“本机装了同一插件的第二个副本”——后者不能抢占原记录的别名。
        occupied = {
            slug(os.path.basename(os.path.normpath(rel)))
            for rel in self._bindings.values()
            if rel and os.path.isdir(os.path.join(self.root, rel.replace("/", os.sep)))
        }
        result = link_scan(self.catalog.plugins, self._bindings, entries,
                           occupied_folders=occupied)
        previous_bindings = set(self._bindings)
        for plugin_id in result.present:
            record = result.plugins[plugin_id]
            record["key"] = plugin_id
            record["rel"] = result.bindings.get(plugin_id, "")
            record["folder_name"] = os.path.basename(os.path.normpath(record["rel"]))
            runtime = self._runtime.get(plugin_id)
            if isinstance(runtime, dict):
                record.update(runtime)
            record.setdefault("missing", False)
            if not record.get("name"):
                record["name"] = record["folder_name"]
            _with_user_defaults(record)
        self.data["plugins"] = {pid: result.plugins[pid] for pid in result.present}
        # 未在本机扫描到的既有绑定需要保留其记录（启用意图与别名不丢）。
        for plugin_id, rel in result.bindings.items():
            if plugin_id in self.data["plugins"] or plugin_id not in self.catalog.plugins:
                continue
            record = dict(self.catalog.plugins[plugin_id])
            record["key"] = plugin_id
            record["plugin_id"] = plugin_id
            record["rel"] = rel
            record["folder_name"] = os.path.basename(os.path.normpath(rel))
            record["missing"] = True
            runtime = self._runtime.get(plugin_id)
            if isinstance(runtime, dict):
                record.update(runtime)
            if not record.get("name"):
                record["name"] = record["folder_name"]
            self.data["plugins"][plugin_id] = _with_user_defaults(record)
        self.catalog.data["plugins"] = result.plugins
        self._bindings = result.bindings
        self.assignments = dict(result.assignment)
        missing = sum(1 for rec in self.data["plugins"].values() if rec.get("missing"))
        self.scan_stats = {
            "added": result.added,
            "updated": result.matched,
            "unchanged": 0,
            "missing": missing,
            "renamed": result.renamed,
            "total": result.total,
            "new_bindings": len(set(result.bindings) - previous_bindings),
        }
        self.save()
        return self.plugins

    def remove(self, key: str) -> None:
        """从本机移除插件：解除绑定并清掉运行状态。

        总资料库条目**保留**（别名/分类/备注等共享数据属于所有电脑，也可能
        在重新安装后复用）；只有本机的安装记录被删除。
        """
        self.plugins.pop(key, None)
        self._bindings.pop(key, None)
        self._runtime.pop(key, None)

    def all(self) -> list[dict]:
        return list(self.plugins.values())

    # -- 分类（共享，存于总资料库） ----------------------------------------
    @property
    def categories(self) -> list[str]:
        """用户分类标签列表：扁平结构，不嵌套。

        插件自带的元数据分类保存在每条记录的 ``auto_category`` 中，不会自动
        灌进这里；需要时用「采用自带分类」单独取用。
        """
        cats = self.data.setdefault("categories", [])
        if not cats:
            cats.append(dict(catalog_store.DEFAULT_CATEGORY))
        if isinstance(cats[0], dict):
            if not any(c.get("id") == "uncategorized" for c in cats):
                cats.insert(0, dict(catalog_store.DEFAULT_CATEGORY))
            return [str(c.get("name") or C.DEFAULT_CATEGORY) for c in cats]
        if C.DEFAULT_CATEGORY not in cats:
            cats.insert(0, C.DEFAULT_CATEGORY)
        return cats

    def _category_records(self) -> list:
        cats = self.data.setdefault("categories", [])
        if not cats or not isinstance(cats[0], dict):
            self.data["categories"] = [dict(catalog_store.DEFAULT_CATEGORY)] + [
                {"id": name, "name": name, "order": i + 1}
                for i, name in enumerate(c for c in cats if c != C.DEFAULT_CATEGORY)
            ]
        return self.data["categories"]

    def ensure_category(self, name: str) -> None:
        name = (name or "").strip()
        if name and name not in self.categories:
            cats = self._category_records()
            cats.append({"id": name, "name": name, "order": len(cats)})

    def add_category(self, name: str) -> bool:
        name = (name or "").strip()
        if not name or name in self.categories:
            return False
        cats = self._category_records()
        cats.append({"id": name, "name": name, "order": len(cats)})
        return True

    def rename_category(self, old: str, new: str) -> None:
        new = (new or "").strip()
        if not new:
            return
        for item in self._category_records():
            if item.get("name") == old:
                item["name"] = new
                item["id"] = new
        for rec in self.plugins.values():
            if rec.get("category") == old:
                rec["category"] = new

    def delete_category(self, name: str) -> None:
        """删除分类，其下插件回到「未分类」（不会删除插件）。"""
        if name == C.DEFAULT_CATEGORY:
            return
        self.data["categories"] = [
            item for item in self._category_records() if item.get("name") != name
        ]
        for rec in self.plugins.values():
            if rec.get("category") == name:
                rec["category"] = C.DEFAULT_CATEGORY

    def reset_auto_categories(self) -> dict:
        """把「仍是插件自带分类、你未改动过」的归类收回为未分类。"""
        moved = 0
        for rec in self.plugins.values():
            auto = (rec.get("auto_category") or "").strip()
            cur = (rec.get("category") or "").strip()
            if cur and cur != C.DEFAULT_CATEGORY and cur == auto:
                rec["category"] = C.DEFAULT_CATEGORY
                moved += 1
        remaining = {
            (rec.get("category") or "").strip()
            for rec in self.plugins.values()
            if (rec.get("category") or "").strip()
            and (rec.get("category") or "").strip() != C.DEFAULT_CATEGORY
        }
        self.data["categories"] = [dict(catalog_store.DEFAULT_CATEGORY)] + [
            {"id": name, "name": name, "order": i + 1}
            for i, name in enumerate(sorted(remaining))
        ]
        return {"moved": moved, "categories_left": len(self.data["categories"])}

    def category_counts(self) -> dict:
        counts = {name: 0 for name in self.categories}
        for rec in self.plugins.values():
            cat = rec.get("category") or C.DEFAULT_CATEGORY
            counts[cat] = counts.get(cat, 0) + 1
        return counts

    # -- 统计 --------------------------------------------------------------
    def startup_stats(self) -> dict:
        total = len(self.plugins)
        marked = sum(1 for r in self.plugins.values() if r.get("startup"))
        return {"total": total, "startup": marked, "not_startup": total - marked}

    def catalog_stats(self) -> dict:
        """总资料库概览：总条目数与本机已安装数。"""
        return {
            "catalog": len(self.catalog.plugins),
            "installed": len(self.plugins),
        }

    # -- 旧数据迁移 --------------------------------------------------------
    def absorb_legacy(self, legacy: dict) -> dict:
        """把旧版 schema 2 的 library.json 数据吸收进总资料库。

        旧记录以库内相对路径为键。这里把它们当作“上一次扫描的结果”，走同一套
        身份匹配：能匹配到总资料库既有条目的就合并，匹配不到才新建，因此不会
        产生重复条目。别名、分类、标签、备注、收藏保留到总资料库；**自启**
        属于本机行为，写入本机运行状态。

        返回迁移统计。
        """
        from .services.catalog_sync import link_scan, migrate_legacy

        plugins, bindings = migrate_legacy(legacy.get("plugins") or {})
        entries = []
        for plugin_id, record in plugins.items():
            rel = bindings.get(plugin_id)
            if not rel:
                continue
            entry = dict(record)
            entry["rel"] = rel
            entry["folder_name"] = rel.split("/")[-1]
            entry["name"] = record.get("name") or entry["folder_name"]
            entries.append(entry)

        if entries:
            result = link_scan(self.catalog.plugins, self._bindings, entries)
            imported = 0
            for rel, plugin_id in result.assignment.items():
                old = plugins.get(plugin_id, {})
                record = result.plugins[plugin_id]
                # 旧数据里的用户字段只在资料库尚无该值时补入（资料库优先）。
                for field in ("display_name", "category", "category_id", "tags",
                              "note", "favorite", "created_at", "updated_at"):
                    if old.get(field) not in (None, "", []) and record.get(field) in (None, "", []):
                        record[field] = old[field]
                imported += 1
            self.catalog.data["plugins"] = result.plugins
            self._bindings = result.bindings
            # 组成视图记录并带上本机运行状态（含旧库的「自启」标记）。
            for plugin_id in result.present:
                rel = result.bindings.get(plugin_id, "")
                record = dict(result.plugins[plugin_id])
                record["key"] = plugin_id
                record["plugin_id"] = plugin_id
                record["rel"] = rel
                record["folder_name"] = os.path.basename(os.path.normpath(rel))
                record.update(self._runtime.get(plugin_id, {}))
                old = plugins.get(plugin_id, {})
                if "startup" in old:
                    record["startup"] = bool(old["startup"])
                self.data["plugins"][plugin_id] = record
        else:
            imported = 0

        legacy_cats = legacy.get("categories")
        if isinstance(legacy_cats, list) and legacy_cats:
            merged = {item.get("id"): dict(item) for item in self._category_records()}
            for item in legacy_cats:
                if isinstance(item, dict) and item.get("id") not in merged:
                    merged[item["id"]] = {
                        "id": str(item.get("id")),
                        "name": str(item.get("name") or item.get("id")),
                        "order": int(item.get("order", len(merged))),
                    }
            self.data["categories"] = sorted(merged.values(), key=lambda c: c.get("order", 0))
        return {"imported": imported, "bindings": len(self._bindings)}
