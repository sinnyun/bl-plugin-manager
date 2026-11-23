"""插件库操作：导入、归档、备份、同步、移除。"""

from __future__ import annotations

import os
import json
import re
import shutil
import tempfile
import zipfile
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import bridge, constants as C, scan
from .db import LibraryDB, clear_load_result, now_iso
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


@contextmanager
def _replace_tree_transactional(src: str, dest: str, root: str, kind: str):
    """Stage and atomically swap plugin contents, retaining a rollback path."""
    backup = backup_plugin(root, dest)
    if not backup:
        raise RuntimeError("无法创建更新前备份，已取消替换")
    parent = os.path.dirname(dest)
    stage = tempfile.mkdtemp(prefix=".pm_stage_", dir=parent)
    old = os.path.join(parent, ".pm_old_" + uuid.uuid4().hex)
    try:
        _copy_tree_into(src, stage)
        valid_kind, reason = scan.validate_plugin(stage)
        if valid_kind != kind:
            raise ValueError(reason or "更新包类型与现有插件不一致")
        shutil.move(dest, old)
        try:
            os.rename(stage, dest)
            yield
        except Exception:
            if os.path.exists(dest):
                os.rename(dest, stage)
            os.rename(old, dest)
            raise
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _refresh_existing(rec: dict, source: str, dest: str, root: str, db: LibraryDB,
                      *, move: bool, enable: bool) -> dict:
    if os.path.normcase(os.path.realpath(source)) == os.path.normcase(os.path.realpath(dest)):
        raise ValueError("该插件已在目标目录中")
    result = _replace_installed(rec, source, dest, root, db, enable=enable, archive_source="")
    if move:
        shutil.rmtree(source, ignore_errors=True)
    return result


def backup_plugin(root: str, plugin_dir: str) -> str | None:
    """把插件目录打包成 zip 存入 <库>/.pm/backups/。"""
    backups = os.path.join(root, C.DIR_BACKUPS)
    os.makedirs(backups, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = sanitize_name(os.path.basename(plugin_dir))
    zip_path = os.path.join(backups, f"{safe}__{stamp}_{uuid.uuid4().hex[:8]}.zip")
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
    db.merge_scan([entry], complete=False, persist=False)
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
                                             move=move, enable=enable)

    dest = _unique_dest(base, folder)

    with tempfile.TemporaryDirectory(prefix=".pm_import_", dir=base) as staging:
        staged = os.path.join(staging, "contents")
        shutil.copytree(plugin_dir, staged)
        if os.path.exists(dest):
            raise FileExistsError("安装目标已被占用")
        os.rename(staged, dest)
        try:
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
        except Exception:
            os.rename(dest, staged)
            db.load()
            raise
    if move:
        shutil.rmtree(plugin_dir, ignore_errors=True)

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
               origin: str = "zip", origin_path: str = "", *, expected_id: str = "",
               expected_version: str = "") -> dict:
    info = scan.inspect_zip(zip_path)
    if not info:
        raise ValueError("压缩包中没有可识别的插件（缺少 manifest 或 __init__.py）")
    tmp = tempfile.mkdtemp(prefix="pm_import_")
    try:
        extract_archive(zip_path, tmp)
        src = os.path.join(tmp, info["prefix"]) if info["prefix"] else tmp
        if expected_id or expected_version:
            meta = scan.read_meta(src, C.KIND_EXTENSION) or {}
            if expected_id and meta.get("id") != expected_id:
                raise ValueError("下载包的扩展 ID 与预览不一致")
            if expected_version and scan.compare_versions(meta.get("version"), expected_version) != 0:
                raise ValueError("下载包版本与预览不一致")
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
    info["is_library"] = any(os.path.isfile(os.path.join(path, C.DIR_META, name))
                             for name in (C.DB_FILENAME, C.LEGACY_DB_FILENAME))
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


