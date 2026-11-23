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
import json
import tempfile
from copy import deepcopy

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
    # 来源与合规检查结果：origin_path / source_path 是本机绝对路径，只存本机。
    "origin", "origin_path", "source_path", "imported_at", "issues",
    "version", "blender_min", "blender_max", "archive_source", "load_environment",
    "latest_repo", "update_source_required",
    "startup_override",
})
_INSTALL_FIELDS = ("version", "blender_min", "blender_max", "metadata_fingerprint")
_LOAD_RESULT_FIELDS = ("load_state", "load_error", "residue", "restore_error", "load_environment")
_ABSENT = object()

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


def compatibility_environment() -> str:
    import sys
    blender = sys.modules.get("bpy")
    version = getattr(getattr(blender, "app", None), "version", ())
    return f"{_environment()}-blender-{'.'.join(map(str, version))}"


def clear_load_result(record: dict) -> None:
    for field in _LOAD_RESULT_FIELDS:
        record.pop(field, None)


# 用户字段的缺省值。无论记录是由导入、扫描还是旧库迁移产生，视图都必须提供
# 这些字段：界面与调用方按字段直接读取（如 rec["note"]），缺失会直接报错。
_USER_DEFAULTS = {
    "display_name": "",
    "category": C.DEFAULT_CATEGORY,
    "groups": [],
    "tags": [],
    "note": "",
    "favorite": False,
    "startup": False,
}


def _normalize_group_list(raw, fallback_category: str = "") -> list[str]:
    """Normalize a plugin's group membership list (excluding the uncategorized sentinel)."""
    groups: list[str] = []
    seen: set[str] = set()
    if isinstance(raw, (list, tuple)):
        for item in raw:
            name = str(item or "").strip()
            if name and name != C.DEFAULT_CATEGORY and name not in seen:
                seen.add(name)
                groups.append(name)
    if not groups:
        cat = str(fallback_category or "").strip()
        if cat and cat != C.DEFAULT_CATEGORY:
            groups.append(cat)
    return groups


def _with_user_defaults(record: dict) -> dict:
    has_groups = isinstance(record.get("groups"), list)
    for field, value in _USER_DEFAULTS.items():
        if record.get(field) is None:
            record[field] = list(value) if isinstance(value, list) else value
    cat = str(record.get("category") or "").strip()
    if has_groups:
        record["groups"] = _normalize_group_list(record.get("groups"))
        record["category"] = record["groups"][0] if record["groups"] else C.DEFAULT_CATEGORY
    else:
        record["groups"] = _normalize_group_list([], fallback_category=cat)
        record["category"] = record["groups"][0] if record["groups"] else C.DEFAULT_CATEGORY
    return record


def _merge_changes(base: dict, edited: dict, latest: dict, conflicts=None, path=()) -> dict:
    """Apply only this handle's changes to the latest values."""
    result = dict(latest)
    for key in dict.fromkeys((*base, *edited)):
        old = base.get(key, _ABSENT)
        new = edited.get(key, _ABSENT)
        if new == old:
            continue
        current = latest.get(key, _ABSENT)
        field_path = (*path, key)
        if isinstance(new, dict) and isinstance(current, dict) and (old is _ABSENT or isinstance(old, dict)):
            result[key] = _merge_changes(old if isinstance(old, dict) else {}, new, current,
                                         conflicts, field_path)
        elif (conflicts is not None and current != old and current != new
              and key not in {"updated_at", "created_at", "order"}):
            conflicts.append({"field": ".".join(map(str, field_path)),
                              "current": None if current is _ABSENT else current,
                              "proposed": None if new is _ABSENT else new})
        elif new is _ABSENT:
            result.pop(key, None)
        else:
            result[key] = new
    return result


