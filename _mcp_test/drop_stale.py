import json

from bl_plugin_manager import db as pdb

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
d = pdb.LibraryDB(LIB)
removed = []
for k, r in list(d.plugins.items()):
    if r.get("missing") and (r.get("folder_name") or "") in ("Straighten UV",):
        d.remove(k)
        removed.append(r.get("folder_name"))
d.save()
print(json.dumps({"removed": removed, "records": len(pdb.LibraryDB(LIB).plugins)},
                 ensure_ascii=False))
