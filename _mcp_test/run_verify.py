"""在运行实例中实测所有未启用插件的兼容性（走 verify_compat 的同一逻辑）。"""
import importlib
import io
import json
import sys

import bpy

import bl_plugin_manager as PM

for n in ("scan", "db", "bridge", "library", "store", "updates", "migrate",
          "watcher", "items", "preferences", "operators", "ui"):
    try:
        importlib.reload(getattr(PM, n))
    except Exception:
        pass

# 重载后必须重新注册类，否则新操作符不可用
_nul2 = io.StringIO()
_o2, _e2 = sys.stdout, sys.stderr
sys.stdout = sys.stderr = _nul2
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
    sys.stdout, sys.stderr = _o2, _e2

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
prefs.library_path = LIB

# 用操作符执行（与用户点按钮完全一致）
_nul = io.StringIO()
_o, _e = sys.stdout, sys.stderr
sys.stdout = sys.stderr = _nul
try:
    res = bpy.ops.plugin_manager.verify_compat(include_enabled=False)
except Exception as e:
    res = f"EXC {e}"
finally:
    sys.stdout, sys.stderr = _o, _e

d = PM.db.LibraryDB(LIB)
ok = [r.get("name") for r in d.plugins.values() if r.get("load_state") == "ok"]
bad = [{"name": r.get("name"), "error": (r.get("load_error") or "")[:110]}
       for r in d.plugins.values() if r.get("load_state") == "failed"]
print("@@VC@@" + json.dumps({
    "op_result": list(res) if isinstance(res, set) else str(res),
    "tested_ok": len(ok),
    "failed": bad,
    "failed_count": len(bad),
    "report_summary": prefs.report_summary,
    "records": len(d.plugins),
    "has_op": hasattr(bpy.ops.plugin_manager, "verify_compat"),
}, ensure_ascii=False, default=str))
