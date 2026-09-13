"""插件库操作：导入、归档、备份、同步、移除。"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
import uuid
from datetime import datetime

from . import bridge, constants as C, scan
from .db import LibraryDB, now_iso
from .security.paths import UnsafeLibraryPathError, resolve_record_path
from .security.archive import extract_archive


# ---------------------------------------------------------------------------
# 基本工具
# ---------------------------------------------------------------------------
# Control chars (0x00-0x1F) -> underscore; via translate so the source
# file never needs a literal NUL escape sequence.
_CTRL_MAP = dict((c, "_") for c in range(0x20))


def sanitize_name(name: str) -> str:
    """清理目录名：去掉路径分隔符与非法字符。

    传统插件的目录名会被 Blender 当作模块名 import，因此**点号必须处理**：
    含点号的目录名（如 "鲜花盛开 (Blooming Flowers 2.0中英对照版)"）会被
    当成包子路径，导致 `No module named '...2'` 这类加载失败。
    中文与空格可以保留（Blender 支持）。
    """
    name = (name or "").strip().strip(".")
    name = name.translate(_CTRL_MAP)                       # 控制字符 -> 下划线
    name = re.sub(r'[\/:*?"<>|]', "_", name)            # 路径分隔符等非法字符
    name = name.replace(".", "_")                          # 点号会被当作包分隔
    name = re.sub(r"_{2,}", "_", name).strip("_ ")
    return name or "unnamed"


def sanitize_module_name(name: str) -> str:
    """把名字净化为合法的 Python 模块名（扩展插件的目录名即模块名）。

    扩展插件的模块名是 ``bl_ext.<仓库>.<目录名>``，Blender 会校验目录名是否
    为合法标识符；含空格、连字符、括号等会报
    "is not a supported module name, skipping" 而根本无法加载。
    """
    cleaned = re.sub(r"[^0-9A-Za-z_]", "_", (name or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return "plugin"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def effective_name(rec: dict) -> str:
    return rec.get("display_name") or rec.get("name") or rec.get("folder_name") or rec.get("key", "")


def rel_key(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def _base_for(root: str, kind: str) -> str:
    return os.path.join(root, C.DIR_EXTENSIONS if kind == C.KIND_EXTENSION else C.DIR_ADDONS)


def _unique_dest(base: str, name: str) -> str:
    dest = os.path.join(base, name)
    if not os.path.exists(dest):
        return dest
    i = 2
    while True:
        cand = os.path.join(base, f"{name}_{i}")
        if not os.path.exists(cand):
            return cand
        i += 1


def _copy_tree_into(src: str, dst: str) -> None:
    """清空 dst 后把 src 的内容拷入（保留 dst 目录本身，即保留目录名/模块名）。"""
    for entry in os.scandir(dst):
        if entry.is_dir():
            shutil.rmtree(entry.path, ignore_errors=True)
        else:
            try:
                os.remove(entry.path)
            except OSError:
                pass
    for entry in os.scandir(src):
        target = os.path.join(dst, entry.name)
        if entry.is_dir():
            shutil.copytree(entry.path, target)
        else:
            shutil.copy2(entry.path, target)


def _replace_tree_transactional(src: str, dest: str, root: str, kind: str) -> None:
    """Stage and atomically swap plugin contents, retaining a rollback path."""
    backup = backup_plugin(root, dest)
    if not backup:
        raise RuntimeError("无法创建更新前备份，已取消替换")
    parent = os.path.dirname(dest)
    stage = tempfile.mkdtemp(prefix=".pm_stage_", dir=parent)
    old = dest + ".pm_old_" + uuid.uuid4().hex
    try:
        _copy_tree_into(src, stage)
        valid_kind, reason = scan.validate_plugin(stage)
        if valid_kind != kind:
            raise ValueError(reason or "更新包类型与现有插件不一致")
        shutil.move(dest, old)
        try:
            shutil.move(stage, dest)
        except Exception:
            # Restore the old directory before propagating the failure.
            if os.path.exists(old) and not os.path.exists(dest):
                shutil.move(old, dest)
            raise
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _refresh_existing(rec: dict, new_src: str, dest: str, root: str, db: LibraryDB,
                      meta: dict, origin: str, origin_path: str,
                      move: bool, enable: bool) -> dict:
    """用新内容刷新库中同名扩展：保留用户字段与启用状态，复用原目录。"""
    kind = scan.detect_kind(dest) or C.KIND_EXTENSION
    was_enabled = bridge.is_module_enabled(rec.get("module", ""))
    if was_enabled:
        ok, err = bridge.set_enabled(rec.get("module", ""), False)
        if not ok:
            raise RuntimeError(f"更新前停用失败: {err}")
    try:
        _replace_tree_transactional(new_src, dest, root, kind)
        if move:
            shutil.rmtree(new_src, ignore_errors=True)
    except Exception:
        if was_enabled:
            bridge.set_enabled(rec.get("module", ""), True)
        raise

    fresh = scan.read_meta(dest, kind) or {}
    rec["version"] = fresh.get("version") or rec.get("version", "")
    rec["name"] = fresh.get("name") or rec.get("name") or rec.get("folder_name")
    rec["blender_min"] = fresh.get("blender_min", rec.get("blender_min", ""))
    rec["blender_max"] = fresh.get("blender_max", rec.get("blender_max", ""))
    _c = scan.blender_compat(rec["blender_min"], rec["blender_max"])
    rec["compat"], rec["compat_detail"] = _c
    rec["author"] = fresh.get("author", rec.get("author", ""))
    rec["description"] = fresh.get("description", rec.get("description", ""))
    rec["auto_category"] = fresh.get("category", rec.get("auto_category", ""))
    rec["auto_tags"] = fresh.get("tags", rec.get("auto_tags", []))
    rec["metadata_fingerprint"] = scan.metadata_fingerprint(dest, kind)
    rec["issues"] = scan.manifest_issues(dest)
    rec["update_available"] = False
    rec["latest_version"] = ""
    rec["latest_url"] = ""
    rec["last_error"] = ""
    rec["updated_at"] = now_iso()
    # 保留分类/备注/别名/收藏/自启等用户字段；用户没填备注时用新描述
    if not rec.get("note"):
        rec["note"] = rec.get("description", "")
    rec["enabled"] = was_enabled
    db.upsert(rec["key"], rec)
    db.save()

    bridge.refresh_blender()
    if was_enabled and rec.get("module"):
        ok, err = bridge.set_enabled(rec["module"], True)
        rec["enabled"] = bridge.is_module_enabled(rec["module"])
        rec["restore_error"] = "" if ok else (err or "更新后重新启用失败")
        db.upsert(rec["key"], rec)
        db.save()
    return rec


def backup_plugin(root: str, plugin_dir: str) -> str | None:
    """把插件目录打包成 zip 存入 <库>/.pm/backups/。"""
    backups = os.path.join(root, C.DIR_BACKUPS)
    os.makedirs(backups, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = sanitize_name(os.path.basename(plugin_dir))
    zip_path = os.path.join(backups, f"{safe}__{stamp}.zip")
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for folder, _dirs, files in os.walk(plugin_dir):
                for fn in files:
                    full = os.path.join(folder, fn)
                    arc = os.path.relpath(full, plugin_dir)
                    zf.write(full, arc)
        return zip_path
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 建立记录
# ---------------------------------------------------------------------------
def _build_record(root: str, plugin_dir: str, kind: str, meta: dict, old: dict | None = None,
                  origin: str = "", origin_path: str = "",
                  module: str | None = None, check_issues: bool = True) -> dict:
    folder = os.path.basename(plugin_dir)
    pkg_id = meta.get("id") or (folder if kind == C.KIND_EXTENSION else "")
    # module 可由调用方预先解析（批量同步时只算一次索引），避免逐个重复遍历
    if module is None:
        module = bridge.resolve_module(plugin_dir, kind, pkg_id)
    auto_name = meta.get("name") or folder
    compat = scan.blender_compat(meta.get("blender_min", ""), meta.get("blender_max", ""))
    rec = {
        "kind": kind,
        "rel": rel_key(root, plugin_dir),
        "folder_name": folder,
        "id": pkg_id,
        "pkg_id": pkg_id,
        "name": auto_name,
        "version": meta.get("version", ""),
        "blender_min": meta.get("blender_min", ""),
        "blender_max": meta.get("blender_max", ""),
        "pkg_type": meta.get("type", ""),
        "compat": compat[0],
        "compat_detail": compat[1],
        "author": meta.get("author", ""),
        "description": meta.get("description", ""),
        "auto_category": meta.get("category", "") or C.DEFAULT_CATEGORY,
        "auto_tags": meta.get("tags", []) or [],
        "metadata_fingerprint": scan.metadata_fingerprint(plugin_dir, kind),
        "source_url": meta.get("doc_url", ""),
        "module": module,
        "missing": False,
        # 扩展插件的 manifest 合规问题（会导致 Blender 跳过加载）
        # 批量同步时可跳过（check_issues=False）以省去逐目录重读 manifest；
        # 导入/新增时仍校验，保证问题能被报出来。
        "issues": (scan.manifest_issues(plugin_dir)
                   if (check_issues and kind == C.KIND_EXTENSION) else []),
        # 上次启用失败的原因（便于排查）
        "last_error": "",
    }
    if old:
        # 已有人为指定的分类就保留；否则一律归到「未分类」，不套用插件自带分类
        rec["category"] = old.get("category") or C.DEFAULT_CATEGORY
        rec["tags"] = old.get("tags", []) or rec["auto_tags"]
        # 备注：用户手动写过就保留；没写过则用插件自带的描述自动填充
        rec["note"] = old.get("note", "") or rec["description"]
        rec["favorite"] = old.get("favorite", False)
        rec["display_name"] = old.get("display_name", "")
        rec["origin"] = old.get("origin", origin)
        rec["origin_path"] = old.get("origin_path", origin_path)
        rec["created_at"] = old.get("created_at")
        # 自启标记：保留用户设置；首次出现时保守默认为「不随启动加载」，
        # 由用户在界面显式勾选（避免把当前会话临时启用的插件误当成自启）
        rec["startup"] = bool(old["startup"]) if "startup" in old else False
        rec["last_error"] = old.get("last_error", "")
    else:
        # 新导入的插件默认「未分类」，分类完全由你自己整理
        rec["category"] = C.DEFAULT_CATEGORY
        rec["tags"] = list(rec["auto_tags"])
        # 首次导入自动写入描述作为备注，用户之后可覆盖
        rec["note"] = rec["description"]
        rec["favorite"] = False
        rec["display_name"] = ""
        rec["startup"] = False
        rec["origin"] = origin
        rec["origin_path"] = origin_path
        rec["imported_at"] = now_iso()
    rec["enabled"] = bridge.is_module_enabled(module)
    return rec


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------
def import_plugin_dir(plugin_dir: str, root: str, db: LibraryDB, move: bool = False,
                      enable: bool = False, origin: str = "manual",
                      origin_path: str = "") -> dict:
    """把一个已解开的插件目录纳入插件库。"""
    kind, reason = scan.validate_plugin(plugin_dir)
    if kind is None:
        raise ValueError(reason or "无法识别为插件")

    meta = scan.read_meta(plugin_dir, kind) or {}
    if kind == C.KIND_EXTENSION:
        # 扩展插件的目录名即模块名，必须是合法标识符
        folder = sanitize_module_name(meta.get("id") or os.path.basename(plugin_dir))
    else:
        folder = sanitize_name(os.path.basename(plugin_dir))
    base = _base_for(root, kind)
    os.makedirs(base, exist_ok=True)

    # 同 id 的扩展已存在 → 视为更新，复用原目录（保住模块名与启用状态），
    # 避免生成 xxx_2 这样的重复副本导致同一插件被加载两次。
    pkg_id = (meta.get("id") or "").strip()
    if kind == C.KIND_EXTENSION and pkg_id:
        for rec in db.plugins.values():
            if (rec.get("kind") == C.KIND_EXTENSION
                    and str(rec.get("id", "")).lower() == pkg_id.lower()
                    and not rec.get("missing")):
                try:
                    existing = str(resolve_record_path(root, rec["rel"], rec.get("kind", "addon")))
                except UnsafeLibraryPathError:
                    continue
                if os.path.isdir(existing):
                    return _refresh_existing(rec, plugin_dir, existing, root, db,
                                             meta, origin, origin_path, move, enable)

    dest = _unique_dest(base, folder)

    key = rel_key(root, dest)
    old = db.get(key) or db.get(rel_key(root, plugin_dir) if not move else "")

    # 目标已存在（同名覆盖）时先备份再清理
    if os.path.exists(dest):
        backup_plugin(root, dest)
        shutil.rmtree(dest, ignore_errors=True)

    if move:
        shutil.move(plugin_dir, dest)
    else:
        shutil.copytree(plugin_dir, dest)

    rec = _build_record(root, dest, kind, meta, old=old, origin=origin, origin_path=origin_path)
    stored = db.upsert(rel_key(root, dest), rec)
    db.save()

    bridge.refresh_blender(deep=True)
    if enable:
        stored["enabled"] = bridge.is_module_enabled(stored["module"])
        bridge.set_enabled(stored["module"], True)
        stored["enabled"] = bridge.is_module_enabled(stored["module"])
        db.upsert(stored["key"], stored)
        db.save()
    return stored


def import_zip(zip_path: str, root: str, db: LibraryDB, enable: bool = False,
               origin: str = "zip", origin_path: str = "") -> dict:
    info = scan.inspect_zip(zip_path)
    if not info:
        raise ValueError("压缩包中没有可识别的插件（缺少 manifest 或 __init__.py）")
    tmp = tempfile.mkdtemp(prefix="pm_import_")
    try:
        extract_archive(zip_path, tmp)
        src = os.path.join(tmp, info["prefix"]) if info["prefix"] else tmp
        rec = import_plugin_dir(src, root, db, move=True, enable=enable,
                                origin=origin, origin_path=origin_path)
        return rec
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def import_path(path: str, root: str, db: LibraryDB, move: bool = False,
                enable: bool = False, origin: str = "manual") -> dict:
    """统一入口：目录或 zip。"""
    if os.path.isdir(path):
        return import_plugin_dir(path, root, db, move=move, enable=enable,
                                 origin=origin, origin_path=path)
    if zipfile.is_zipfile(path):
        return import_zip(path, root, db, enable=enable, origin=origin, origin_path=path)
    raise ValueError("不是有效的插件文件夹或 zip 压缩包")


# ---------------------------------------------------------------------------
# 同步磁盘状态
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 目标目录侦测与智能连接
# ---------------------------------------------------------------------------
_LIB_SUBDIRS = (C.DIR_ADDONS, C.DIR_EXTENSIONS, C.DIR_INBOX, C.DIR_TRASH, C.DIR_META)


def inspect_target(path: str) -> dict:
    """检查目标目录的现状，决定后续动作（建空库 / 扫描 / 收编）。

    返回：
        exists          目录是否存在
        is_plugin       目录本身就是一个插件
        is_library      已含 addons/ 或 extensions/ 的插件库
        addon_count     addons/ 下的插件数
        extension_count extensions/ 下的插件数
        flat_plugins    直接散落在根目录下的插件（文件夹或 zip）
    """
    path = os.path.abspath(path) if path else ""
    info = {
        "path": path, "exists": bool(path) and os.path.isdir(path),
        "is_plugin": False, "is_library": False, "is_empty": False,
        "addon_count": 0, "extension_count": 0,
        "flat_plugins": [],
    }
    if not info["exists"]:
        return info

    info["is_plugin"] = scan.detect_kind(path) is not None
    try:
        info["is_empty"] = not any(True for _ in os.scandir(path))
    except OSError:
        info["is_empty"] = False

    add_dir = os.path.join(path, C.DIR_ADDONS)
    ext_dir = os.path.join(path, C.DIR_EXTENSIONS)
    if os.path.isdir(add_dir):
        info["is_library"] = True
        info["addon_count"] = sum(
            1 for e in os.scandir(add_dir) if e.is_dir() and not scan.is_ignored(e.name)
        )
    if os.path.isdir(ext_dir):
        info["is_library"] = True
        info["extension_count"] = sum(
            1 for e in os.scandir(ext_dir) if e.is_dir() and not scan.is_ignored(e.name)
        )

    # 根目录下直接散落的插件：转入 addons/ 或 extensions/
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
    except OSError:
        entries = []
    for e in entries:
        if e.name in _LIB_SUBDIRS or scan.is_ignored(e.name):
            continue
        if e.is_dir():
            if scan.detect_kind(e.path):
                info["flat_plugins"].append(e.path)
        elif e.is_file() and e.name.lower().endswith(".zip") and scan.inspect_zip(e.path):
            info["flat_plugins"].append(e.path)
    return info


def connect_library(root: str, db: LibraryDB, move_flat: bool = True) -> dict:
    """智能连接一个目录作为插件库。

    * 目录不存在     → 创建空库（addons/extensions/inbox/trash/.pm）
    * 已是插件库     → 保留原结构，补建缺失子目录
    * 含散落插件     → 收编进 addons/ 或 extensions/
    * 选中的是插件本身 → 报错，提示改选其上一级目录
    """
    info = inspect_target(root)
    result = {"info": info, "created": False, "imported": [], "failed": [],
              "already_library": info["is_library"], "error": ""}

    if info["path"] and info["is_plugin"]:
        result["error"] = "所选目录本身就是一个插件，请选择它的上一级目录作为插件库"
        return result

    if not info["exists"]:
        bridge.ensure_library_dirs(root)
        result["created"] = True
        return result

    # 保护：已存在、非空、既不是插件库也没有可识别的插件 →
    # 不擅自初始化，避免把资产目录等无关文件夹变成插件库
    if not info["is_library"] and not info["is_empty"] and not info["flat_plugins"]:
        result["error"] = ("该目录非空且其中没有可识别的插件，不像是插件库；"
                           "为避免误改文件夹，已取消。请改选空目录或已有的插件库目录")
        return result

    bridge.ensure_library_dirs(root)
    if info["flat_plugins"]:
        for path in info["flat_plugins"]:
            try:
                rec = import_path(path, root, db, move=move_flat, origin="目录收编")
                result["imported"].append(rec)
            except Exception as exc:
                result["failed"].append(f"{os.path.basename(path)}: {exc}")
    elif not info["is_library"]:
        # 空目录 → 初始化为空库
        result["created"] = True
    return result


def sync_library(root: str, db: LibraryDB, skip_unchanged: bool = True) -> dict:
    """扫描库内容，补建/更新记录，并标记消失的插件。

    性能要点：
    * 模块索引只解析一次并复用（原先每个插件都全量遍历一次，152 个约 8 秒）；
    * 已存在且版本未变的记录直接跳过，不做 manifest 重解析与写盘。
    """
    seen = set()
    stats = {"added": 0, "updated": 0, "unchanged": 0, "missing": 0}

    if db.data.get("schema") == 2 and db.data.get("library_id"):
        portable = []
        for kind, base in ((C.KIND_ADDON, os.path.join(root, C.DIR_ADDONS)),
                           (C.KIND_EXTENSION, os.path.join(root, C.DIR_EXTENSIONS))):
            for entry in scan.scan_dir(base, kind_hint=kind):
                if not entry["valid"]:
                    continue
                meta = entry["meta"] or {}
                portable.append({
                    "key": rel_key(root, entry["abs"]),
                    "kind": kind,
                    "rel": rel_key(root, entry["abs"]),
                    "id": meta.get("id", ""),
                    "name": meta.get("name") or entry.get("name", ""),
                    "folder_name": entry.get("name", ""),
                    "version": meta.get("version", ""),
                })
        old_keys = set(db.plugins)
        new = db.merge_scan(portable)
        stats["added"] = len(set(new) - old_keys)
        stats["updated"] = len(set(new) & old_keys)
        stats["missing"] = len(old_keys - set(new))
        return stats

    # 一次性建立模块索引，供本函数内所有记录解析复用
    module_index = bridge._module_index()

    # 扩展模块名要带上"实际仓库"，一次性取好避免逐条查询
    active_repo = bridge._active_repo_module()

    def _module_for(plugin_dir, kind, pkg_id):
        found = module_index.get(bridge._norm(plugin_dir))
        if found:
            return found
        folder = os.path.basename(os.path.normpath(plugin_dir))
        if kind == C.KIND_EXTENSION:
            return f"bl_ext.{active_repo}.{folder}"
        return folder

    for kind, base in ((C.KIND_ADDON, os.path.join(root, C.DIR_ADDONS)),
                       (C.KIND_EXTENSION, os.path.join(root, C.DIR_EXTENSIONS))):
        for entry in scan.scan_dir(base, kind_hint=kind):
            if not entry["valid"]:
                continue
            key = rel_key(root, entry["abs"])
            seen.add(key)
            old = db.get(key)
            meta = entry["meta"] or {}
            pkg_id = meta.get("id") or (entry["name"] if kind == C.KIND_EXTENSION else "")

            # 记录已存在、版本一致、且文件未动 → 只刷新派生字段，跳过重解析
            if old is not None and skip_unchanged:
                same_ver = (meta.get("version") or "") == (old.get("version") or "")
                fingerprint = scan.metadata_fingerprint(entry["abs"], kind)
                same_meta = old.get("metadata_fingerprint") == fingerprint
                if same_ver and same_meta and not old.get("missing"):
                    module = old.get("module") or _module_for(entry["abs"], kind, pkg_id)
                    enabled = bridge.is_module_enabled(module)
                    changed = False
                    if enabled != bool(old.get("enabled")) or "module" not in old:
                        old["module"] = module
                        old["enabled"] = enabled
                        changed = True
                    # 兼容信息是派生字段，老记录可能缺失 → 补算
                    if "blender_max" not in old or "compat" not in old:
                        old["blender_max"] = meta.get("blender_max", "")
                        _c = scan.blender_compat(meta.get("blender_min", ""),
                                                 old["blender_max"])
                        old["compat"], old["compat_detail"] = _c
                        changed = True
                    if changed:
                        stats["updated"] += 1
                    else:
                        stats["unchanged"] += 1
                    continue

            module = _module_for(entry["abs"], kind, pkg_id)
            # 已有记录（非新增）时跳过 manifest 合规重校验，显著加快批量同步
            rec = _build_record(root, entry["abs"], kind, meta, old=old, module=module,
                                check_issues=(old is None))
            if old is None:
                stats["added"] += 1
            else:
                stats["updated"] += 1
            db.upsert(key, rec)

    for key, rec in list(db.plugins.items()):
        if key not in seen:
            if not rec.get("missing"):
                rec["missing"] = True
                stats["missing"] += 1
        elif rec.get("missing"):
            rec["missing"] = False

    db.save()
    return stats


# ---------------------------------------------------------------------------
# 移除
# ---------------------------------------------------------------------------
def remove_plugin(root: str, db: LibraryDB, key: str, to_trash: bool = True) -> str | None:
    rec = db.get(key)
    if not rec:
        return None
    try:
        plugin_dir = str(resolve_record_path(root, rec["rel"], rec.get("kind", "addon")))
    except UnsafeLibraryPathError:
        return None
    moved_to = None
    if os.path.isdir(plugin_dir):
        if to_trash:
            trash = os.path.join(root, C.DIR_TRASH)
            os.makedirs(trash, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target = os.path.join(trash, f"{sanitize_name(rec.get('folder_name') or key)}__{stamp}")
            try:
                shutil.move(plugin_dir, target)
                moved_to = target
            except Exception:
                # A failed move must never become an irreversible delete.  Keep
                # both the source and the DB record so the user can retry.
                return None
        else:
            shutil.rmtree(plugin_dir, ignore_errors=True)
    # 若是扩展插件，尽量同步从仓库缓存中移除
    bridge.refresh_blender(deep=True)
    db.remove(key)
    db.save()
    return moved_to


# ---------------------------------------------------------------------------
# 自启同步：把「自启标记」应用为 Blender 实际启用状态
# ---------------------------------------------------------------------------
def apply_startup(root: str, db: LibraryDB, enable_marked: bool = True,
                  disable_unmarked: bool = False) -> dict:
    """按库中的自启标记同步 Blender 的插件启用状态。

    * enable_marked   ：把标记为「自启」的插件启用（默认开启）
    * disable_unmarked：把未标记自启的插件停用（默认关闭，需显式开启）

    默认只做「确保自启的插件是启用的」，不会主动停用任何插件——这样即使你
    还没整理好自启标记，也不会有插件被意外关掉。
    """
    stats = {"enabled": 0, "disabled": 0, "failed": [], "skipped": 0}
    # 一次性取得当前已启用集合，避免逐个插件遍历 preferences.addons
    enabled_set = bridge.enabled_modules()
    for key, rec in list(db.plugins.items()):
        module = rec.get("module")
        if not module or rec.get("missing"):
            stats["skipped"] += 1
            continue
        want = bool(rec.get("startup"))
        if want and enable_marked:
            if module in enabled_set:
                continue
            ok, err = bridge.set_enabled(module, True)
            if ok:
                enabled_set.add(module)
            if ok:
                rec["enabled"] = True
                rec["last_error"] = ""
                stats["enabled"] += 1
            else:
                rec["last_error"] = err
                stats["failed"].append({"module": module, "name": rec.get("name"), "error": err})
        elif (not want) and disable_unmarked:
            if module not in enabled_set:
                continue
            ok, err = bridge.set_enabled(module, False)
            if ok:
                enabled_set.discard(module)
                rec["enabled"] = False
                stats["disabled"] += 1
            else:
                rec["last_error"] = err
                stats["failed"].append({"module": module, "name": rec.get("name"), "error": err})
    db.save()
    return stats


# ---------------------------------------------------------------------------
# 用压缩包替换库中已有插件（用于在线更新）
# ---------------------------------------------------------------------------
def replace_from_zip(rec: dict, zip_path: str, root: str, db: LibraryDB,
                     version: str = "", defer_refresh: bool = False) -> dict:
    """用 zip 里的内容替换库中已有插件，保留该记录的元数据与启用状态。

    保留原目录名（也就是扩展插件的模块名），避免破坏启用状态与外部引用。

    defer_refresh=True 时不在每次替换后立刻刷新 Blender（批量更新时用，
    由调用方在全部完成后统一刷新一次），可避免 N 次刷新造成的长时间卡顿。
    """
    try:
        target = str(resolve_record_path(root, rec["rel"], rec.get("kind", "addon")))
    except UnsafeLibraryPathError as exc:
        raise ValueError(f"不安全的插件路径: {rec.get('rel', '')}") from exc
    if not os.path.isdir(target):
        raise ValueError(f"库中找不到插件目录: {rec['rel']}")

    info = scan.inspect_zip(zip_path)
    if not info:
        raise ValueError("压缩包中没有可识别的插件")

    was_enabled = bridge.is_module_enabled(rec.get("module", ""))
    module = rec.get("module", "")
    if was_enabled:
        ok, err = bridge.set_enabled(module, False)
        if not ok:
            raise RuntimeError(f"更新前停用失败: {err}")

    tmp = tempfile.mkdtemp(prefix="pm_replace_")
    try:
        extract_archive(zip_path, tmp)
        src = os.path.join(tmp, info["prefix"]) if info["prefix"] else tmp

        # 在临时目录中验证完整内容后再交换，失败时保留旧版本。
        _replace_tree_transactional(src, target, root, rec.get("kind") or info["kind"])
    except Exception:
        if was_enabled:
            bridge.set_enabled(module, True)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 重新读取元数据，更新记录中源自插件的字段（用户字段一律保留）
    kind = scan.detect_kind(target) or rec.get("kind")
    meta = scan.read_meta(target, kind) or {}
    rec["kind"] = kind
    rec["name"] = meta.get("name") or rec.get("name") or rec.get("folder_name")
    rec["version"] = scan.version_str(version) or meta.get("version") or rec.get("version")
    rec["blender_min"] = meta.get("blender_min", rec.get("blender_min", ""))
    rec["blender_max"] = meta.get("blender_max", rec.get("blender_max", ""))
    _c = scan.blender_compat(rec["blender_min"], rec["blender_max"])
    rec["compat"], rec["compat_detail"] = _c
    rec["author"] = meta.get("author", rec.get("author", ""))
    rec["description"] = meta.get("description", rec.get("description", ""))
    rec["auto_category"] = meta.get("category", rec.get("auto_category", ""))
    rec["auto_tags"] = meta.get("tags", rec.get("auto_tags", []))
    rec["metadata_fingerprint"] = scan.metadata_fingerprint(target, kind)
    if kind == C.KIND_EXTENSION:
        rec["issues"] = scan.manifest_issues(target)
    rec["update_available"] = False
    rec["latest_version"] = ""
    rec["latest_url"] = ""
    rec["last_error"] = ""
    rec["enabled"] = was_enabled
    rec["updated_at"] = now_iso()
    db.upsert(rec["key"], rec)
    if defer_refresh:
        return rec
    db.save()

    bridge.refresh_blender()
    if was_enabled and module:
        # 更新后重新启用（旧模块可能已失效，需重新加载）
        ok, err = bridge.set_enabled(module, True)
        rec["enabled"] = bridge.is_module_enabled(module)
        rec["restore_error"] = "" if ok else (err or "更新后重新启用失败")
        db.upsert(rec["key"], rec)
        db.save()
    return rec
