"""把库数据同步到偏好设置里的列表项，供 UIList 与面板显示。"""

from __future__ import annotations

import time

from . import constants as C, scan
from .db import LibraryDB

# 轻量节流：避免 N 面板高频重绘时反复读取库文件
_LAST = {"t": 0.0, "sig": None}


def _signature(prefs):
    return (
        prefs.library_path,
        prefs.active_category,
        prefs.only_favorites,
        prefs.only_updates,
        prefs.only_enabled,
        prefs.only_incompatible,
        prefs.search,
    )


def maybe_rebuild(prefs, force: bool = False) -> None:
    """在 draw 中调用：签名变化或超过 0.3 秒才真正重建。"""
    sig = _signature(prefs)
    now = time.time()
    if not force and sig == _LAST["sig"] and (now - _LAST["t"]) < 0.3:
        return
    _LAST["sig"] = sig
    _LAST["t"] = now
    rebuild_items(prefs)



def _filter(rec: dict, prefs) -> bool:
    if prefs.only_favorites and not rec.get("favorite"):
        return False
    if prefs.only_updates and not rec.get("update_available"):
        return False
    if prefs.only_enabled and not rec.get("enabled"):
        return False
    if getattr(prefs, "only_incompatible", False):
        # 实时判定，避免用换版本后已过期的存储结论
        comp = scan.blender_compat(rec.get("blender_min", ""),
                                   rec.get("blender_max", ""))[0]
        if comp not in ("too_new", "too_old"):
            return False
    needle = (prefs.search or "").strip().lower()
    if needle:
        hay = " ".join(
            str(rec.get(f) or "")
            for f in ("name", "display_name", "folder_name", "description", "author", "note", "tags")
        ).lower()
        if needle not in hay:
            return False
    return True


def rebuild_items(prefs) -> list[str]:
    """依据当前过滤条件重建 plugin_items，返回可见的 key 顺序。

    勾选状态保存在 SELECTED_KEYS 里，重建后按 key 恢复，因此面板重绘、
    刷新、过滤都不会把用户的勾选弄丢。
    """
    prefs.plugin_items.clear()
    if not prefs.library_path:
        prefs.active_index = 0
        return []

    db = LibraryDB(prefs.library_path)
    keys = []
    for key, rec in db.plugins.items():
        cat = rec.get("category") or C.DEFAULT_CATEGORY
        if prefs.active_category and prefs.active_category not in ("", "全部") and cat != prefs.active_category:
            continue
        if not _filter(rec, prefs):
            continue
        keys.append(key)

    keys.sort(
        key=lambda k: (
            (db.plugins[k].get("category") or ""),
            (db.plugins[k].get("display_name") or db.plugins[k].get("name") or k).lower(),
        )
    )

    # 只清理「库中已不存在」的勾选项；因过滤而暂时不可见的勾选要保留，
    # 否则切换过滤条件就会把用户的批量选择冲掉。
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
        # 兼容性**每次按当前 Blender 版本现算**：存储在记录里的 compat 是
        # 相对某个版本的结论，换版本后就会过期；这里始终用实时判定，
        # 保证在任何 Blender 版本下看到的都是准确结果。
        item.compat, item.compat_detail = scan.blender_compat(
            item.blender_min, item.blender_max)
        item.compat_text = scan.compat_label(item.blender_min, item.blender_max)
        item.max_version_text = scan.max_version_label(item.blender_max)
        # 只有真实加载测试才可判定「支持」。版本声明只能说明候选兼容，
        # 未实测时保持 unknown，避免把无法启动的插件显示成可用；UI 会
        # 将这个状态明确显示为“声明兼容，未实测”。
        # 判定优先级：实测结果 > 明确版本超限 > 未实测未知
        item.load_state = rec.get("load_state") or ""
        item.load_error = rec.get("load_error") or ""
        item.residue = int(rec.get("residue") or 0)
        if item.load_state == "failed":
            item.supported = "no"
        elif item.load_state == "ok":
            item.supported = "yes"
        elif item.compat in ("too_new", "too_old"):
            item.supported = "no"
        else:
            item.supported = "unknown"
        item.author = rec.get("author") or ""
        item.module = rec.get("module") or ""
        item.note = rec.get("note") or ""
        item.category = rec.get("category") or C.DEFAULT_CATEGORY
        item.enabled = bool(rec.get("enabled"))
        item.update_available = bool(rec.get("update_available"))
        item.missing = bool(rec.get("missing"))
        item.favorite = bool(rec.get("favorite"))
        item.startup = bool(rec.get("startup"))
        item.last_error = rec.get("last_error") or ""
        item.restore_error = rec.get("restore_error") or ""
        # 恢复勾选状态
        item.selected = key in SELECTED_KEYS

    if prefs.active_index >= len(keys):
        prefs.active_index = max(0, len(keys) - 1)
    prefs.selected_key = keys[prefs.active_index] if keys else ""
    _rebuild_categories(prefs, db)
    return keys


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
    """当前**可见列表**中被勾选的 key（保持列表顺序）。

    批量操作应作用于全部勾选项（含被过滤暂时隐藏的），用 all_selected_keys()。
    """
    return [it.key for it in prefs.plugin_items if it.key and it.key in SELECTED_KEYS]


def all_selected_keys() -> list[str]:
    """所有被勾选的 key，包含因过滤而暂时不可见的项。"""
    return sorted(SELECTED_KEYS)


def clear_selection() -> None:
    SELECTED_KEYS.clear()


# 供操作符枚举使用：不依赖 context 的分类缓存，避免下拉在某些调用环境下取不到值
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


def category_options() -> list[str]:
    """当前可用分类（含未分类），供操作符枚举使用。"""
    opts = list(CATEGORY_CACHE)
    if C.DEFAULT_CATEGORY not in opts:
        opts.insert(0, C.DEFAULT_CATEGORY)
    return opts


def refresh_cache(prefs=None) -> list[str]:
    """从库文件重新读取分类到缓存，保证任何入口都能拿到最新列表。"""
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
    counts = {}
    for rec in db.plugins.values():
        cat = rec.get("category") or C.DEFAULT_CATEGORY
        counts[cat] = counts.get(cat, 0) + 1
    counts["全部"] = sum(counts.values())
    return counts
