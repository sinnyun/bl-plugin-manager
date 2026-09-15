import json
import os
import re

import addon_utils
import bpy

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
ADD = os.path.join(LIB, "addons")

mods = {m.__name__ for m in addon_utils.modules()}
enabled = {a.module for a in bpy.context.preferences.addons}

bad_names = []
found_ok = []
for e in sorted(os.scandir(ADD), key=lambda x: x.name.lower()):
    if not e.is_dir() or e.name.startswith("."):
        continue
    # 传统插件模块名 = 目录名，必须可作为标识符导入
    legal = bool(re.fullmatch(r"[^\W\d]\w*", e.name, re.UNICODE))
    if not legal:
        bad_names.append({"dir": e.name, "discovered": e.name in mods,
                          "enabled": e.name in enabled})
    else:
        found_ok.append(e.name)

print(json.dumps({
    "addons_total": len(found_ok) + len(bad_names),
    "illegal_dir_names": bad_names,
    "illegal_count": len(bad_names),
}, ensure_ascii=False, default=str))
