"""在运行实例中清理自动分类：把插件自带分类收回未分类。"""
import json

import bpy

from bl_plugin_manager import db as pdb

LIB = r"D:\nastongbu\qitaziliao\blender_addons"

d = pdb.LibraryDB(LIB)
before = {}
for r in d.plugins.values():
    c = r.get("category") or "未分类"
    before[c] = before.get(c, 0) + 1

res = d.reset_auto_categories()
d.save()

d2 = pdb.LibraryDB(LIB)
after = {}
for r in d2.plugins.values():
    c = r.get("category") or "未分类"
    after[c] = after.get(c, 0) + 1

print("@@CLEAN@@" + json.dumps({
    "before_categories": len(before),
    "after_categories": len(after),
    "after_counts": after,
    "result": res,
    "records": len(d2.plugins),
}, ensure_ascii=False))
