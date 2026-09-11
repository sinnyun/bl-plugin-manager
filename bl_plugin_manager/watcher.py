"""投放区扫描（手动触发）：处理 <库>/inbox 与库根目录中投放的插件。

本模块不注册任何定时器——插件只在用户点击「扫描投放区」时运行，
因此每次都会做一次完整检查，并逐项记录导入结果与失败原因。
"""

from __future__ import annotations

import os

from . import constants as C, library, scan
from .db import LibraryDB


def _fingerprint(path: str) -> float:
    """目录取 __init__ / manifest 的最近修改时间。"""
    stamps = []
    for name in (C.MANIFEST_NAME, C.LEGACY_INIT):
        f = os.path.join(path, name)
        if os.path.isfile(f):
            try:
                stamps.append(os.path.getmtime(f))
            except OSError:
                pass
    return max(stamps) if stamps else 0.0


def _describe(path: str) -> str:
    """给报告用的简短描述。"""
    if os.path.isfile(path):
        return f"{path}"
    return path


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


def scan_inbox(root: str, db: LibraryDB, move: bool = True, enable: bool = False) -> dict:
    """扫描投放区与库根目录，逐项记录结果。

    返回 dict：
        imported / skipped / failed  计数
        entries                      每项 {name, path, kind, status, detail}
        scanned                      扫描的投放位置数
    """
    result = {"imported": 0, "skipped": 0, "failed": 0, "entries": [], "scanned": 0}

    # 1) 标准投放区 <库>/inbox/
    inbox = os.path.join(root, C.DIR_INBOX)
    if os.path.isdir(inbox):
        result["scanned"] += 1
        try:
            entries = sorted(os.scandir(inbox), key=lambda e: e.name.lower())
        except OSError:
            entries = []
        for entry in entries:
            if entry.is_dir():
                _handle_dir(entry.path, entry.name, root, db, result, move, "投放区")
            elif entry.is_file() and entry.name.lower().endswith(".zip"):
                _handle_zip(entry.path, entry.name, root, db, result, "投放区", True)

    # 2) 库根目录直接投放
    if os.path.isdir(root):
        result["scanned"] += 1
        reserved = {C.DIR_ADDONS, C.DIR_EXTENSIONS, C.DIR_INBOX, C.DIR_TRASH, C.DIR_META}
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
        except OSError:
            entries = []
        for entry in entries:
            if entry.name in reserved or entry.name.startswith("."):
                continue
            if entry.is_dir():
                _handle_dir(entry.path, entry.name, root, db, result, move, "库根目录")
            elif entry.is_file() and entry.name.lower().endswith(".zip"):
                _handle_zip(entry.path, entry.name, root, db, result, "库根目录", True)

    return result
