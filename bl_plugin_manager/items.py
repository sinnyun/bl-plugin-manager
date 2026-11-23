"""把库数据同步到偏好设置里的列表项，供 UIList 与面板显示。"""

from __future__ import annotations

import os
import time

from . import constants as C, native_names, scan
from .db import LibraryDB
from .services import groups as group_service

# 轻量节流：避免 N 面板高频重绘时反复读取库文件
_LAST = {"t": 0.0, "sig": None}
_local_state = ("", "")
_database_status = ("", "MISSING")


def _signature(prefs):
    """界面刷新签名：库路径、筛选条件，以及总插件数据库与本机状态的文件签名。"""
    db_sigs = []
    if getattr(prefs, "library_path", ""):
        paths = [os.path.join(prefs.library_path, C.DIR_META, name)
                 for name in (C.DB_FILENAME, C.LEGACY_DB_FILENAME)]
        if _local_state[0] == prefs.library_path and _local_state[1]:
            paths.append(_local_state[1])
        for path in paths:
            try:
                st = os.stat(path)
                db_sigs.append((st.st_dev, st.st_ino, st.st_ctime_ns, st.st_mtime_ns, st.st_size))
            except OSError:
                db_sigs.append(None)
    return (
        prefs.library_path,
        prefs.active_category,
        getattr(prefs, "kind_filter", "all"),
        prefs.only_favorites,
        prefs.only_updates,
        prefs.only_enabled,
        prefs.only_incompatible,
        prefs.search,
        tuple(db_sigs),
    )


def maybe_rebuild(prefs, force: bool = False) -> None:
    """在 draw 中调用：只有筛选条件或数据库文件签名变化才重建。"""
    if getattr(prefs, "library_job_running", False):
        return
    sig = _signature(prefs)
    now = time.time()
    if not force and sig == _LAST["sig"]:
        return
    _LAST["sig"] = sig
    _LAST["t"] = now
    rebuild_items(prefs)


def compatibility_status(compat: str, load_state: str) -> str:
    if load_state == "failed":
        return "load_failed"
    if load_state == "ok":
        return "load_ok"
    if compat in ("too_new", "too_old"):
        return "out_of_range"
    return "declared" if compat == "ok" else "unknown"


def _filter(rec: dict, prefs) -> bool:
    kind_filter = getattr(prefs, "kind_filter", "all") or "all"
    if kind_filter != "all" and (rec.get("kind") or "") != kind_filter:
        return False
    if prefs.only_favorites and not rec.get("favorite"):
        return False
    if prefs.only_updates and not rec.get("update_available"):
        return False
    if prefs.only_enabled and not rec.get("enabled"):
        return False
    if getattr(prefs, "only_incompatible", False):
        comp = scan.blender_compat(rec.get("blender_min", ""),
                                   rec.get("blender_max", ""))[0]
        if compatibility_status(comp, rec.get("load_state", "")) not in {"load_failed", "out_of_range"}:
            return False
    needle = (prefs.search or "").strip().lower()
    if needle:
        hay = " ".join(
            str(rec.get(f) or "")
            for f in ("name", "display_name", "folder_name", "description", "author", "note", "tags", "groups")
        ).lower()
        if needle not in hay:
            return False
    return True


