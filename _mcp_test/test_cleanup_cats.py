"""验证强制清理能否清掉 Cats 的注册残留。"""
import importlib, io, json, sys
import bpy
import bl_plugin_manager as PM
for n in ("constants","scan","db","bridge","library","items","preferences","operators","ui","header"):
    try: importlib.reload(getattr(PM, n))
    except Exception: pass
out = {}
MOD = "cats-blender-plugin-master"
out["residue_before"] = PM.bridge.module_residue(MOD)
out["mmd_panels_before"] = len([c for c in dir(bpy.types) if "MMD" in c and "PT" in c])
# 强制清理
left, cerr = PM.bridge.cleanup_residue(MOD)
out["residue_after"] = left
out["cleanup_err"] = (cerr or "")[:150]
out["mmd_panels_after"] = len([c for c in dir(bpy.types) if "MMD" in c and "PT" in c])
out["cats_ops_after"] = len([c for c in dir(bpy.types) if c.startswith("CATS_")])
out["mmd_type_still_broken"] = not hasattr(bpy.types.Object, "mmd_type")
print("@@TC2@@" + json.dumps(out, ensure_ascii=False, default=str))
