"""一体化插件迁移：把散落各处的插件统一收敛到目标插件库。

在 Blender 内部以 headless 方式运行（需要读取当前偏好以还原启用状态）：

    blender --background --python do_migrate.py -- --dry     # 只读预演
    blender --background --python do_migrate.py -- --run     # 实际执行

特性：
* manifest 优先分类：有 blender_manifest.toml → 扩展插件；否则 → 传统插件。
  这样同一插件即使被旧配置重复加载（如 chajian/addons 既是脚本目录又是扩展仓库），
  也只会收敛成一份。
* 移动而非复制（D 盘内秒级），并写 journal.json，可用 --rollback 还原。
* 迁移前备份 userpref.blend 与偏好配置。
* 迁移后重写脚本目录/扩展仓库配置，并按原启用状态重新启用。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

import bpy

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
TARGET = r"D:\nastongbu\qitaziliao\blender_addons"
BLENDER_DIR = r"D:\nastongbu\qitaziliao\blender"
HERE = os.path.dirname(os.path.abspath(__file__))
JOURNAL = os.path.join(HERE, "journal.json")
BACKUP_ROOT = os.path.join(HERE, "backups")

# 目标库自己、以及要保留的 Blender 系统仓库（不迁移内容，仅保留索引以支持更新检测）
PROTECTED_REPO_MODULES = {"system"}
# 保持由 Blender 自行管理、不迁入插件库的仓库（用户选择「只统一自装插件」）：
#   blender_org  = 官方扩展商店（保留一键更新）
#   user_default = BlenderKit（162MB，自带独立更新器）
KEEP_REPO_MODULES = {"blender_org", "user_default", "pmlib"}
OFFICIAL_REPO_MODULE = "blender_org"

EXCLUDE_DIR = {"__pycache__", ".git", ".svn", ".blender_ext", ".pm", ".local", ".cache",
               "node_modules", "backups"}
EXCLUDE_NAME = {"备份用", "备份", "addons", "bl_plugin_manager"}  # 管理器本身留作引导器，不迁移

# 迁移范围开关
INCLUDE_USER_ADDONS = True       # 用户配置 scripts/addons
INCLUDE_LOCAL_REPOS = True       # 用户配置 extensions/ 下的本地仓库
INCLUDE_OFFICIAL_REPO = False    # blender_org 官方商店扩展（默认保留由 Blender 管理）
OFFICIAL_INCLUDE_IDS: set[str] = set()


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def log(*a):
    print("[MIGRATE]", *a)


def norm(p):
    return os.path.normcase(os.path.abspath(p or ""))


def has_manifest(p):
    return os.path.isfile(os.path.join(p, "blender_manifest.toml"))


def has_bl_info(p):
    init = os.path.join(p, "__init__.py")
    if not os.path.isfile(init):
        return False
    try:
        with open(init, "r", encoding="utf-8", errors="replace") as f:
            return "bl_info" in f.read(40000)
    except OSError:
        return False


def classify(p):
    """manifest 优先。"""
    if has_manifest(p):
        # manifest 但内容无效时退化为传统插件
        return "extension"
    if has_bl_info(p):
        return "addon"
    return None


def read_manifest_id(p):
    mp = os.path.join(p, "blender_manifest.toml")
    try:
        with open(mp, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    import re

    m = re.search(r'^\s*id\s*=\s*["\']([^"\']+)', text, re.M)
    return m.group(1) if m else None


def read_name_version(p, kind):
    if kind == "extension":
        mp = os.path.join(p, "blender_manifest.toml")
        try:
            import re

            text = open(mp, encoding="utf-8", errors="replace").read()
            nm = re.search(r'^\s*name\s*=\s*["\']([^"\']+)', text, re.M)
            vr = re.search(r'^\s*version\s*=\s*["\']([^"\']+)', text, re.M)
            return (nm.group(1) if nm else ""), (vr.group(1) if vr else "")
        except OSError:
            return "", ""
    init = os.path.join(p, "__init__.py")
    try:
        import re

        text = open(init, encoding="utf-8", errors="replace").read(40000)
        nm = re.search(r'["\']name["\']\s*:\s*["\']([^"\']*)', text)
        vr = re.search(r'["\']version["\']\s*:\s*\(([^)]*)\)', text)
        ver = ".".join(re.findall(r"\d+", vr.group(1))) if vr else ""
        return (nm.group(1) if nm else ""), ver
    except OSError:
        return "", ""


def module_path_map():
    """module_name -> 插件目录（normcase）。"""
    import addon_utils

    out = {}
    for mod in addon_utils.modules():
        f = getattr(mod, "__file__", None)
        if f:
            out[mod.__name__] = norm(os.path.dirname(f))
    return out


def enabled_modules():
    return {a.module for a in bpy.context.preferences.addons}


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------
def collect_sources():
    """返回 {normcase(path): entry}。"""
    found: dict[str, dict] = {}
    modmap = module_path_map()
    enabled = enabled_modules()

    def add_dir(path, container_kind, origin, posix_repo=None):
        p = norm(path)
        if p in found or not os.path.isdir(path):
            return
        name = os.path.basename(path)
        if name in EXCLUDE_DIR or name in EXCLUDE_NAME or name.startswith("."):
            return
        kind = classify(path)
        if kind is None:
            return
        pkg_id = (read_manifest_id(path) or name) if kind == "extension" else name
        # 判断原启用状态
        was_enabled = False
        for m, mp in modmap.items():
            if mp == p and m in enabled:
                was_enabled = True
                break
        nm, ver = read_name_version(path, kind)
        found[p] = {
            "src": path,
            "name": name,
            "display": nm or name,
            "version": ver,
            "kind": kind,
            "id": pkg_id,
            "origin": origin,
            "was_enabled": was_enabled,
        }

    # A) 当前脚本目录：其 addons/ 子目录 → 传统插件；根下子目录也按分类判定
    for item in bpy.context.preferences.filepaths.script_directories:
        base = item.directory
        addons_sub = os.path.join(base, "addons")
        if os.path.isdir(addons_sub):
            for e in os.scandir(addons_sub):
                if e.is_dir():
                    add_dir(e.path, "addon", f"脚本目录[{item.name}]")

    # B) 本地扩展仓库（用户自定义目录）
    for repo in bpy.context.preferences.extensions.repos:
        module = getattr(repo, "module", "")
        if module in PROTECTED_REPO_MODULES or module in KEEP_REPO_MODULES:
            continue
        directory = getattr(repo, "custom_directory", "") or getattr(repo, "directory", "")
        if not directory or not os.path.isdir(directory):
            continue
        is_official = module == OFFICIAL_REPO_MODULE
        if is_official and not INCLUDE_OFFICIAL_REPO:
            continue
        # 避免把脚本目录的 addons 子目录当仓库重复扫（靠 norm(path) 去重即可）
        for e in os.scandir(directory):
            if e.is_dir() and not e.name.startswith("."):
                if is_official and e.name not in OFFICIAL_INCLUDE_IDS and OFFICIAL_INCLUDE_IDS:
                    continue
                add_dir(e.path, "extension", f"扩展仓库[{getattr(repo,'name',module)}]")

    # C) 用户配置 scripts/addons
    if INCLUDE_USER_ADDONS:
        for sub in bpy.utils.script_paths(subdir="addons"):
            # 只处理用户配置目录（排除 Blender 安装目录）
            if "Blender Foundation" not in sub and "blender_addons" not in sub:
                continue
            if not os.path.isdir(sub):
                continue
            if norm(sub).startswith(norm(TARGET)):
                continue
            for e in os.scandir(sub):
                if e.is_dir():
                    add_dir(e.path, "addon", "用户配置 scripts/addons")

    return found


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------
def build_plan(entries: dict) -> dict:
    plan = {"moves": [], "duplicates": [], "conflicts": []}
    by_key: dict[tuple, dict] = {}
    dest_used: set[str] = set()

    # 目标现有内容
    for sub in ("addons", "extensions"):
        d = os.path.join(TARGET, sub)
        if os.path.isdir(d):
            for e in os.scandir(d):
                if e.is_dir():
                    dest_used.add(norm(e.path))

    for ent in sorted(entries.values(), key=lambda x: x["src"].lower()):
        key = (ent["kind"], (ent["id"] or ent["name"]).lower())
        if key in by_key:
            plan["duplicates"].append({
                "kept": by_key[key]["src"], "skipped": ent["src"],
                "kind": ent["kind"], "id": ent["id"],
                "skipped_was_enabled": ent["was_enabled"],
                "kept_was_enabled": by_key[key]["was_enabled"],
            })
            # 若被跳过的那份是启用的，把启用状态合并到保留项
            if ent["was_enabled"]:
                by_key[key]["was_enabled"] = True
            continue

        base = os.path.join(TARGET, "extensions" if ent["kind"] == "extension" else "addons")
        folder = ent["id"] if ent["kind"] == "extension" else ent["name"]
        dest = os.path.join(base, folder)
        n = 2
        while norm(dest) in dest_used or os.path.exists(dest):
            dest = os.path.join(base, f"{folder}_{n}")
            n += 1
            if n > 50:
                break
        if os.path.exists(dest):
            plan["conflicts"].append({"src": ent["src"], "dest": dest})
            continue
        dest_used.add(norm(dest))
        ent["dest"] = dest
        by_key[key] = ent
        plan["moves"].append(ent)

    plan["summary"] = {
        "target": TARGET,
        "moves": len(plan["moves"]),
        "addons": sum(1 for m in plan["moves"] if m["kind"] == "addon"),
        "extensions": sum(1 for m in plan["moves"] if m["kind"] == "extension"),
        "duplicates": len(plan["duplicates"]),
        "conflicts": len(plan["conflicts"]),
        "enabled_to_restore": sum(1 for m in plan["moves"] if m["was_enabled"]),
    }
    return plan


# ---------------------------------------------------------------------------
# 备份
# ---------------------------------------------------------------------------
def backup_prefs():
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_ROOT, stamp)
    os.makedirs(dest, exist_ok=True)
    cfg = bpy.utils.user_resource("CONFIG")
    saved = []
    for fn in ("userpref.blend",):
        src = os.path.join(cfg, fn)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest, fn))
            saved.append(fn)
    # 记录当前配置快照，便于人工核对
    snap = {
        "script_directories": [i.directory for i in bpy.context.preferences.filepaths.script_directories],
        "repos": [
            {"name": r.name, "module": r.module, "source": r.source,
             "custom_directory": getattr(r, "custom_directory", ""),
             "enabled": r.enabled}
            for r in bpy.context.preferences.extensions.repos
        ],
        "enabled_addons": sorted(enabled_modules()),
    }
    with open(os.path.join(dest, "config_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    return dest


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
def execute(plan, dry=True):
    log(f"{'[DRY] ' if dry else ''}开始执行 {len(plan['moves'])} 个移动")
    journal = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "target": TARGET, "moves": []}
    ok = fail = 0
    for m in plan["moves"]:
        src, dst = m["src"], m["dest"]
        if dry:
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            journal["moves"].append({
                "src": src, "dst": dst, "kind": m["kind"],
                "id": m["id"], "was_enabled": m["was_enabled"],
            })
            ok += 1
        except Exception as exc:
            log(f"移动失败 {src}: {exc}")
            fail += 1
    if not dry:
        with open(JOURNAL, "w", encoding="utf-8") as f:
            json.dump(journal, f, ensure_ascii=False, indent=2)
    log(f"{'[DRY] ' if dry else ''}完成: 成功 {ok}, 失败 {fail}")
    return journal


def rewrite_prefs(plan, journal, dry=True):
    """移除旧挂载，注册新库，并还原启用状态。"""
    prefs = bpy.context.preferences
    moves = journal["moves"] if not dry else plan["moves"]

    # 收集旧来源目录（用于移除脚本目录/旧仓库）
    old_script_dirs = {norm(i.directory) for i in prefs.filepaths.script_directories}
    lib_root = norm(TARGET)
    keep_script = []
    for i in list(prefs.filepaths.script_directories):
        if norm(i.directory) in old_script_dirs and norm(i.directory) != lib_root:
            keep_script.append(i)
    if dry:
        log(f"[DRY] 将移除 {len(keep_script)} 个旧脚本目录",
            [i.directory for i in keep_script])
    else:
        for i in list(keep_script):
            try:
                prefs.filepaths.script_directories.remove(i)
            except Exception as exc:
                log("移除脚本目录失败:", exc)

    # 移除旧本地扩展仓库（保留 system / 官方商店 / BlenderKit / pmlib）
    remove_repos = []
    for r in prefs.extensions.repos:
        module = getattr(r, "module", "")
        if module in PROTECTED_REPO_MODULES or module in KEEP_REPO_MODULES:
            continue
        directory = getattr(r, "custom_directory", "") or getattr(r, "directory", "")
        if directory and (norm(directory) == norm(os.path.join(TARGET, "extensions"))
                          or norm(directory) == lib_root):
            continue
        remove_repos.append(r)
    if dry:
        log(f"[DRY] 将移除 {len(remove_repos)} 个旧扩展仓库",
            [getattr(r, "module", "?") for r in remove_repos])
    else:
        for r in list(remove_repos):
            try:
                prefs.extensions.repos.remove(r)
            except Exception as exc:
                log("移除仓库失败:", exc)

    if dry:
        return

    # 注册新库
    import bl_plugin_manager as PM

    PM.bridge.ensure_library_dirs(TARGET)
    state = PM.bridge.register_library(TARGET, save=False)
    log("库挂载状态:", state)

    # 还原启用状态
    restored = 0
    for m in moves:
        if not m.get("was_enabled"):
            continue
        dst = m["dst"]
        if m["kind"] == "extension":
            mod = f"bl_ext.pmlib.{m['id']}"
        else:
            mod = os.path.basename(dst)
        ok, err = PM.bridge.set_enabled(mod, True)
        if ok:
            restored += 1
        else:
            log(f"启用失败 {mod}: {err}")
    log(f"已还原 {restored} 个插件的启用状态")
    PM.bridge.save_prefs()


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    dry = "--dry" in argv or "--run" not in argv

    log(f"目标库: {TARGET}")
    entries = collect_sources()
    log(f"采集到 {len(entries)} 个插件目录")
    plan = build_plan(entries)
    log("计划汇总:", json.dumps(plan["summary"], ensure_ascii=False))

    with open(os.path.join(HERE, "migration_plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    if dry:
        print("=== 待迁移（前 50）===")
        for m in plan["moves"][:50]:
            flag = "★启用" if m["was_enabled"] else ""
            print(f"  [{m['kind']:9s}] {m['name']:<40s} → {os.path.relpath(m['dest'], TARGET)}  {flag}")
        print(f"\n重复跳过 {len(plan['duplicates'])}, 冲突 {len(plan['conflicts'])}")
        execute(plan, dry=True)
        rewrite_prefs(plan, {"moves": []}, dry=True)
        return

    if bpy.app.background is False:
        log("警告：建议在无界面模式下运行")
    bdir = backup_prefs()
    log("已备份偏好到:", bdir)
    journal = execute(plan, dry=False)
    rewrite_prefs(plan, journal, dry=False)
    log("迁移完成。请重启 Blender 后检查插件列表。")


main()