def target_error(info: dict) -> str:
    if info["path"] and os.path.exists(info["path"]) and not info["exists"]:
        return "所选路径不是目录"
    if info["is_plugin"]:
        return "所选目录本身就是一个插件，请选择它的上一级目录作为插件库"
    if info["exists"] and not (info["is_library"] or info["is_empty"] or info["flat_plugins"]):
        return "该目录非空且其中没有可识别的插件；请选择空目录或已有插件库"
    return ""


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

    if error := target_error(info):
        result["error"] = error
        return result

    if not info["exists"]:
        bridge.ensure_library_dirs(root)
        result["created"] = True
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
    journal = None
    module = rec.get("module", "")
    was_enabled = bool(module and bridge.is_module_enabled(module))
    if os.path.isdir(plugin_dir):
        if to_trash:
            trash = os.path.join(root, C.DIR_TRASH)
            os.makedirs(trash, exist_ok=True)
            if Path(trash).resolve().parent != Path(root).resolve():
                raise ValueError("回收站目录不在插件库内")
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"{sanitize_name(rec.get('folder_name') or key)}__{stamp}_{uuid.uuid4().hex[:8]}"
            target = os.path.join(trash, name)
            journal = Path(trash) / (name + ".json")
            if was_enabled:
                ok, error = bridge.set_enabled(module, False)
                if not ok:
                    raise RuntimeError(f"移除前停用失败: {error}")
            try:
                journal.write_text(json.dumps({
                    "rel": rec["rel"], "kind": rec.get("kind", "addon"),
                    "name": effective_name(rec), "startup": bool(rec.get("startup")),
                }, ensure_ascii=False), encoding="utf-8")
                shutil.move(plugin_dir, target)
                moved_to = target
            except Exception:
                # A failed move must never become an irreversible delete.  Keep
                # both the source and the DB record so the user can retry.
                journal.unlink(missing_ok=True)
                if was_enabled:
                    bridge.set_enabled(module, True)
                return None
        else:
            shutil.rmtree(plugin_dir, ignore_errors=True)
    # 若是扩展插件，尽量同步从仓库缓存中移除
    bridge.refresh_blender(deep=True)
    # 只解除本机绑定；总资料库条目保留（别名/分类/备注属于所有电脑，
    # 该插件在别的电脑上仍然安装着，重新安装到本机时也会自动连回）。
    try:
        db.remove(key)
        db.save()
    except Exception:
        if moved_to:
            shutil.move(moved_to, plugin_dir)
            journal.unlink(missing_ok=True)
            bridge.refresh_blender(deep=True)
            if was_enabled:
                bridge.set_enabled(module, True)
        db.load()
        raise
    return moved_to


def trash_entries(root: str) -> dict[str, dict]:
    trash = Path(root, C.DIR_TRASH)
    if not trash.is_dir() or trash.resolve().parent != Path(root).resolve():
        return {}
    entries = {}
    for journal in sorted(trash.glob("*.json")):
        try:
            source = (trash / journal.stem).resolve()
            if source.parent != trash.resolve() or not source.is_dir():
                continue
            entry = json.loads(journal.read_text(encoding="utf-8"))
            if not isinstance(entry, dict):
                continue
            resolve_record_path(root, entry.get("rel"), entry.get("kind"))
        except (OSError, ValueError):
            continue
        entries[journal.stem] = entry
    return entries


