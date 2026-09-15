"""最终验证：库内容、启用状态、配置、遗留重复项。"""

from __future__ import annotations

import json
import os

import bpy

TARGET = r"D:\nastongbu\qitaziliao\blender_addons"
DB = os.path.join(TARGET, ".pm", "library.json")

with open(DB, "r", encoding="utf-8") as f:
    db = json.load(f)

prefs = bpy.context.preferences
enabled = sorted(a.module for a in prefs.addons)
repos = [(r.module, getattr(r, "custom_directory", "") or r.directory) for r in prefs.extensions.repos]
sds = [i.directory for i in prefs.filepaths.script_directories]

# 库内容
addons = sorted(os.listdir(os.path.join(TARGET, "addons")))
exts = sorted(os.listdir(os.path.join(TARGET, "extensions")))

# 检测重复：同一个插件名/文件夹在两个位置或同位置多名
names = {}
for rec in db["plugins"].values():
    n = (rec.get("name") or "").strip()
    names.setdefault(n, []).append(rec.get("rel"))

dupes = {k: v for k, v in names.items() if len(v) > 1}

# 库内记录 vs 磁盘
disk_count = len(addons) + len(exts)
rec_count = len(db["plugins"])

print("@@ADDONS@@", len(addons), json.dumps(addons, ensure_ascii=False))
print("@@EXTS@@", len(exts), json.dumps(exts, ensure_ascii=False))
print("@@RECORDS@@", rec_count)
print("@@DISK_COUNT@@", disk_count)
print("@@ENABLED_TOTAL@@", len(enabled))
print("@@ENABLED_LIST@@", json.dumps(enabled, ensure_ascii=False))
print("@@REPOS@@", json.dumps(repos, ensure_ascii=False))
print("@@SCRIPT_DIRS@@", json.dumps(sds, ensure_ascii=False))
print("@@DUPES@@", json.dumps(dupes, ensure_ascii=False))

# 库内插件的启用统计
enabled_in_lib = [r for r in db["plugins"].values() if r.get("enabled")]
print("@@LIB_ENABLED@@", len(enabled_in_lib))
cats = {}
for r in db["plugins"].values():
    c = r.get("category") or "未分类"
    cats[c] = cats.get(c, 0) + 1
print("@@CATEGORIES@@", json.dumps(cats, ensure_ascii=False))
notes = sum(1 for r in db["plugins"].values() if r.get("note"))
print("@@WITH_NOTES@@", notes)
