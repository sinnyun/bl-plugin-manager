import json
import os

import bpy

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
from bl_plugin_manager import bridge, db, scan

out = {}
prefs = bpy.context.preferences
out["library_path"] = prefs.addons["bl_plugin_manager"].preferences.library_path
out["library_state"] = bridge.library_state(LIB)
d = db.LibraryDB(LIB)
out["records"] = len(d.plugins)
out["addons"] = sum(1 for r in d.plugins.values() if r.get("kind") == "addon")
out["extensions"] = sum(1 for r in d.plugins.values() if r.get("kind") == "extension")
out["missing"] = [r.get("folder_name") for r in d.plugins.values() if r.get("missing")]
out["with_issues"] = {r.get("folder_name"): r.get("issues")
                      for r in d.plugins.values() if r.get("issues")}
out["categories"] = len(d.categories)
out["script_dirs"] = [i.directory for i in prefs.filepaths.script_directories]
out["repos"] = [(r.module, getattr(r, "custom_directory", "") or r.directory)
                for r in prefs.extensions.repos]
out["enabled_count"] = len(prefs.addons)

# 扩展目录名合法性。扩展仓库会在根目录下创建 `.blender_ext` 索引目录，
# 它不是插件包，必须沿用扫描器的忽略规则排除；其它真正的非法模块名仍需报告。
ext_dir = os.path.join(LIB, "extensions")
illegal = [e.name for e in os.scandir(ext_dir)
           if e.is_dir() and not scan.is_ignored(e.name)
           and not scan._MODULE_RE.fullmatch(e.name)]
out["illegal_ext_dirs"] = illegal
out["wrong_c_lib_exists"] = os.path.isdir(r"C:\Users\sanliD\BlenderPluginLibrary")

print("@@FINAL@@" + json.dumps(out, ensure_ascii=False, default=str))
