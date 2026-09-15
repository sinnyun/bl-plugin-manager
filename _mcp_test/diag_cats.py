"""诊断 Cats/mmd_tools_local 的 poll 报错来源与当前状态。"""
import json

import bpy

out = {}

# 1) Cats 是否启用
prefs = bpy.context.preferences
cats_mods = [a.module for a in prefs.addons if "cats" in a.module.lower()]
out["cats_enabled"] = cats_mods

# 2) mmd_type 属性是否注册
out["mmd_type_on_Object"] = hasattr(bpy.types.Object, "mmd_type")

# 3) 报错涉及的类是否已注册
for cls in ("MMD_TOOLS_PT_view_prop", "OBJECT_PT_mmd_tools_view_prop"):
    out[cls] = hasattr(bpy.types, cls)

# 4) 相关面板类清单（含 poll 的）
panel_classes = [c for c in dir(bpy.types) if "MMD" in c and "PT" in c]
out["mmd_panels_registered"] = panel_classes[:20]

# 5) 当前是否存在 partial-registered 的 mmd 模块
import sys
mmd_mods = [m for m in sys.modules if "mmd_tools" in m]
out["mmd_modules_loaded"] = mmd_mods[:15]

# 6) Cats 记录状态
from bl_plugin_manager import db as pdb

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
d = pdb.LibraryDB(LIB)
for k, r in d.plugins.items():
    if "cats" in (r.get("folder_name") or "").lower() or "cats" in (r.get("name") or "").lower():
        out["cats_record"] = {kk: r.get(kk) for kk in
                              ("name", "module", "load_state", "load_error", "enabled", "last_error")}

# 7) 插件自带的 mmd_tools_local 是否是"精简/剥离版"
import os
cats_dir = None
for k, r in d.plugins.items():
    if "cats" in (r.get("folder_name") or "").lower():
        cats_dir = os.path.join(LIB, r["rel"].replace("/", os.sep))
        break
out["cats_dir"] = cats_dir
if cats_dir:
    ext = os.path.join(cats_dir, "extern_tools", "mmd_tools_local")
    out["has_bundled_mmd"] = os.path.isdir(ext)
    vp = os.path.join(ext, "panels", "view_prop.py")
    out["view_prop_exists"] = os.path.isfile(vp)
    if os.path.isfile(vp):
        with open(vp, "r", encoding="utf-8", errors="replace") as f:
            out["view_prop_head"] = f.read(700)

print("@@CATS@@" + json.dumps(out, ensure_ascii=False, default=str))
