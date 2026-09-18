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
    plugin_id = rec["plugin_id"]
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
    db.plugins[plugin_id] = rec
    db.save()

    bridge.refresh_blender()
    if was_enabled and rec.get("module"):
        ok, err = bridge.set_enabled(rec["module"], True)
        rec["enabled"] = bridge.is_module_enabled(rec["module"])
        rec["restore_error"] = "" if ok else (err or "更新后重新启用失败")
        db.plugins[plugin_id] = rec
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
def _build_entry(root: str, plugin_dir: str, kind: str, meta: dict,
                 module: str | None = None, check_issues: bool = True) -> dict:
    """构造一条**扫描条目**（不是最终记录）。

    条目只携带可与总资料库对齐的信息；``plugin_id`` 由 ``link_scan`` 依据身份
    线索匹配或新建，因此导入路径不会自己编造 id。
    """
    folder = os.path.basename(plugin_dir)
    pkg_id = meta.get("id") or ""
    if module is None:
        module = bridge.resolve_module(plugin_dir, kind, pkg_id)
    entry = {
        "rel": rel_key(root, plugin_dir),
        "kind": kind,
        "pkg_id": pkg_id,
        "id": pkg_id,
        "name": meta.get("name") or folder,
        "folder_name": folder,
        "version": meta.get("version", ""),
        "blender_min": meta.get("blender_min", ""),
        "blender_max": meta.get("blender_max", ""),
        "pkg_type": meta.get("type", ""),
        "author": meta.get("author", ""),
        "description": meta.get("description", ""),
        "auto_category": meta.get("category", "") or C.DEFAULT_CATEGORY,
        "auto_tags": meta.get("tags", []) or [],
        "doc_url": meta.get("doc_url", ""),
        "location": meta.get("location", ""),
        "metadata_fingerprint": scan.metadata_fingerprint(plugin_dir, kind),
        "issues": (scan.manifest_issues(plugin_dir)
                   if (check_issues and kind == C.KIND_EXTENSION) else []),
        "module": module,
    }
    return entry


def _link_entry(db: LibraryDB, entry: dict) -> str:
    """把单条扫描条目并入总资料库，返回匹配/新建的 plugin_id。"""
    db.merge_scan([entry])
    plugin_id = db.assignments.get(entry.get("rel", ""))
    if not plugin_id:
        for pid, rel in db.bindings.items():
            if rel == entry.get("rel"):
                plugin_id = pid
                break
    if not plugin_id:
        raise RuntimeError("插件未能并入总资料库")
    return plugin_id


