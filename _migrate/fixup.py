"""迁移收尾：修正配置、清理幽灵条目、收编散装单文件插件。

只做修复，不重复导入（旧目录里的残留经核实都是非插件资料或已入库插件的副本）。
"""

from __future__ import annotations

import os
import shutil

import bpy

TARGET = r"D:\nastongbu\qitaziliao\blender_addons"
# 用户资源根目录：<...>\Blender\5.2 （config / scripts / extensions 都在其下）
USER_ROOT = os.path.dirname(bpy.utils.user_resource("CONFIG"))
USER_ADDONS = os.path.join(USER_ROOT, "scripts", "addons")
OFFICIAL_DIR = os.path.join(USER_ROOT, "extensions", "blender_org")

prefs = bpy.context.preferences

# --- 1) 还原官方商店仓库目录 -------------------------------------------
for r in prefs.extensions.repos:
    if getattr(r, "module", "") == "blender_org":
        cur = getattr(r, "custom_directory", "") or getattr(r, "directory", "")
        if os.path.normcase(cur) != os.path.normcase(OFFICIAL_DIR):
            print("[FIX] blender_org 目录:", cur, "->", OFFICIAL_DIR)
            r.use_custom_directory = False
            r.directory = OFFICIAL_DIR

# --- 2) 移除失效的旧扩展仓库 -------------------------------------------
REMOVE_REPOS = {"addons", "chajian_001", "www_blenderkit_com", "www_blenderkit_com_001",
                "zidingyi_chajian", "gongjuxiaolv_chajian", "caizhixuanran_chajian",
                "donghuaguge_chajian", "wulimoni_chajian", "jianmo_chajian"}
for r in list(prefs.extensions.repos):
    if getattr(r, "module", "") in REMOVE_REPOS:
        print("[FIX] 移除旧仓库:", r.module)
        prefs.extensions.repos.remove(r)

# --- 3) 移除失效的旧脚本目录 -------------------------------------------
for i in list(prefs.filepaths.script_directories):
    d = os.path.normcase(i.directory)
    if "nastongbu" in d and os.path.normcase(TARGET) not in d:
        print("[FIX] 移除旧脚本目录:", i.directory)
        prefs.filepaths.script_directories.remove(i)

# --- 4) 收编散装单文件插件 ---------------------------------------------
os.makedirs(os.path.join(TARGET, "addons"), exist_ok=True)
loose = {
    "blender_mcp.py": "addons",        # 保留（MCP for Blender）
    "addon.py": "trash",               # 与上者内容完全相同，属重复，移入回收站
    "addon.py.bak": "trash",
}
for fn, where in loose.items():
    src = os.path.join(USER_ADDONS, fn)
    if not os.path.isfile(src):
        continue
    dst_dir = os.path.join(TARGET, "addons" if where == "addons" else "trash")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, fn)
    if os.path.exists(dst):
        print("[FIX] 目标已存在，跳过:", fn)
        continue
    shutil.move(src, dst)
    print(f"[FIX] 散装插件 {fn} -> {where}")

# --- 5) 清理幽灵启用项（模块已无法被发现）------------------------------
import addon_utils

discovered = {m.__name__ for m in addon_utils.modules()}
CORE_KEEP = {"bl_pkg"}
ghosts = [a.module for a in prefs.addons
          if a.module not in discovered and a.module not in CORE_KEEP]
print("[FIX] 幽灵启用项:", ghosts)
for mod in ghosts:
    try:
        prefs.addons.remove(prefs.addons[mod])
    except Exception as exc:
        print("[FIX] 移除失败", mod, exc)

# --- 6) 刷新并保存 ------------------------------------------------------
import bl_plugin_manager as PM
from bl_plugin_manager import db as pdb, library

PM.bridge.refresh_blender()
library.sync_library(TARGET, pdb.LibraryDB(TARGET))
PM.bridge.save_prefs()

print("[FIX] 库内插件:", len(pdb.LibraryDB(TARGET).plugins))
print("[FIX] 当前启用:", len(prefs.addons))
sd = [i.directory for i in prefs.filepaths.script_directories]
print("[FIX] 脚本目录:", sd)
print("[FIX] 仓库:", [(r.module, getattr(r, "custom_directory", "") or r.directory) for r in prefs.extensions.repos])
print("[FIX] @@DONE@@")
