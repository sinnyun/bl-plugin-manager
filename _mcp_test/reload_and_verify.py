"""在运行实例中重载插件管理器模块（应用刚同步的代码），再全面复验。"""
import importlib
import io
import json
import sys

import bpy

out = {}
# 重载各子模块以应用最新代码
import bl_plugin_manager as PM
subs = ["constants", "scan", "db", "bridge", "library", "updates", "migrate",
        "watcher", "items", "preferences", "operators", "ui"]
for name in subs:
    mod = getattr(PM, name, None)
    if mod is not None:
        try:
            importlib.reload(mod)
            out[f"reload_{name}"] = "ok"
        except Exception as e:
            out[f"reload_{name}"] = f"ERR {e}"

# 重载后重新注册类，确保 UI 用新代码（静默，避免刷屏）
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
            bpy.utils.register_class(cls)
    out["reregister"] = "ok"
except Exception as e:
    out["reregister"] = f"ERR {e}"
finally:
    sys.stdout, sys.stderr = _o, _e

# 校验新函数可用
out["has_manifest_issues"] = hasattr(PM.scan, "manifest_issues")
out["has_sanitize_module"] = hasattr(PM.library, "sanitize_module_name")

# 用新校验检查库里所有扩展
LIB = r"D:\nastongbu\qitaziliao\blender_addons"
import os
problems = {}
ext = os.path.join(LIB, "extensions")
for e in sorted(os.scandir(ext), key=lambda x: x.name.lower()):
    if e.is_dir() and not e.name.startswith("."):
        iss = PM.scan.manifest_issues(e.path)
        if iss:
            problems[e.name] = iss
out["extension_issues"] = problems

# 重建列表并重绘
from bl_plugin_manager import items
prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
for w in bpy.context.window_manager.windows:
    for a in w.screen.areas:
        if a.type == "VIEW_3D":
            for sp in a.spaces:
                if sp.type == "VIVE_3D" or sp.type == "VIEW_3D":
                    sp.show_region_ui = True
            for r in a.regions:
                if r.type == "UI":
                    r.active_panel_category = "插件库"

buf = io.StringIO()
o, e2 = sys.stdout, sys.stderr
sys.stdout = buf
sys.stderr = buf
try:
    n = len(items.rebuild_items(prefs))
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
    out["rebuild_count"] = n
    out["redraw"] = "done"
except Exception as ex:
    out["redraw"] = f"EXC {ex!r}"
finally:
    sys.stdout = o
    sys.stderr = e2

txt = buf.getvalue()
out["has_traceback"] = "Traceback" in txt
out["error_lines"] = [ln for ln in txt.splitlines()
                      if any(k in ln for k in ("Traceback", "Error", "Exception", "ui.py", "scan.py"))][:20]

print("@@RELOAD_VERIFY@@" + json.dumps(out, ensure_ascii=False, default=str))
