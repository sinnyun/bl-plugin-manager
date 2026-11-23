"""投放区扫描（手动触发）：处理 <库>/inbox 与库根目录中投放的插件。

本模块不注册任何定时器——插件只在用户点击「扫描投放区」时运行，
因此每次都会做一次完整检查，并逐项记录导入结果与失败原因。
"""

from __future__ import annotations

import os

from . import constants as C, library, scan
from .db import LibraryDB


def _handle_dir(path: str, name: str, root: str, db, result: dict, move: bool,
                origin: str) -> None:
    if name in C.IGNORED_NAMES or name.startswith("."):
        return
    kind, reason = scan.validate_plugin(path)
    if kind is None:
        # 目录里出现了 manifest 或 __init__.py，说明本意是装插件但内容有问题 →
        # 归为「失败」（需要你去修），否则只是无关文件夹 → 归为「跳过」
        looks_like_plugin = (
            os.path.isfile(os.path.join(path, C.MANIFEST_NAME))
            or os.path.isfile(os.path.join(path, C.LEGACY_INIT))
        )
        status = "failed" if looks_like_plugin else "skipped"
        result["entries"].append({
            "name": name, "path": path, "kind": "-", "status": status,
            "detail": reason or "不是插件",
        })
        result[status] += 1
        return
    try:
        rec = library.import_plugin_dir(path, root, db, move=move,
                                        origin=origin, origin_path=path)
        result["entries"].append({
            "name": rec.get("name") or name, "path": path,
            "kind": C.KIND_LABELS.get(kind, kind), "status": "imported",
            "detail": f"版本 {rec.get('version') or '—'} → {rec.get('rel', '')}",
        })
        result["imported"] += 1
    except Exception as exc:
        result["entries"].append({
            "name": name, "path": path, "kind": C.KIND_LABELS.get(kind, kind),
            "status": "failed", "detail": str(exc),
        })
        result["failed"] += 1


def _handle_zip(path: str, name: str, root: str, db, result: dict, origin: str,
                remove_after: bool) -> None:
    info = scan.inspect_zip(path)
    if not info:
        result["entries"].append({
            "name": name, "path": path, "kind": "压缩包", "status": "skipped",
            "detail": "压缩包内没有找到可识别的插件（缺少 manifest 或 __init__.py）",
        })
        result["skipped"] += 1
        return
    try:
        rec = library.import_zip(path, root, db, origin=origin, origin_path=path)
        if remove_after:
            try:
                os.remove(path)
            except OSError:
                pass
        result["entries"].append({
            "name": rec.get("name") or name, "path": path,
            "kind": C.KIND_LABELS.get(rec.get("kind", ""), "压缩包"),
            "status": "imported",
            "detail": f"版本 {rec.get('version') or '—'} → {rec.get('rel', '')}",
        })
        result["imported"] += 1
    except Exception as exc:
        result["entries"].append({
            "name": name, "path": path, "kind": "压缩包", "status": "failed",
            "detail": str(exc),
        })
        result["failed"] += 1


def _locations(root: str):
    yield os.path.join(root, C.DIR_INBOX), "投放区"
    yield root, "库根目录"


def _candidates(directory: str):
    ignored = {C.DIR_ADDONS, C.DIR_EXTENSIONS, C.DIR_INBOX, C.DIR_TRASH, C.DIR_META}
    with os.scandir(directory) as entries:
        for entry in sorted(entries, key=lambda item: item.name.casefold()):
            if entry.name in ignored or scan.is_ignored(entry.name):
                continue
            if os.path.dirname(os.path.realpath(entry.path)) != os.path.realpath(directory):
                continue
            is_folder = entry.is_dir()
            if is_folder or (entry.is_file() and entry.name.lower().endswith(".zip")):
                yield entry.path, entry.name, is_folder


def preview_paths(root: str) -> list[str]:
    return [path for directory, _origin in _locations(root) if os.path.isdir(directory)
            for path, _name, _folder in _candidates(directory)]


def scan_inbox(root: str, db: LibraryDB, move: bool = True, enable: bool = False,
               *, allowed=None) -> dict:
    """Process the previewed paths and report each import, skip or failure."""
    result = {"imported": 0, "skipped": 0, "failed": 0, "entries": [], "scanned": 0}
    allowed_paths = set(allowed) if allowed is not None else None
    for directory, origin in _locations(root):
        if not os.path.isdir(directory):
            continue
        result["scanned"] += 1
        for path, name, is_folder in _candidates(directory):
            if allowed_paths is not None and path not in allowed_paths:
                continue
            if is_folder:
                _handle_dir(path, name, root, db, result, move, origin)
            else:
                _handle_zip(path, name, root, db, result, origin, move)
    return result
