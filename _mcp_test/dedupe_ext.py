"""清理同一 id 的重复扩展副本（保留最早那条，其余移入回收站）。"""
import json
import os
import shutil
from datetime import datetime

import bpy

from bl_plugin_manager import db as pdb

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
d = pdb.LibraryDB(LIB)

# 按 id 分组（仅扩展插件）
groups = {}
for k, r in d.plugins.items():
    if r.get("kind") != "extension":
        continue
    pid = str(r.get("id") or "").lower()
    if pid:
        groups.setdefault(pid, []).append((k, r))

removed = []
trash = os.path.join(LIB, "trash")
os.makedirs(trash, exist_ok=True)
for pid, items in groups.items():
    if len(items) < 2:
        continue
    # 保留导入时间最早的一条（原位置），其余移入回收站
    items.sort(key=lambda kv: kv[1].get("imported_at") or kv[1].get("created_at") or "")
    for k, r in items[1:]:
        src = os.path.join(LIB, r["rel"].replace("/", os.sep))
        if os.path.isdir(src):
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = os.path.join(trash, f"{os.path.basename(src)}__dup_{stamp}")
            try:
                shutil.move(src, dst)
            except Exception:
                shutil.rmtree(src, ignore_errors=True)
        d.remove(k)
        removed.append({"id": pid, "removed": r.get("rel"), "kept": items[0][1].get("rel")})

d.save()
from bl_plugin_manager import bridge, library

bridge.refresh_blender()
stats = library.sync_library(LIB, pdb.LibraryDB(LIB))
print(json.dumps({"removed": removed, "sync": stats,
                  "records": len(pdb.LibraryDB(LIB).plugins)}, ensure_ascii=False))
