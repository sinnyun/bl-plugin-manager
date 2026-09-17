"""实测 Cats：启用失败 → 检测残留 → 清理 → 再检测。"""
import importlib, io, json, sys
import bpy
import bl_plugin_manager as PM
for n in ("constants","scan","db","bridge","library","items","preferences","operators","ui"):
    try: importlib.reload(getattr(PM, n))
    except Exception: pass
LIB = r"D:\nastongbu\qitaziliao\blender_addons"
out = {}
MOD = "cats-blender-plugin-master"

out["residue_before"] = PM.bridge.module_residue(MOD)
out["enabled_before"] = PM.bridge.is_module_enabled(MOD)

# 尝试启用（预期失败，因为 Cats 不兼容 5.2）
ok, err = PM.bridge.set_enabled(MOD, True)
out["enable_ok"] = ok
out["enable_err"] = (err or "")[:170]
out["residue_after_enable"] = PM.bridge.module_residue(MOD)
out["enabled_after_enable"] = PM.bridge.is_module_enabled(MOD)

# 显式清理
left, cerr = PM.bridge.cleanup_residue(MOD)
out["residue_after_cleanup"] = left
out["cleanup_err"] = (cerr or "")[:120]
out["enabled_after_cleanup"] = PM.bridge.is_module_enabled(MOD)
# mmd 面板是否还残留
out["mmd_panels_left"] = sorted([c for c in dir(bpy.types) if "MMD" in c and "PT" in c])[:8]

print("@@TC@@" + json.dumps(out, ensure_ascii=False, default=str))
