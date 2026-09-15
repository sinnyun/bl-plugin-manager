import json

import bpy

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
from bl_plugin_manager import bridge, db, library

out = {}
out["reloaded"] = library.sync_library(LIB, db.LibraryDB(LIB))
bridge.refresh_blender()

# Straighten_UV / PlaceHelper 是否可被识别
import addon_utils
mods = {m.__name__ for m in addon_utils.modules()}
out["straighten_found"] = "bl_ext.pmlib.Straighten_UV" in mods
out["placehelper_found"] = "bl_ext.pmlib.PlaceHelper" in mods
out["straighten_mentions"] = [m for m in mods if "Straighten" in m]
out["placehelper_mentions"] = [m for m in mods if "PlaceHelper" in m]

# 记录同步后库里是否还有 missing
d = db.LibraryDB(LIB)
out["records"] = len(d.plugins)
out["missing"] = [r.get("folder_name") for r in d.plugins.values() if r.get("missing")]
out["ext_records"] = sum(1 for r in d.plugins.values() if r.get("kind") == "extension")
out["addon_records"] = sum(1 for r in d.plugins.values() if r.get("kind") == "addon")

print(json.dumps(out, ensure_ascii=False, default=str))
