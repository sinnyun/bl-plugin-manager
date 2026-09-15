import json
import os

import bpy

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
WRONG = r"C:\Users\sanliD\BlenderPluginLibrary"
prefs = bpy.context.preferences
log = {}

# 1) 指向真实库（会触发同步）
p = prefs.addons["bl_plugin_manager"].preferences
p.library_path = LIB
log["library_path"] = p.library_path

# 2) 移除错误的 C 盘脚本目录
before_sd = [i.directory for i in prefs.filepaths.script_directories]
for i in list(prefs.filepaths.script_directories):
    if os.path.normcase(i.directory).startswith(os.path.normcase(WRONG)):
        prefs.filepaths.script_directories.remove(i)
log["script_dirs_removed"] = [d for d in before_sd
                              if os.path.normcase(d).startswith(os.path.normcase(WRONG))]

# 3) 移除 pmlib 后重新挂载到正确目录
for r in list(prefs.extensions.repos):
    if getattr(r, "module", "") == "pmlib":
        prefs.extensions.repos.remove(r)
from bl_plugin_manager import bridge
log["remounted"] = bridge.register_library(LIB, save=False)

# 4) 清理多余/失效的本地仓库
KEEP = {"blender_org", "user_default", "system", "pmlib"}
removed = []
for r in list(prefs.extensions.repos):
    m = getattr(r, "module", "")
    d = getattr(r, "custom_directory", "") or getattr(r, "directory", "")
    if m in KEEP:
        continue
    if os.path.normcase(d).startswith(os.path.normcase(WRONG)):
        removed.append(m)
        prefs.extensions.repos.remove(r)
    elif m in ("www_blenderkit_com", "chajian_001", "addons"):
        removed.append(m)
        prefs.extensions.repos.remove(r)
log["repos_removed"] = removed

bridge.refresh_blender()
bpy.ops.wm.save_userpref()
log["saved"] = True

# 5) 复核
log["script_dirs_now"] = [i.directory for i in prefs.filepaths.script_directories]
log["repos_now"] = [(r.module, getattr(r, "custom_directory", "") or getattr(r, "directory", ""))
                    for r in prefs.extensions.repos]
from bl_plugin_manager import db
log["library_records"] = len(db.LibraryDB(LIB).plugins)
log["library_state"] = bridge.library_state(LIB)

print(json.dumps(log, ensure_ascii=False, default=str))
