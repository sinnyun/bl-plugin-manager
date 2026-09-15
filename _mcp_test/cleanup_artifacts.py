"""清理测试产生的痕迹：临时分类、测试备注；并删除误建的 C 盘空库。"""
import json
import os
import shutil

from bl_plugin_manager import db as pdb

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
WRONG = r"C:\Users\sanliD\BlenderPluginLibrary"

d = pdb.LibraryDB(LIB)
report = {}

# 1) 移除测试分类
for c in ("测试分类", "MCP测试"):
    if c in d.categories:
        d.delete_category(c)
        report.setdefault("categories_removed", []).append(c)
report["categories_now"] = list(d.categories)

# 2) 清掉测试备注与测试用的别名
cleared = []
for rec in d.plugins.values():
    if rec.get("note") == "MCP备注":
        rec["note"] = rec.get("description", "")
        cleared.append(rec.get("name"))
    if rec.get("display_name") == "MCP":
        rec["display_name"] = ""
report["notes_restored"] = cleared
d.save()
report["records"] = len(pdb.LibraryDB(LIB).plugins)

# 3) 删除误建的 C 盘空库（仅当它确实为空）
if os.path.isdir(WRONG):
    entries = []
    for root, dirs, files in os.walk(WRONG):
        for fn in files:
            entries.append(os.path.join(root, fn))
    # 只含内部元数据、没有插件目录才算空
    plugin_dirs = []
    for sub in ("addons", "extensions"):
        p = os.path.join(WRONG, sub)
        if os.path.isdir(p):
            plugin_dirs += [e for e in os.listdir(p) if not e.startswith(".")]
    report["wrong_lib_files"] = len(entries)
    report["wrong_lib_plugin_dirs"] = plugin_dirs
    if not plugin_dirs:
        shutil.rmtree(WRONG)
        report["wrong_lib_removed"] = True
    else:
        report["wrong_lib_removed"] = False

print(json.dumps(report, ensure_ascii=False, default=str))
