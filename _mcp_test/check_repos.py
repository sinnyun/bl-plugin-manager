import json
import os

import bpy

prefs = bpy.context.preferences
out = {"repos": []}
for r in prefs.extensions.repos:
    out["repos"].append({
        "module": r.module, "use_custom": r.use_custom_directory,
        "dir": getattr(r, "directory", ""),
        "remote": getattr(r, "remote_url", ""),
    })
cfg = bpy.utils.user_resource("CONFIG")
off = os.path.join(os.path.dirname(cfg), "extensions", "blender_org")
lib = r"D:\nastongbu\qitaziliao\blender_addons\extensions"


def pkgs(d):
    if not os.path.isdir(d):
        return []
    return sorted(e.name for e in os.scandir(d) if e.is_dir() and not e.name.startswith("."))


out["official_pkgs"] = pkgs(off)
out["lib_pkgs"] = pkgs(lib)
out["overlap"] = sorted(set(out["official_pkgs"]) & set(out["lib_pkgs"]))
print("@@REPO@@" + json.dumps(out, ensure_ascii=False))