class LibraryDB:
    """总资料库 + 本机绑定 + 本机运行状态的读写视图。"""

    def __init__(self, root: str, use_cache: bool = True, *, load_environment: str | None = None):
        self.root = root
        self.catalog = Catalog(root)
        self.path = str(self.catalog.path)
        self._use_cache = use_cache
        self._load_environment = load_environment if load_environment is not None else compatibility_environment()
        # 解析缓存由 json_cache 统一按文件签名处理；use_cache=False 用于必须
        # 直接读盘的受控流程（首次激活、切库）。
        if not use_cache:
            json_cache.invalidate()
        self.status = "MISSING"
        self.migration_report = None
        self.loaded_signature = None
        self._bindings: dict[str, str] = {}
        self._runtime: dict[str, dict] = {}
        self._group_state: dict[str, dict] = {}
        # 最近一次 merge_scan 的统计（added/updated/missing/renamed…）与
        # 本次扫描的 rel -> plugin_id 映射。
        self.scan_stats: dict = {}
        self.assignments: dict[str, str] = {}
        self.data = _empty_view()
        self.load()

    # -- 读取 --------------------------------------------------------------
    def load(self) -> None:
        status = self.catalog.load()
        self._catalog_baseline = deepcopy(self.catalog.plugins) if status == "OK" else {}
        legacy_status = self.catalog.legacy_status() if status != "OK" else "MISSING"
        self._bindings, self._runtime, self._group_state = {}, {}, {}
        if status == "OK":
            self.status = "OK"
        elif status in {"CORRUPT", "UNSUPPORTED"} or legacy_status in {"CORRUPT", "UNSUPPORTED"}:
            # 资料库本身损坏，或旧库损坏：只读，绝不覆盖（保存会被拒绝）。
            self.status = status if status != "MISSING" else legacy_status
            self.data = _empty_view()
            self.loaded_signature = self.catalog.loaded_signature
            self._baseline = self._split()
            return
        else:
            self.status = "MISSING"
        self.loaded_signature = self.catalog.loaded_signature

        if status != "OK":
            self.data = _empty_view()
            self._baseline = self._split()
            return

        library_id = str(self.catalog.data.get("library_id") or "")
        self._bindings, self._runtime, self._group_state = self._load_local(library_id)

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
            if record.get("load_environment") and record["load_environment"] != self._load_environment:
                clear_load_result(record)
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
        _split_shared, split_bindings, split_runtime, split_groups = self._split()
        raw_shared = {
            "plugins": deepcopy(self.catalog.plugins),
            "categories": {item["id"]: dict(item) for item in self.catalog.categories},
        }
        self._baseline = (raw_shared, deepcopy(split_bindings), deepcopy(split_runtime), deepcopy(split_groups))

    def _load_local(self, library_id: str) -> tuple[dict, dict, dict]:
        state = self._state_store(library_id).load()
        raw_bindings = state.get("bindings")
        bindings = ({str(k): str(v) for k, v in raw_bindings.items()
                     if isinstance(v, str) and v}
                    if isinstance(raw_bindings, dict) else {})
        raw_runtime = state.get("plugins")
        runtime = ({str(k): dict(v) for k, v in raw_runtime.items() if isinstance(v, dict)}
                   if isinstance(raw_runtime, dict) else {})
        raw_groups = state.get("groups")
        groups = {}
        if isinstance(raw_groups, dict):
            for name, cfg in raw_groups.items():
                if isinstance(name, str) and name and isinstance(cfg, dict):
                    groups[name] = {
                        "active": bool(cfg.get("active", False)),
                        "startup": bool(cfg.get("startup", False)),
                    }
        return bindings, runtime, groups

    def _state_store(self, library_id: str) -> StateStore:
        return StateStore(library_id or "unknown", _environment())

    # -- 写入 --------------------------------------------------------------
    def save(self) -> None:
        if self.status in {"CORRUPT", "UNSUPPORTED"}:
            raise CorruptDatabaseError(f"插件资料库不可写 ({self.status}): {self.path}")

        previous_id = self.catalog.data.get("library_id")
        if self.catalog.status != "OK":
            # 显式写入即视为初始化（首次连接/导入前可能尚未调用 initialize）。
            report = self.catalog.initialize()
            if report.status not in {"READY", "CREATED"}:
                self.status = report.status
                raise CorruptDatabaseError(f"插件资料库不可写 ({self.status}): {self.path}")
        else:
            # 本进程内可能有多个句柄先后写入同一文件，同步软件也可能在外部替换
            # 文件。先重新读取最新内容，再把自己的记录合并上去，这样既不会误报
            # 冲突，也不会把对方新增的记录整体覆盖掉。
            self.catalog.refresh_if_stale()
            if self.catalog.status != "OK":
                self.status = self.catalog.status
                raise CorruptDatabaseError(f"插件资料库不可写 ({self.status}): {self.path}")

        library_id = str(self.catalog.data["library_id"])
        if previous_id and previous_id != library_id:
            raise catalog_store.CatalogConflictError("库标识已变更，请重新打开插件库后再保存")
        current = self._split()
        old_shared, old_bindings, old_runtime, old_groups = self._baseline
        shared, bindings, runtime, groups = current
        existing = {"plugins": self.catalog.plugins,
                    "categories": {item["id"]: item for item in self.catalog.categories}}
        conflicts = []
        # 本机解绑只影响本机状态。共享记录仅在调用方实际修改字段时写回。
        merged = _merge_changes(old_shared, shared, existing, conflicts)
        if conflicts:
            path = self._state_store(library_id).path.parent
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path,
                                             prefix="catalog-conflict-", suffix=".json",
                                             delete=False) as proposal:
                json.dump({"library": self.path, "conflicts": conflicts}, proposal,
                          ensure_ascii=False, indent=2)
            raise catalog_store.CatalogConflictError(
                f"共享资料已被修改，请刷新。两份修改已记录在 {proposal.name}")
        if merged != existing:
            self.catalog.data["plugins"] = merged["plugins"]
            self.catalog.data["categories"] = list(merged["categories"].values())
            try:
                self.catalog.save()
            except catalog_store.CatalogConflictError:
                self.catalog.load()
                raise

        if bindings != old_bindings or runtime != old_runtime or groups != old_groups:
            latest_bindings, latest_runtime, latest_groups = self._load_local(library_id)
            self._bindings = _merge_changes(old_bindings, bindings, latest_bindings)
            self._runtime = _merge_changes(old_runtime, runtime, latest_runtime)
            self._group_state = _merge_changes(old_groups, groups, latest_groups)
            self._state_store(library_id).save({
                "schema": 2,
                "bindings": self._bindings,
                "plugins": self._runtime,
                "groups": self._group_state,
            })
        self._baseline = deepcopy(current)
        self._catalog_baseline = deepcopy(shared["plugins"])
        self.status = "OK"
        self.loaded_signature = self.catalog.loaded_signature
        self.data["library_id"] = library_id
        self.data["revision"] = int(self.catalog.data.get("revision", 0))

    def _split(self) -> tuple[dict, dict, dict, dict]:
        """把视图记录按归属拆成 总资料库 / 绑定 / 运行状态 / 分组状态。

        以资料库既有条目为底，保证本机未安装的条目不会被这次写入抹掉。
        """
        catalog_plugins = dict(self._catalog_baseline)
        bindings: dict[str, str] = {}
        runtime: dict[str, dict] = {}
        old_plugins = (self._baseline[0].get("plugins", {})
                       if getattr(self, "_baseline", None) else {})
        for plugin_id, record in self.data["plugins"].items():
            # Keep legacy `category` and multi-group `groups` synchronized
            # regardless of which one the caller mutated.
            old_rec = old_plugins.get(plugin_id) or {}
            old_cat = str(old_rec.get("category") or C.DEFAULT_CATEGORY)
            old_groups = _normalize_group_list(old_rec.get("groups"), fallback_category=old_cat)
            cur_cat = str(record.get("category") or C.DEFAULT_CATEGORY).strip() or C.DEFAULT_CATEGORY
            cur_groups = _normalize_group_list(record.get("groups"))
            if cur_cat != old_cat and cur_groups == old_groups:
                cur_groups = [cur_cat] if cur_cat != C.DEFAULT_CATEGORY else []
                record["groups"] = cur_groups
            else:
                record["groups"] = cur_groups
                record["category"] = cur_groups[0] if cur_groups else C.DEFAULT_CATEGORY

            portable = {field: record[field] for field in PORTABLE_FIELDS if field in record}
            # Avoid churning catalog.json with synthetic empty defaults that were absent on disk.
            for field, default_val in _USER_DEFAULTS.items():
                if field in portable and field not in old_rec and portable[field] == default_val:
                    portable.pop(field, None)
            portable["plugin_id"] = plugin_id
            catalog_plugins[plugin_id] = portable
            rel = record.get("rel")
            if isinstance(rel, str) and rel:
                bindings[plugin_id] = rel
            fields = {field: record[field] for field in _V2_RUNTIME_FIELDS if field in record}
            if fields:
                runtime[plugin_id] = fields
        categories = {item["id"]: dict(item) for item in self._category_records()}
        groups = {name: {"active": bool(cfg.get("active", False)),
                         "startup": bool(cfg.get("startup", False))}
                  for name, cfg in self._group_state.items()
                  if isinstance(name, str) and name and isinstance(cfg, dict)}
        return {"plugins": catalog_plugins, "categories": categories}, bindings, runtime, groups

    # -- 初始化与迁移 ------------------------------------------------------
    def prepare(self) -> str:
        """确保总资料库就绪，并把旧版 library.json 迁移进资料库。

        幂等；目录已由调用方确认是插件库（不是无关目录）时才能调用。返回
        ``OK`` / ``CORRUPT``。迁移完成后旧文件被归档为只读。
        """
        if self.status in {"CORRUPT", "UNSUPPORTED"}:
            return self.status
        report = self.catalog.initialize()
        if report.status not in {"READY", "CREATED"}:
            self.status = report.status
            return self.status
        self.load()
        if self.catalog.legacy_path.exists():
            legacy = self.catalog.read_legacy()
            if legacy is not None:
                self.migration_report = self.absorb_legacy(legacy)
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

    def merge_scan(self, entries: list[dict], *, complete: bool = True, persist: bool = True) -> dict:
        """把本机扫描结果连接到总资料库，并写入本机绑定。"""
        if self.status in {"CORRUPT", "UNSUPPORTED"}:
            raise CorruptDatabaseError(f"插件资料库不可写 ({self.status}): {self.path}")
        from .services.catalog_sync import link_scan, slug

        # 本机当前仍然存在、且已被既有绑定占用的目录名。用于区分“插件搬家”
        # 与“本机装了同一插件的第二个副本”——后者不能抢占原记录的别名。
        occupied = {
            slug(os.path.basename(os.path.normpath(rel)))
            for rel in self._bindings.values()
            if rel and not rel.startswith(C.BUILTIN_REL_PREFIX)
            and os.path.isdir(os.path.join(self.root, rel.replace("/", os.sep)))
        }
        result = link_scan(self.catalog.plugins, self._bindings, entries,
                           occupied_folders=occupied)
        previous_bindings = set(self._bindings)
        scanned = {entry["rel"]: entry for entry in entries}
        for plugin_id in result.present:
            record = result.plugins[plugin_id]
            record["key"] = plugin_id
            record["rel"] = result.bindings.get(plugin_id, "")
            record["folder_name"] = os.path.basename(os.path.normpath(record["rel"]))
            runtime = self._runtime.get(plugin_id)
            if isinstance(runtime, dict):
                record.update(runtime)
            entry = scanned[record["rel"]]
            if any(field in entry and entry[field] != record.get(field) for field in _INSTALL_FIELDS):
                clear_load_result(record)
            if record.get("load_environment") and record["load_environment"] != self._load_environment:
                clear_load_result(record)
            for field in _INSTALL_FIELDS:
                if field in entry:
                    record[field] = entry[field]
            record["missing"] = False
            if not record.get("name"):
                record["name"] = record["folder_name"]
            _with_user_defaults(record)
        previous_plugins = dict(self.data.get("plugins") or {})
        self.data["plugins"] = {pid: result.plugins[pid] for pid in result.present}
        # 未在本机扫描到的既有绑定需要保留其记录（启用意图与别名不丢）。
        for plugin_id, rel in result.bindings.items():
            if plugin_id in self.data["plugins"] or plugin_id not in self.catalog.plugins:
                continue
            prev = previous_plugins.get(plugin_id)
            if isinstance(prev, dict) and not complete:
                record = dict(prev)
                record["rel"] = rel
                self.data["plugins"][plugin_id] = _with_user_defaults(record)
                continue
            record = dict(self.catalog.plugins[plugin_id])
            record["key"] = plugin_id
            record["plugin_id"] = plugin_id
            record["rel"] = rel
            record["folder_name"] = os.path.basename(os.path.normpath(rel))
            runtime = self._runtime.get(plugin_id)
            if isinstance(runtime, dict):
                record.update(runtime)
            if complete and record.get("kind") != C.KIND_BUILTIN:
                record["missing"] = True
            if not record.get("name"):
                record["name"] = record["folder_name"]
            self.data["plugins"][plugin_id] = _with_user_defaults(record)
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
        if persist:
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

    def relink_options(self, key: str) -> dict:
        source = self.get(key)
        if not source or source.get("kind") != "addon" or source.get("missing"):
            return {}
        return {pid: record for pid, record in self.catalog.plugins.items()
                if pid != key and record.get("kind") == "addon"
                and (pid not in self.bindings or (self.get(pid) or {}).get("missing"))}

    def relink_addon(self, key: str, target: str) -> dict:
        """Apply a user-confirmed catalog identity to the current installation."""
        portable = self.relink_options(key).get(target)
        if not portable:
            raise ValueError("该资料不能关联，或已绑定到本机另一项安装")
        record = dict(self.get(key))
        for field in ("display_name", "category", "category_id", "tags", "note", "favorite", "source_url"):
            if field in portable:
                record[field] = portable[field]
        for field in ("names", "folders"):
            record[field] = sorted(set(record.get(field, ())) | set(portable.get(field, ())))
        self.remove(key)
        rebound = self.upsert(target, record)
        self.save()
        return rebound

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
        if not new or old == C.DEFAULT_CATEGORY:
            return
        for item in self._category_records():
            if item.get("name") == old:
                item["name"] = new
                item["id"] = new
        for rec in self.plugins.values():
            groups = _normalize_group_list(rec.get("groups"), fallback_category=rec.get("category", ""))
            if old in groups:
                updated = []
                for g in groups:
                    target = new if g == old else g
                    if target not in updated:
                        updated.append(target)
                rec["groups"] = updated
                rec["category"] = updated[0] if updated else C.DEFAULT_CATEGORY
            elif rec.get("category") == old:
                rec["category"] = new
                rec["groups"] = [new]
        if old in self._group_state:
            state = self._group_state.pop(old)
            self._group_state.setdefault(new, state)

    def delete_category(self, name: str) -> None:
        """删除分组/分类，其下插件移出该组（不会删除插件）。"""
        if name == C.DEFAULT_CATEGORY:
            return
        self.data["categories"] = [
            item for item in self._category_records() if item.get("name") != name
        ]
        for rec in self.plugins.values():
            groups = _normalize_group_list(rec.get("groups"), fallback_category=rec.get("category", ""))
            if name in groups:
                groups = [g for g in groups if g != name]
                rec["groups"] = groups
                rec["category"] = groups[0] if groups else C.DEFAULT_CATEGORY
            elif rec.get("category") == name:
                rec["category"] = C.DEFAULT_CATEGORY
                rec["groups"] = []
        self._group_state.pop(name, None)

    def reset_auto_categories(self) -> dict:
        """把「仍是插件自带分类、你未改动过」的归类收回为未分类。"""
        moved = 0
        for rec in self.plugins.values():
            auto = (rec.get("auto_category") or "").strip()
            groups = _normalize_group_list(rec.get("groups"), fallback_category=rec.get("category", ""))
            if len(groups) == 1 and groups[0] == auto and auto != C.DEFAULT_CATEGORY:
                rec["groups"] = []
                rec["category"] = C.DEFAULT_CATEGORY
                moved += 1
        remaining = set()
        for rec in self.plugins.values():
            for g in _normalize_group_list(rec.get("groups"), fallback_category=rec.get("category", "")):
                if g and g != C.DEFAULT_CATEGORY:
                    remaining.add(g)
        existing_meta = {item.get("name"): item for item in self._category_records()}
        rebuilt = [dict(catalog_store.DEFAULT_CATEGORY)]
        for i, name in enumerate(sorted(remaining)):
            item = dict(existing_meta.get(name) or {"id": name, "name": name})
            item["id"] = name
            item["name"] = name
            item["order"] = i + 1
            rebuilt.append(item)
        self.data["categories"] = rebuilt
        self._group_state = {k: v for k, v in self._group_state.items() if k in remaining}
        return {"moved": moved, "categories_left": len(self.data["categories"])}

    def category_counts(self) -> dict:
        counts = {name: 0 for name in self.categories}
        for key in self.plugins:
            groups = self.plugin_groups(key)
            if not groups:
                counts[C.DEFAULT_CATEGORY] = counts.get(C.DEFAULT_CATEGORY, 0) + 1
            else:
                for g in groups:
                    counts[g] = counts.get(g, 0) + 1
        return counts

    # -- 分组（多对多归属 + 本机启停/自启状态） ----------------------------
    def group_records(self) -> list[dict]:
        """Return user groups (excluding uncategorized) merged with local state."""
        out: list[dict] = []
        for idx, item in enumerate(self._category_records()):
            name = str(item.get("name") or "").strip()
            if not name or name == C.DEFAULT_CATEGORY or item.get("id") == "uncategorized":
                continue
            state = self._group_state.get(name) or {}
            out.append({
                "id": str(item.get("id") or name),
                "name": name,
                "order": int(item.get("order", idx)),
                "pinned": bool(item.get("pinned", False)),
                "description": str(item.get("description") or ""),
                "active": bool(state.get("active", False)),
                "startup": bool(state.get("startup", False)),
            })
        return out

    def group_names(self) -> list[str]:
        return [g["name"] for g in self.group_records()]

    def get_group_state(self, name: str) -> dict:
        state = self._group_state.get(name) or {}
        return {
            "active": bool(state.get("active", False)),
            "startup": bool(state.get("startup", False)),
        }

    def set_group_state(self, name: str, *, active: bool | None = None,
                        startup: bool | None = None) -> dict:
        name = (name or "").strip()
        if not name or name == C.DEFAULT_CATEGORY:
            return {"active": False, "startup": False}
        self.ensure_category(name)
        cur = self.get_group_state(name)
        if active is not None:
            cur["active"] = bool(active)
        if startup is not None:
            cur["startup"] = bool(startup)
        self._group_state[name] = cur
        return dict(cur)

    def set_group_pinned(self, name: str, pinned: bool) -> None:
        name = (name or "").strip()
        if not name or name == C.DEFAULT_CATEGORY:
            return
        self.ensure_category(name)
        for item in self._category_records():
            if item.get("name") == name:
                item["pinned"] = bool(pinned)
                break

    def plugin_groups(self, key: str) -> list[str]:
        rec = self.get(key)
        if not rec:
            return []
        return _normalize_group_list(rec.get("groups"), fallback_category=rec.get("category", ""))

    def set_plugin_groups(self, key: str, group_names: list[str]) -> list[str]:
        rec = self.get(key)
        if not rec:
            return []
        cleaned = _normalize_group_list(group_names)
        for name in cleaned:
            self.ensure_category(name)
        rec["groups"] = cleaned
        rec["category"] = cleaned[0] if cleaned else C.DEFAULT_CATEGORY
        return list(cleaned)

    def add_plugin_to_group(self, key: str, group_name: str) -> list[str]:
        name = (group_name or "").strip()
        groups = self.plugin_groups(key)
        if name and name != C.DEFAULT_CATEGORY and name not in groups:
            groups.append(name)
        return self.set_plugin_groups(key, groups)

    def remove_plugin_from_group(self, key: str, group_name: str) -> list[str]:
        name = (group_name or "").strip()
        groups = [g for g in self.plugin_groups(key) if g != name]
        return self.set_plugin_groups(key, groups)

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
            old_by_path = {rel: plugins[key] for key, rel in bindings.items()}
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
                old = old_by_path[rel]
                for field in ("display_name", "category", "category_id", "tags", "note",
                              "favorite", "created_at", "updated_at", "startup", *_INSTALL_FIELDS):
                    if old.get(field) not in (None, "", []) and record.get(field) in (None, "", []):
                        record[field] = old[field]
                self.data["plugins"][plugin_id] = _with_user_defaults(record)
                imported += 1
        else:
            imported = 0

        legacy_cats = legacy.get("categories")
        if isinstance(legacy_cats, list) and legacy_cats:
            merged = {item.get("id"): dict(item) for item in self._category_records()}
            for item in legacy_cats:
                if isinstance(item, str) and item and item != C.DEFAULT_CATEGORY:
                    item = {"id": item, "name": item}
                if isinstance(item, dict) and item.get("id") not in merged:
                    merged[item["id"]] = {
                        "id": str(item.get("id")),
                        "name": str(item.get("name") or item.get("id")),
                        "order": int(item.get("order", len(merged))),
                    }
            self.data["categories"] = sorted(merged.values(), key=lambda c: c.get("order", 0))
            names = {item["id"]: item["name"] for item in self.data["categories"]}
            for plugin_id in result.present if entries else ():
                record = self.plugins[plugin_id]
                if record.get("category") == C.DEFAULT_CATEGORY and record.get("category_id") in names:
                    record["category"] = names[record["category_id"]]
        return {"imported": imported, "bindings": len(self._bindings)}
