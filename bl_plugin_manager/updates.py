"""更新检测：比对插件库内扩展插件与扩展仓库缓存索引中的版本号。"""

from __future__ import annotations

import bpy

from . import constants as C, scan, store
from .db import now_iso


def collect_known_versions() -> dict:
    known = {}
    for offer in store.read_catalog(include_unavailable=True):
        if offer["version"] and store.is_compatible(offer):
            known.setdefault(offer["id"].casefold(), []).append(offer)
    return known


def check_updates(db, root: str, online: bool = False) -> dict:
    """标记 update_available / latest_version。返回统计。

    注意：库的 extensions 目录同时也是官方商店仓库的目录（索引就在其中），
    因此这里**不能**排除它，否则会读不到商店目录而查不出任何更新。
    """
    if online:
        try:
            bpy.ops.extensions.repo_sync_all()
        except Exception as exc:
            print("[插件库] 同步商店索引失败（将使用本地缓存）:", exc)

    known = collect_known_versions()
    stats = {"checked": 0, "updates": 0, "unknown": 0}
    stamp = now_iso()

    for rec in db.plugins.values():
        rec["last_checked"] = stamp
        if rec.get("kind") != C.KIND_EXTENSION:
            rec["update_available"] = False
            rec["latest_version"] = ""
            stats["unknown"] += 1
            continue
        key = str(rec.get("pkg_id") or rec.get("id") or "").casefold()
        source = rec.get("archive_source")
        offers = known.get(key, ())
        candidates = [offer for offer in offers if offer["source"] == source]
        rec["update_source_required"] = bool(not candidates and offers and not rec.get("missing"))
        if not candidates or rec.get("missing"):
            rec["update_available"] = False
            rec["latest_version"] = ""
            rec["latest_url"] = ""
            stats["unknown"] += 1
            continue
        offer = candidates[0]
        stats["checked"] += 1
        rec["update_available"] = scan.compare_versions(offer["version"], rec.get("version", "0")) > 0
        rec["latest_version"] = offer["version"]
        rec["latest_url"] = offer["archive_url"]
        rec["latest_hash"] = offer["archive_hash"]
        rec["latest_size"] = offer["archive_size"]
        rec["latest_website"] = offer["website"]
        rec["latest_repo"] = offer["repo"]
        if rec["update_available"]:
            stats["updates"] += 1

    db.save()
    return stats


def unmet_requirements(rec: dict, blender_version: tuple | None = None) -> bool:
    """判断插件要求的最低 Blender 版本是否高于当前版本。"""
    if blender_version is None:
        blender_version = bpy.app.version
    need = scan.version_key(rec.get("blender_min"))
    if not any(need):
        return False
    cur = tuple(blender_version[: len(need)])
    need = need[: len(blender_version)]
    return need > cur
