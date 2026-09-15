"""恢复因重建 pmlib 仓库而丢失的 10 个启用状态。"""
import json

import bpy

from bl_plugin_manager import bridge, db

LIB = r"D:\nastongbu\qitaziliao\blender_addons"

# 迁移后原本处于启用状态的 pmlib 扩展（来自迁移 journal 的 was_enabled）
WANT = [
    "lattice_helper", "simple_deform_helper", "extreme_pbr", "auto_rig_pro",
    "auto_rig_pro_quick_rig", "bonex", "auto_reload", "photographer",
    "chinese_text_input_redhalostudio", "looptools",
]

# 确认这些插件都确实在库中
d = db.LibraryDB(LIB)
in_lib = {r.get("folder_name") for r in d.plugins.values() if r.get("kind") == "extension"}

res = {"enabled": [], "failed": [], "not_in_lib": []}
for name in WANT:
    if name not in in_lib:
        res["not_in_lib"].append(name)
        continue
    mod = f"bl_ext.pmlib.{name}"
    ok, err = bridge.set_enabled(mod, True)
    if ok:
        res["enabled"].append(mod)
    else:
        res["failed"].append({"module": mod, "err": err[:100]})

bridge.save_prefs()
res["enabled_total"] = len(bpy.context.preferences.addons)
res["now_enabled_pmlib"] = sorted(
    a.module for a in bpy.context.preferences.addons if a.module.startswith("bl_ext.pmlib.")
)
print("@@RESTORE@@" + json.dumps(res, ensure_ascii=False))
