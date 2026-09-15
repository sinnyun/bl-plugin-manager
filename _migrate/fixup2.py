"""迁移收尾（精准版）：修正官方仓库目录、清理幽灵启用项、收编散装单文件插件。

避免触发全局 refresh（那会连带禁用/启用一堆插件并引发无关报错）。
"""

from __future__ import annotations

import os
import shutil

import bpy

TARGET = r"D:\nastongbu\qitaziliao\blender_addons"
CONFIG = bpy.utils.user_resource("CONFIG")
USER_ROOT = os.path.dirname(CONFIG)  # <...>\Blender\5.2
USER_ADDONS = os.path.join(USER_ROOT, "scripts", "addons")
OFFICIAL_DIR = os.path.join(USER_ROOT, "extensions", "blender_org")

prefs = bpy.context.preferences
report = {}

# --- 1) 修正官方商店仓库目录（directory 只读，改用 custom_directory 开关）---
for r in prefs.extensions.repos:
    if getattr(r, "module", "") == "blender_org":
        before = getattr(r, "custom_directory", "") or r.directory
        try:
            r.use_custom_directory = False
            r.custom_directory = ""
        except Exception as exc:
            print("[FIX2] 设置失败:", exc)
        after = getattr(r, "custom_directory", "") or r.directory
        report["blender_org"] = {"before": before, "after": after,
                                 "expected": OFFICIAL_DIR,
                                 "ok": os.path.normcase(after) == os.path.normcase(OFFICIAL_DIR)}
        print("[FIX2] blender_org:", before, "->", after)

# --- 2) 移除残留的旧扩展仓库 -------------------------------------------
REMOVE_REPOS = {"addons", "chajian_001", "www_blenderkit_com", "www_blenderkit_com_001",
                "zidingyi_chajian", "gongjuxiaolv_chajian", "caizhixuanran_chajian",
                "donghuaguge_chajian", "wulimoni_chajian", "jianmo_chajian"}
removed_repos = []
for r in list(prefs.extensions.repos):
    if getattr(r, "module", "") in REMOVE_REPOS:
        removed_repos.append(r.module)
        prefs.extensions.repos.remove(r)
report["removed_repos"] = removed_repos

# --- 3) 移除残留的旧脚本目录 -------------------------------------------
removed_sd = []
for i in list(prefs.filepaths.script_directories):
    d = os.path.normcase(i.directory)
    if "nastongbu" in d and os.path.normcase(TARGET) not in d:
        removed_sd.append(i.directory)
        prefs.filepaths.script_directories.remove(i)
report["removed_script_dirs"] = removed_sd

# --- 4) 收编散装单文件插件 ---------------------------------------------
os.makedirs(os.path.join(TARGET, "addons"), exist_ok=True)
os.makedirs(os.path.join(TARGET, "trash"), exist_ok=True)
moved = []
for fn, where in (("blender_mcp.py", "addons"), ("addon.py", "trash"),
                  ("addon.py.bak", "trash")):
    src = os.path.join(USER_ADDONS, fn)
    if not os.path.isfile(src):
        continue
    dst = os.path.join(TARGET, where, fn)
    if os.path.exists(dst):
        continue
    shutil.move(src, dst)
    moved.append(f"{fn}->{where}")
report["loose_moved"] = moved

# 清掉 scripts/addons 下遗留的缓存文件（非插件）
for fn in ("Flow_Libraries.txt",):
    p = os.path.join(USER_ADDONS, fn)
    if os.path.isfile(p) and os.path.getsize(p) == 0:
        try:
            os.remove(p)
        except OSError:
            pass

# --- 5) 清理幽灵启用项（模块已不可被发现的）----------------------------
import addon_utils

discovered = {m.__name__ for m in addon_utils.modules()}
KEEP = {"bl_plugin_manager"}
ghosts = []
for a in list(prefs.addons):
    mod = a.module
    if mod in discovered or mod in KEEP:
        continue
    ghosts.append(mod)
    try:
        prefs.addons.remove(prefs.addons[mod])
    except Exception as exc:
        print("[FIX2] 移除幽灵失败", mod, exc)
report["ghosts_removed"] = ghosts

# --- 6) 同步库并保存（不做全局 refresh）--------------------------------
from bl_plugin_manager import db as pdb, library

library.sync_library(TARGET, pdb.LibraryDB(TARGET))
bpy.ops.wm.save_userpref()

report["library_plugins"] = len(pdb.LibraryDB(TARGET).plugins)
report["enabled_total"] = len(prefs.addons)
report["script_dirs"] = [i.directory for i in prefs.filepaths.script_directories]
report["repos"] = [{"module": r.module,
                    "dir": getattr(r, "custom_directory", "") or r.directory}
                   for r in prefs.extensions.repos]

import json

print("[FIX2] @@REPORT@@" + json.dumps(report, ensure_ascii=False))