def _apply_user_defaults(db: LibraryDB, plugin_id: str, entry: dict,
                         origin: str, origin_path: str) -> dict:
    """为新并入的条目补齐用户字段默认值（仅在字段缺失时）。"""
    from .db import now_iso as _now

    record = db.get(plugin_id) or {}
    changed = False
    defaults = {
        "category": C.DEFAULT_CATEGORY,
        "tags": list(entry.get("auto_tags") or []),
        "note": entry.get("description", ""),
        "favorite": False,
        "display_name": "",
        "startup": False,
        "origin": origin,
        "origin_path": origin_path,
        "created_at": record.get("created_at") or _now(),
    }
    for field, value in defaults.items():
        # 用户字段必须始终存在（即便值为空），界面与调用方按字段读取。
        if record.get(field) in (None, ""):
            record[field] = value
            changed = True
    if changed:
        db.plugins[plugin_id] = record
    return record



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

    # 目标已存在（同名覆盖）时先备份再清理
    if os.path.exists(dest):
        backup_plugin(root, dest)
        shutil.rmtree(dest, ignore_errors=True)

    if move:
        shutil.move(plugin_dir, dest)
    else:
        shutil.copytree(plugin_dir, dest)

    entry = _build_entry(root, dest, kind, meta)
    # 匹配可能连回既有条目，也可能新建（本机已有同一插件的另一个副本时）。
    plugin_id = _link_entry(db, entry)
    # 目标目录已按库内命名规范落位，覆盖本机绑定，避免沿用导入源的路径。
    # 用户字段默认值写完后必须重新取回记录：_apply_user_defaults 更新的是
    # db.plugins 中的那份，持有旧引用再回写会把补齐的字段覆盖掉。
    _apply_user_defaults(db, plugin_id, entry, origin, origin_path)
    record = db.get(plugin_id) or {}
    record["rel"] = entry["rel"]
    record["folder_name"] = entry["folder_name"]
    record["module"] = entry["module"]
    record["metadata_fingerprint"] = entry["metadata_fingerprint"]
    record["issues"] = entry["issues"]
    record["missing"] = False
    # compat 是相对当前 Blender 版本的结论，属本机派生字段（界面每次现算），
    # 这里一并写入，便于调用方直接读取导入结果。
    record["compat"], record["compat_detail"] = scan.blender_compat(
        record.get("blender_min", ""), record.get("blender_max", ""))
    record["enabled"] = bridge.is_module_enabled(record.get("module", ""))
    db.plugins[plugin_id] = record
    db.save()

    bridge.refresh_blender(deep=True)
    if enable:
        stored = db.get(plugin_id) or record
        bridge.set_enabled(stored.get("module", ""), True)
        stored["enabled"] = bridge.is_module_enabled(stored.get("module", ""))
        db.plugins[plugin_id] = stored
        db.save()
        return stored
    return db.get(plugin_id) or record


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
    """扫描库内容，把本机安装的插件连接到总资料库。

    记录以稳定 plugin_id 为键；本机实际安装位置保存在 ``db.bindings`` 中。
    已存在且元数据未变的插件只刷新本机派生字段（模块名、启用状态），跳过
    manifest 重解析与写盘。

    性能要点：模块索引只解析一次并复用（原先每个插件都全量遍历一次，
    152 个约 8 秒）。
    """
    entries = []
    runtime_modules: dict[str, str] = {}
    module_index = bridge._module_index()
    active_repo = bridge._active_repo_module()

    for kind, base in ((C.KIND_ADDON, os.path.join(root, C.DIR_ADDONS)),
                       (C.KIND_EXTENSION, os.path.join(root, C.DIR_EXTENSIONS))):
        for entry in scan.scan_dir(base, kind_hint=kind):
            if not entry["valid"]:
                continue
            meta = entry["meta"] or {}
            folder = os.path.basename(os.path.normpath(entry["abs"]))
            key = rel_key(root, entry["abs"])
            module = module_index.get(bridge._norm(entry["abs"]))
            if not module:
                module = (f"bl_ext.{active_repo}.{folder}"
                          if kind == C.KIND_EXTENSION else folder)
            runtime_modules[key] = module
            entries.append({
                "rel": key,
                "kind": kind,
                "pkg_id": meta.get("id", ""),
                "id": meta.get("id", ""),
                # scan_dir 的 entry["name"] 是**目录名**；插件声明名在 meta 中。
                "name": meta.get("name") or folder,
                "folder_name": folder,
                "version": meta.get("version", ""),
                "blender_min": meta.get("blender_min", ""),
                "blender_max": meta.get("blender_max", ""),
                "pkg_type": meta.get("type", ""),
                "author": meta.get("author", ""),
                "description": meta.get("description", ""),
                "auto_category": meta.get("category", "") or C.DEFAULT_CATEGORY,
                "auto_tags": meta.get("tags", []) or [],
                "doc_url": meta.get("doc_url", ""),
                "location": meta.get("location", ""),
                "metadata_fingerprint": scan.metadata_fingerprint(entry["abs"], kind),
                "issues": (scan.manifest_issues(entry["abs"])
                           if kind == C.KIND_EXTENSION else []),
            })

    db.merge_scan(entries)
    stats = dict(db.scan_stats or {})
    stats.setdefault("added", 0)
    stats.setdefault("updated", 0)
    stats.setdefault("unchanged", 0)
    stats.setdefault("missing", 0)
    stats["bindings"] = len(db.bindings)

    # 本机派生字段：模块名、启用状态、缺失标记。写入本机运行状态，不进总资料库。
    for record in db.plugins.values():
        record["compat"], record["compat_detail"] = scan.blender_compat(
            record.get("blender_min", ""), record.get("blender_max", ""))
        module = runtime_modules.get(record.get("rel", ""))
        if module:
            record["module"] = module
            record["enabled"] = bridge.is_module_enabled(module)
            record["missing"] = False
        else:
            # 本机这次没扫描到：保留既有启用意图，只标记缺失。
            record["missing"] = True
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
    # 只解除本机绑定；总资料库条目保留（别名/分类/备注属于所有电脑，
    # 该插件在别的电脑上仍然安装着，重新安装到本机时也会自动连回）。
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
    db.plugins[rec["plugin_id"]] = rec
    if defer_refresh:
        return rec
    db.save()

    bridge.refresh_blender()
    if was_enabled and module:
        # 更新后重新启用（旧模块可能已失效，需重新加载）
        ok, err = bridge.set_enabled(module, True)
        rec["enabled"] = bridge.is_module_enabled(module)
        rec["restore_error"] = "" if ok else (err or "更新后重新启用失败")
        db.plugins[rec["plugin_id"]] = rec
        db.save()
    return rec
