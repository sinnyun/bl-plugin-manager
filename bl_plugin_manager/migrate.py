"""迁移：扫描既有的脚本目录 / 默认用户目录 / 本地扩展仓库，把散落插件收编进插件库。"""

from __future__ import annotations

import os

import bpy

from . import bridge, constants as C, library, scan
from .db import LibraryDB

# 绝不作为外部收编来源的系统/内部仓库（注意：user_default 是用户本地安装的扩展目录，必须纳入扫描！）
_SKIP_MIGRATE_REPO_MODULES = {"system", "addons", C.REPO_MODULE}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _is_inside(path: str, root: str) -> bool:
    try:
        return _norm(os.path.commonpath([_norm(path), _norm(root)])) == _norm(root)
    except ValueError:
        return False


def is_temp_or_backup_folder(name: str) -> bool:
    """Return True for updater backup/staging residue folders that should never be adopted."""
    low = (name or "").strip().lower()
    if not low or low.startswith("."):
        return True
    if low == C.ADDON_ID.lower():
        return True
    markers = ("_backup_temp", "_updater_backup", ".previous", "-previous-", ".stage.")
    return any(marker in low for marker in markers)


def _existing_ids(db: LibraryDB) -> set[str]:
    ids = set()
    for rec in db.plugins.values():
        if rec.get("missing") or rec.get("kind") == C.KIND_BUILTIN:
            continue
        if rec.get("id"):
            ids.add(rec["id"].lower())
        if rec.get("pkg_id"):
            ids.add(rec["pkg_id"].lower())
        if rec.get("folder_name"):
            ids.add(rec["folder_name"].lower())
    return ids


def collect_candidates(root: str, db: LibraryDB) -> list[dict]:
    """汇总可迁移的外部插件。

    扫描来源：
    1. Blender 默认用户插件目录（``user_resource('SCRIPTS', path='addons')``）；
    2. 偏好设置中配置的自定义脚本目录；
    3. 所有用户侧扩展仓库（包含 ``user_default`` 及指向库外的自定义/官方仓库）；
    自动跳过：库目录自身、Blender 系统内置 ``addons_core``、管理器自身及更新残留备份目录。
    """
    candidates: list[dict] = []
    seen_paths: set[str] = set()
    existing = _existing_ids(db)
    builtin_names = {n.lower() for n in bridge.builtin_module_names()}

    def add(path: str, kind: str, source: str, *, repo_module: str = ""):
        folder = os.path.basename(os.path.normpath(path))
        if is_temp_or_backup_folder(folder):
            return
        norm = _norm(path)
        if norm in seen_paths or _is_inside(path, root):
            return
        seen_paths.add(norm)
        meta = scan.read_meta(path, kind) or {}
        pkg_id = (meta.get("id") or "").lower()
        conflict = (kind == C.KIND_ADDON and folder.lower() in builtin_names)
        module_name = f"bl_ext.{repo_module}.{folder}" if (kind == C.KIND_EXTENSION and repo_module) else folder
        candidates.append(
            {
                "path": path,
                "kind": kind,
                "name": meta.get("name") or folder,
                "folder": folder,
                "version": meta.get("version", ""),
                "source": source,
                "module": module_name,
                "already": bool((pkg_id and pkg_id in existing) or (folder.lower() in existing)),
                "builtin_conflict": conflict,
            }
        )

    # 1) Blender 默认用户 scripts/addons 目录
    try:
        default_addons = bpy.utils.user_resource("SCRIPTS", path=C.DIR_ADDONS)
        if default_addons and os.path.isdir(default_addons):
            for entry in scan.scan_dir(default_addons, kind_hint=C.KIND_ADDON):
                if entry["valid"]:
                    add(entry["abs"], entry["kind"], "Blender 默认用户插件目录")
    except Exception:
        pass

    # 2) 用户自定义脚本目录下的 addons
    try:
        for item in bpy.context.preferences.filepaths.script_directories:
            if not item.directory:
                continue
            base = os.path.join(item.directory, C.DIR_ADDONS)
            if not os.path.isdir(base):
                continue
            for entry in scan.scan_dir(base, kind_hint=C.KIND_ADDON):
                if entry["valid"]:
                    add(entry["abs"], entry["kind"], f"脚本目录: {item.name}")
    except Exception:
        pass

    # 3) 用户本地扩展仓库（含 user_default 与库外自定义仓库）
    try:
        for repo in bpy.context.preferences.extensions.repos:
            module = getattr(repo, "module", "")
            if module in _SKIP_MIGRATE_REPO_MODULES:
                continue
            if getattr(repo, "source", "") == "SYSTEM":
                continue
            directory = getattr(repo, "custom_directory", "") or getattr(repo, "directory", "")
            if not directory or not os.path.isdir(directory) or _is_inside(directory, root):
                continue
            label = getattr(repo, "name", "") or module
            for entry in scan.scan_dir(directory, kind_hint=C.KIND_EXTENSION):
                if entry["valid"]:
                    add(entry["abs"], entry["kind"], f"扩展仓库: {label}", repo_module=module)
    except Exception:
        pass

    return candidates


def import_candidates(root: str, db: LibraryDB, candidates: list[dict], move: bool = False,
                      enable: bool = False) -> dict:
    stats = {"ok": 0, "skip": 0, "fail": 0, "conflicts": 0, "errors": [], "details": []}
    for cand in candidates:
        name = cand.get("name") or cand.get("folder") or ""
        if cand.get("already"):
            stats["skip"] += 1
            stats["details"].append({
                "name": name, "path": cand["path"], "kind": cand["kind"],
                "status": "skipped", "detail": f"库中已存在（来源：{cand.get('source', '')}）",
            })
            continue
        if cand.get("builtin_conflict"):
            stats["skip"] += 1
            stats["conflicts"] += 1
            msg = f"{cand.get('folder')}: 与 Blender 内置插件同名，已跳过收编（请使用内置链接管理）"
            stats["errors"].append(msg)
            stats["details"].append({
                "name": name, "path": cand["path"], "kind": cand["kind"],
                "status": "skipped", "detail": "与 Blender 内置插件同名，已跳过",
            })
            continue
        src_mod = cand.get("module") or ""
        was_enabled = bool(src_mod and bridge.is_module_enabled(src_mod))
        if move and was_enabled:
            # 移动前先停用源位置模块，避免搬家后旧仓库仍保留启用状态或文件被锁
            bridge.set_enabled(src_mod, False)
        try:
            library.import_plugin_dir(
                cand["path"], root, db, move=move, enable=(enable or was_enabled),
                origin=cand.get("source", "migrate"), origin_path=cand["path"],
            )
            stats["ok"] += 1
            stats["details"].append({
                "name": name, "path": cand["path"], "kind": cand["kind"],
                "status": "imported", "detail": f"已收编（原来源：{cand.get('source', '')}）",
            })
        except Exception as exc:
            if move and was_enabled:
                bridge.set_enabled(src_mod, True)
            stats["fail"] += 1
            stats["errors"].append(f"{cand.get('folder')}: {exc}")
            stats["details"].append({
                "name": name, "path": cand["path"], "kind": cand["kind"],
                "status": "failed", "detail": str(exc),
            })
    return stats
