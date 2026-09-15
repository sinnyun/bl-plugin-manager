"""查明残留类的真实 __module__，据此修正匹配规则。"""
import json, bpy
from collections import Counter
import bl_plugin_manager as PM

out = {}
mods = Counter()
samples = []
for name in dir(bpy.types):
    cls = getattr(bpy.types, name, None)
    m = getattr(cls, "__module__", "") or ""
    if "mmd" in m.lower() or "cats" in m.lower():
        mods[m] += 1
        if len(samples) < 12:
            samples.append((name, m))
out["modules"] = dict(mods.most_common(15))
out["samples"] = samples
out["residue_for_cats"] = PM.bridge.module_residue("cats-blender-plugin-master")
out["residue_for_mmd_local"] = PM.bridge.module_residue("mmd_tools_local")
print("@@IR@@" + json.dumps(out, ensure_ascii=False, default=str))
