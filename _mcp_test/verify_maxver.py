"""验证：列表右侧显示最高支持版本 + 不兼容标注。"""
import importlib
import io
import json
import sys

import bpy

import bl_plugin_manager as PM

for n in ("constants", "scan", "db", "bridge", "library", "store", "updates",
          "migrate", "watcher", "items", "preferences", "operators", "ui"):
    try:
        importlib.reload(getattr(PM, n))
    except Exception:
        pass

_nul = io.StringIO()
_o, _e = sys.stdout, sys.stderr
sys.stdout = sys.stderr = _nul
try:
    for mod in (PM.preferences, PM.operators, PM.ui):
        for cls in getattr(mod, "classes", ()):
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
            try:
                bpy.utils.register_class(cls)
            except Exception:
                pass
finally:
    sys.stdout, sys.stderr = _o, _e

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
prefs.library_path = LIB
prefs.search = ""
prefs.active_category = "全部"
prefs.only_enabled = prefs.only_updates = prefs.only_favorites = False
prefs.only_incompatible = False
PM.items.rebuild_items(prefs)

cur = ".".join(str(v) for v in bpy.app.version)
rows = []
inc = []
for it in prefs.plugin_items:
    rows.append({"name": (it.display_name or it.name)[:26],
                 "max": it.max_version_text,
                 "compat": it.compat,
                 "load": it.load_state})
    if it.compat in ("too_new", "too_old") or it.load_state == "failed":
        inc.append({"name": (it.display_name or it.name)[:30],
                    "max": it.max_version_text,
                    "compat": it.compat, "load": it.load_state})

out = {"blender": cur, "total": len(rows), "sample": rows[:14],
       "incompatible": inc[:22], "incompatible_count": len(inc)}
print("@@MV@@" + json.dumps(out, ensure_ascii=False))