def restore_plugin(root: str, db: LibraryDB, trash_id: str) -> dict:
    entry = trash_entries(root).get(trash_id)
    if not entry:
        raise ValueError("回收站条目已不存在")
    trash = Path(root, C.DIR_TRASH)
    source = trash / trash_id
    dest = resolve_record_path(root, entry["rel"], entry["kind"])
    if dest.exists():
        raise ValueError("原安装路径已被占用，请先移动现有插件")
    kind, reason = scan.validate_plugin(str(source))
    if kind != entry["kind"]:
        raise ValueError(reason or "回收站条目的插件类型不匹配")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    try:
        meta = scan.read_meta(str(dest), kind) or {}
        record = _build_entry(root, str(dest), kind, meta)
        key = _link_entry(db, record)
        result = db.get(key)
        result.update(module=record["module"], startup=bool(entry.get("startup")))
        db.save()
    except Exception:
        shutil.move(str(dest), str(source))
        db.load()
        raise
    (trash / (trash_id + ".json")).unlink(missing_ok=True)
    bridge.refresh_blender(deep=True)
    return result


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
                rec["startup_override"] = True
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
                rec["startup_override"] = False
                stats["disabled"] += 1
            else:
                rec["last_error"] = err
                stats["failed"].append({"module": module, "name": rec.get("name"), "error": err})
    db.save()
    return stats


# ---------------------------------------------------------------------------
# 用压缩包替换库中已有插件（用于在线更新）
# ---------------------------------------------------------------------------
def _replace_installed(rec: dict, source: str, target: str, root: str, db: LibraryDB,
                       *, version: str = "", enable: bool = False, archive_source: str | None = None) -> dict:
    kind = rec.get("kind") or scan.detect_kind(target)
    metadata = scan.read_meta(source, kind) or {}
    expected = rec.get("pkg_id") or rec.get("id")
    if kind == C.KIND_EXTENSION and expected and metadata.get("id") != expected:
        raise ValueError("更新包的扩展 ID 与已安装插件不一致")
    if version and scan.compare_versions(metadata.get("version"), version) != 0:
        raise ValueError("更新包版本与预览不一致")
    module = rec.get("module", "")
    was_enabled = bool(module and bridge.is_module_enabled(module))
    if was_enabled:
        ok, error = bridge.set_enabled(module, False)
        if not ok:
            raise RuntimeError(f"更新前停用失败: {error}")

    plugin_id = rec["plugin_id"]
    try:
        with _replace_tree_transactional(source, target, root, kind):
            refreshed = dict(rec)
            refreshed.update(_build_entry(root, target, kind, metadata, module=module))
            clear_load_result(refreshed)
            refreshed.update(enabled=False, update_available=False, latest_version="",
                             latest_url="", last_error="", updated_at=now_iso())
            if archive_source is not None:
                refreshed["archive_source"] = archive_source
            refreshed["compat"], refreshed["compat_detail"] = scan.blender_compat(
                refreshed["blender_min"], refreshed["blender_max"])
            bridge.refresh_blender(deep=True)
            try:
                if (was_enabled or enable) and module:
                    ok, error = bridge.set_enabled(module, True)
                    if not ok:
                        raise RuntimeError(f"新版加载失败: {error}")
                    refreshed["enabled"] = True
                db.plugins[plugin_id] = refreshed
                db.save()
            except Exception:
                if module and bridge.is_module_enabled(module):
                    bridge.set_enabled(module, False)
                raise
    except Exception as exc:
        db.load()
        bridge.refresh_blender(deep=True)
        if was_enabled:
            ok, error = bridge.set_enabled(module, True)
            if not ok:
                raise RuntimeError(f"{exc}；已恢复原文件，但原版重新启用失败: {error}") from exc
        raise
    return refreshed


def replace_from_zip(rec: dict, zip_path: str, root: str, db: LibraryDB,
                     version: str = "", archive_source: str = "") -> dict:
    """Validate, replace and reload while the original installation remains recoverable."""
    target = str(resolve_record_path(root, rec["rel"], rec.get("kind", "addon")))
    if not os.path.isdir(target):
        raise ValueError(f"库中找不到插件目录: {rec['rel']}")
    info = scan.inspect_zip(zip_path)
    if not info:
        raise ValueError("压缩包中没有可识别的插件")
    with tempfile.TemporaryDirectory(prefix="pm_replace_") as temporary:
        extract_archive(zip_path, temporary)
        source = os.path.join(temporary, info["prefix"]) if info["prefix"] else temporary
        return _replace_installed(rec, source, target, root, db,
                                  version=version, archive_source=archive_source)
