"""迁移：扫描既有的脚本目录 / 本地扩展仓库，把散落插件收编进插件库。"""

from __future__ import annotations

import os

import bpy

from . import bridge, constants as C, library, scan
from .db import LibraryDB


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _is_inside(path: str, root: str) -> bool:
    try:
        return _norm(os.path.commonpath([_norm(path), _norm(root)])) == _norm(root)
    except ValueError:
        return False


def _existing_ids(db: LibraryDB) -> set[str]:
    ids = set()
    for rec in db.plugins.values():
        if rec.get("id"):
            ids.add(rec["id"].lower())
        if rec.get("folder_name"):
            ids.add(rec["folder_name"].lower())
    return ids


def collect_candidates(root: str, db: LibraryDB) -> list[dict]:
    """汇总可迁移的插件。默认跳过 Blender 官方/系统仓库，避免搬动内置内容。"""
    candidates: list[dict] = []
    seen_paths: set[str] = set()
    existing = _existing_ids(db)

    def add(path: str, kind: str, source: str):
        norm = _norm(path)
        if norm in seen_paths or _is_inside(path, root):
            return
        seen_paths.add(norm)
        meta = scan.read_meta(path, kind) or {}
        name = os.path.basename(path)
        pkg_id = (meta.get("id") or "").lower()
        candidates.append(
            {
                "path": path,
                "kind": kind,
                "name": meta.get("name") or name,
                "folder": name,
                "version": meta.get("version", ""),
                "source": source,
                "already": (pkg_id and pkg_id in existing) or (name.lower() in existing),
            }
        )

    # 1) 用户脚本目录下的 addons
    try:
        for item in bpy.context.preferences.filepaths.script_directories:
            base = os.path.join(item.directory, C.DIR_ADDONS)
            for entry in scan.scan_dir(base, kind_hint=C.KIND_ADDON):
                if entry["valid"]:
                    add(entry["abs"], entry["kind"], f"脚本目录: {item.name}")
    except Exception:
        pass

    # 2) 用户本地扩展仓库
    try:
        for repo in bpy.context.preferences.extensions.repos:
            module = getattr(repo, "module", "")
            if module in C.BUILTIN_REPO_MODULES or module == C.REPO_MODULE:
                continue
            if getattr(repo, "source", "") != "USER":
                continue
            directory = getattr(repo, "custom_directory", "") or getattr(repo, "directory", "")
            if not directory or not os.path.isdir(directory):
                continue
            for entry in scan.scan_dir(directory, kind_hint=C.KIND_EXTENSION):
                if entry["valid"]:
                    add(entry["abs"], entry["kind"], f"扩展仓库: {getattr(repo, 'name', module)}")
    except Exception:
        pass

    return candidates


def import_candidates(root: str, db: LibraryDB, candidates: list[dict], move: bool = False,
                      enable: bool = False) -> dict:
    stats = {"ok": 0, "skip": 0, "fail": 0, "errors": []}
    for cand in candidates:
        if cand.get("already"):
            stats["skip"] += 1
            continue
        try:
            library.import_plugin_dir(
                cand["path"], root, db, move=move, enable=enable,
                origin=cand.get("source", "migrate"), origin_path=cand["path"],
            )
            stats["ok"] += 1
        except Exception as exc:
            stats["fail"] += 1
            stats["errors"].append(f"{cand.get('folder')}: {exc}")
    return stats
