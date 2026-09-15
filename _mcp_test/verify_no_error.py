"""验证清理后界面重绘不再报错。"""
import io, json, sys
import bpy
from bl_plugin_manager import bridge as BR

out = {}
buf = io.StringIO()
old_out, old_err = sys.stdout, sys.stderr
sys.stdout = sys.stderr = buf
try:
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
    out["redraw"] = "done"
except Exception as e:
    out["redraw"] = f"EXC {e}"
finally:
    sys.stdout, sys.stderr = old_out, old_err

txt = buf.getvalue()
# 是否还有 mmd_type / Cats 相关报错
out["has_traceback"] = "Traceback" in txt
out["mmd_type_error"] = "mmd_type" in txt
out["cats_error"] = "cats" in txt.lower()
errs = [l for l in txt.splitlines() if any(k in l for k in
        ("Traceback", "mmd_type", "findRoot", "Error"))]
out["error_lines"] = errs[:10]
out["residue"] = BR.module_residue("cats-blender-plugin-master")
out["cats_enabled"] = BR.is_module_enabled("cats-blender-plugin-master")
print("@@VE@@" + json.dumps(out, ensure_ascii=False, default=str))