def rebuild_items(prefs) -> list[str]:
    """依据当前过滤条件重建 plugin_items 与 group_items，返回可见的 key 顺序。"""
    global _local_state, _database_status
    previous_selection = prefs.selected_key
    prefs.plugin_items.clear()
    if hasattr(prefs, "group_items"):
        prefs.group_items.clear()
    if not prefs.library_path:
        prefs.active_index = 0
        prefs.selected_key = ""
        return []

    db = LibraryDB(prefs.library_path)
    _database_status = (prefs.library_path, db.status)
    library_id = db.data.get("library_id")
    _local_state = (prefs.library_path, str(db._state_store(library_id).path) if library_id else "")
    active_cat = prefs.active_category or "全部"

    keys = []
    for key, rec in db.plugins.items():
        groups = db.plugin_groups(key)
        if active_cat == C.DEFAULT_CATEGORY:
            if groups:
                continue
        elif active_cat not in ("", "全部"):
            if active_cat not in groups:
                continue
        if not _filter(rec, prefs):
            continue
        keys.append(key)

    keys.sort(
        key=lambda k: (
            0 if db.plugins[k].get("kind") != C.KIND_BUILTIN else 1,
            (db.plugins[k].get("display_name") or db.plugins[k].get("name") or k).lower(),
        )
    )

    SELECTED_KEYS.intersection_update(set(db.plugins.keys()))

    for key in keys:
        rec = db.plugins[key]
        item = prefs.plugin_items.add()
        item.key = key
        item.name = rec.get("name") or rec.get("folder_name") or key
        item.folder_name = rec.get("folder_name") or ""
        item.display_name = rec.get("display_name") or ""
        item.kind = rec.get("kind") or ""
        item.version = rec.get("version") or ""
        item.latest_version = rec.get("latest_version") or ""
        item.blender_min = rec.get("blender_min") or ""
        item.blender_max = rec.get("blender_max") or ""
        item.compat, item.compat_detail = scan.blender_compat(
            item.blender_min, item.blender_max)
        item.compat_text = scan.compat_label(item.blender_min, item.blender_max)
        item.max_version_text = scan.max_version_label(item.blender_max)
        item.load_state = rec.get("load_state") or ""
        item.load_error = rec.get("load_error") or ""
        item.residue = int(rec.get("residue") or 0)
        state = compatibility_status(item.compat, item.load_state)
        if state in {"load_failed", "out_of_range"}:
            item.supported = "no"
        elif state == "load_ok":
            item.supported = "yes"
        else:
            item.supported = "unknown"
        item.author = rec.get("author") or ""
        item.module = rec.get("module") or ""
        item.note = rec.get("note") or ""
        item.category = rec.get("category") or C.DEFAULT_CATEGORY
        if hasattr(item, "groups_text"):
            item.groups_text = " / ".join(db.plugin_groups(key))
        item.enabled = bool(rec.get("enabled"))
        item.update_available = bool(rec.get("update_available"))
        item.missing = bool(rec.get("missing"))
        item.favorite = bool(rec.get("favorite"))
        item.startup = bool(rec.get("startup"))
        item.last_error = rec.get("last_error") or ""
        item.restore_error = rec.get("restore_error") or ""
        item.selected = key in SELECTED_KEYS

    prefs.active_index = keys.index(previous_selection) if previous_selection in keys else -1
    prefs.selected_key = previous_selection if previous_selection in keys else ""
    _rebuild_categories(prefs, db)
    _rebuild_groups(prefs, db)
    native_names.invalidate_cache()
    _LAST["sig"] = _signature(prefs)
    return keys


def database_status(prefs) -> str:
    return _database_status[1] if _database_status[0] == prefs.library_path else "MISSING"


# 勾选集合（以插件 key 为准，跨重建/重绘保持）
SELECTED_KEYS: set[str] = set()


def set_selected(key: str, selected: bool) -> None:
    if not key:
        return
    if selected:
        SELECTED_KEYS.add(key)
    else:
        SELECTED_KEYS.discard(key)


def selected_keys(prefs) -> list[str]:
    """当前**可见列表**中被勾选的 key（保持列表顺序）。"""
    return [it.key for it in prefs.plugin_items if it.key and it.key in SELECTED_KEYS]


def all_selected_keys() -> list[str]:
    """所有被勾选的 key，包含因过滤而暂时不可见的项。"""
    return sorted(SELECTED_KEYS)


def clear_selection() -> None:
    SELECTED_KEYS.clear()


# 供操作符枚举使用：不依赖 context 的分组/分类缓存
CATEGORY_CACHE: list[str] = [C.DEFAULT_CATEGORY]


def _rebuild_categories(prefs, db: LibraryDB) -> None:
    global CATEGORY_CACHE
    prefs.category_items.clear()
    all_item = prefs.category_items.add()
    all_item.name = "全部"
    for name in db.categories:
        item = prefs.category_items.add()
        item.name = name
    CATEGORY_CACHE = [C.DEFAULT_CATEGORY] + [n for n in db.categories if n != C.DEFAULT_CATEGORY]


def _rebuild_groups(prefs, db: LibraryDB) -> None:
    if not hasattr(prefs, "group_items"):
        return
    prefs.group_items.clear()
    for info in group_service.list_groups(db):
        g = prefs.group_items.add()
        g.name = info["name"]
        g.active = bool(info["active"])
        g.startup = bool(info["startup"])
        g.pinned = bool(info["pinned"])
        g.total = int(info["total"])
        g.enabled = int(info["enabled"])


def category_options() -> list[str]:
    """当前可用分组（含未分类），供操作符枚举使用。"""
    opts = list(CATEGORY_CACHE)
    if C.DEFAULT_CATEGORY not in opts:
        opts.insert(0, C.DEFAULT_CATEGORY)
    return opts


def group_options() -> list[str]:
    """当前可用工作分组（不含未分类）。"""
    return [n for n in CATEGORY_CACHE if n != C.DEFAULT_CATEGORY]


def refresh_cache(prefs=None) -> list[str]:
    """从库文件重新读取分组到缓存，保证任何入口都能拿到最新列表。"""
    global CATEGORY_CACHE
    path = getattr(prefs, "library_path", "") if prefs is not None else ""
    if path:
        try:
            db = LibraryDB(path)
            CATEGORY_CACHE = [C.DEFAULT_CATEGORY] + [
                n for n in db.categories if n != C.DEFAULT_CATEGORY
            ]
        except Exception:
            pass
    return CATEGORY_CACHE


def category_counts(prefs) -> dict:
    if not prefs.library_path:
        return {}
    db = LibraryDB(prefs.library_path)
    counts = db.category_counts()
    counts["全部"] = len(db.plugins)
    return counts
