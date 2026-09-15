"""在运行实例中加载新代码（含 header 模块）并验证新功能。"""
import importlib
import io
import json
import sys

import bpy

out = {}
# 重载全部子模块（含新 header）
import bl_plugin_manager as PM
for name in ("constants", "scan", "db", "bridge", "library", "updates", "migrate",
             "watcher", "items", "preferences", "operators", "ui", "header"):
    mod = getattr(PM, name, None)
    if mod is None:
        try:
            mod = importlib.import_module(f"bl_plugin_manager.{name}")
            setattr(PM, name, mod)
            out[f"import_{name}"] = "ok"
        except Exception as e:
            out[f"import_{name}"] = f"ERR {e}"
        continue
    try:
        importlib.reload(mod)
        out[f"reload_{name}"] = "ok"
    except Exception as e:
        out[f"reload_{name}"] = f"ERR {e}"

# 重新注册类
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
    # header 注册
    try:
        PM.header.unregister()
    except Exception:
        pass
    PM.header.register()
    out["header_registered"] = True
except Exception as e:
    out["header_registered"] = f"ERR {e}"
finally:
    sys.stdout, sys.stderr = _o, _e

# 新操作符是否可用
for opname in ("set_startup", "apply_startup", "toggle_select", "select_all",
               "batch_enable", "batch_set_category", "batch_set_startup", "header_popup"):
    out[f"op_{opname}"] = hasattr(bpy.ops.plugin_manager, opname)

# Header 绘制回调是否已挂到 VIEW3D_HT_header
try:
    from bl_plugin_manager import header
    # Blender 的 _dyn_ui_initialize 返回 (draw_funcs, draw_funcs_py)
    funcs = bpy.types.VIEW3D_HT_header._dyn_ui_initialize()
    flat = []
    for f in funcs:
        if isinstance(f, (list, tuple)):
            flat.extend(f)
        else:
            flat.append(f)
    out["header_draw_hooked"] = any(getattr(f, "__name__", "") == "_draw_header" for f in flat)
except Exception as e:
    out["header_draw_hooked"] = f"ERR {e}"

# 测试 header 弹窗真实绘制
LIB = r"D:\nastongbu\qitaziliao\blender_addons"
prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
prefs.library_path = LIB
from bl_plugin_manager import items
items.rebuild_items(prefs)
out["items"] = len(prefs.plugin_items)
out["categories"] = [it.name for it in prefs.category_items][:12]

# 弹出框 draw（模拟 layout）
class L:
    def __init__(self): self.n = 0
    def __getattr__(self, k):
        def m(*a, **kw):
            self.n += 1
            return L()
        return m

try:
    op = PM.header.PM_OT_header_popup
    inst = op()
    inst.category = "全部"
    lay = L()
    op.draw(inst, type("S", (), {"layout": lay})())
    out["popup_draw_calls"] = lay.n
    out["popup_draw"] = "ok"
except Exception as e:
    out["popup_draw"] = f"ERR {e}"

print("@@HDR@@" + json.dumps(out, ensure_ascii=False, default=str))
