import json, bpy, sys, os
out = {}
prefs = bpy.context.preferences
# 1) Cats 是否在启用列表
out["cats_in_prefs"] = [a.module for a in prefs.addons if "cats" in a.module.lower()]
# 2) mmd_type 是否注册
out["mmd_type_registered"] = hasattr(bpy.types.Object, "mmd_type")
# 3) 残留的 mmd 面板类
mmd_panels = sorted([c for c in dir(bpy.types) if "MMD" in c and "PT" in c])
out["mmd_panel_classes"] = mmd_panels
# 4) 这些面板所属模块
import bpy.types as T
mods = {}
for c in mmd_panels:
    cls = getattr(T, c, None)
    m = getattr(cls, "__module__", "?")
    mods[c] = m
out["panel_modules"] = mods
# 5) 相关模块是否加载
out["mmd_local_loaded"] = [m for m in sys.modules if "mmd_tools_local" in m]
# 6) addon_support 枚举现状
try:
    props = bpy.context.window_manager.bl_rna.properties["addon_support"]
    out["addon_support_enum"] = [i.identifier for i in props.enum_items]
except Exception as e:
    out["addon_support_enum"] = f"ERR {e}"
print("@@C2@@" + json.dumps(out, ensure_ascii=False, default=str))
