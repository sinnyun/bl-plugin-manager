"""更新检测：比对插件库内扩展插件与扩展仓库缓存索引中的版本号。"""

from __future__ import annotations

import json
import os

import bpy

from . import constants as C, scan
from .db import now_iso


def _repo_dirs() -> list[tuple[str, str]]:
    """返回 [(仓库名, 目录)]，仅包含带可用目录的仓库。"""
    out = []
    try:
        repos = bpy.context.preferences.extensions.repos
    except Exception:
        return out
    for repo in repos:
        directory = getattr(repo, "custom_directory", "") or getattr(repo, "directory", "")
        if directory and os.path.isdir(directory):
            out.append((getattr(repo, "name", ""), directory))
    return out


def collect_known_versions(exclude_dir: str = "") -> dict:
    """从各扩展仓库的 .blender_ext/index.json 汇总 id -> 最新版本信息。"""
    known: dict[str, dict] = {}
    exclude = os.path.normcase(os.path.abspath(exclude_dir)) if exclude_dir else ""
    for name, directory in _repo_dirs():
        if exclude and os.path.normcase(os.path.abspath(directory)) == exclude:
            continue
        index_path = os.path.join(directory, ".blender_ext", "index.json")
        if not os.path.isfile(index_path):
            continue
        try:
            with open(index_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        for item in (data.get("data") or []):
            pkg_id = item.get("id")
            version = scan.version_str(item.get("version"))
            if not pkg_id or not version:
                continue
            cur = known.get(pkg_id)
            if cur is None or scan.compare_versions(version, cur["version"]) > 0:
                known[pkg_id] = {
                    "version": version,
                    "name": item.get("name", ""),
                    "repo": name,
                    "url": item.get("archive_url", ""),
                    "archive_hash": item.get("archive_hash", ""),
                    "archive_size": item.get("archive_size", 0),
                    "tagline": item.get("tagline", ""),
                    "website": item.get("website", ""),
                }
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

    known = collect_known_versions(exclude_dir="")
    stats = {"checked": 0, "updates": 0, "unknown": 0}
    stamp = now_iso()

    for rec in db.plugins.values():
        rec["last_checked"] = stamp
        if rec.get("kind") != C.KIND_EXTENSION:
            rec["update_available"] = False
            rec["latest_version"] = ""
            stats["unknown"] += 1
            continue
        info = known.get(rec.get("id") or "")
        if not info:
            rec["update_available"] = False
            rec["latest_version"] = ""
            stats["unknown"] += 1
            continue
        stats["checked"] += 1
        rec["latest_version"] = info["version"]
        rec["latest_url"] = info.get("url", "")
        rec["latest_hash"] = info.get("archive_hash", "")
        rec["latest_size"] = info.get("archive_size", 0)
        rec["latest_website"] = info.get("website", "")
        if scan.compare_versions(info["version"], rec.get("version", "0")) > 0:
            rec["update_available"] = True
            stats["updates"] += 1
        else:
            rec["update_available"] = False
            rec["latest_url"] = ""
            rec["latest_hash"] = ""
            rec["latest_size"] = 0

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
