"""汇总审计结果。"""
import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OLD = r"D:\nastongbu\qitaziliao\blender"

r = json.load(open(os.path.join(HERE, "audit_old.json"), encoding="utf-8"))
dirs = r["old_plugin_dirs"]
matched = [d for d in dirs if d["in_library_by"]]
unmatched = [d for d in dirs if not d["in_library_by"]]

print(f"库规模: {r['lib_counts']}")
print(f"A) 旧目录插件目录: {len(dirs)}  已入库={len(matched)}  未入库={len(unmatched)}")
print()
print("未入库的按顶层目录:")
c = Counter()
for d in unmatched:
    rel = os.path.relpath(d["path"], OLD)
    c[rel.split(os.sep)[0]] += 1
for k, v in c.most_common():
    print(f"   {v:3d}  {k}")
print()
print("未入库清单（名称 | 类型 | 相对路径）:")
for d in sorted(unmatched, key=lambda x: x["path"].lower()):
    rel = os.path.relpath(d["path"], OLD)
    print(f"   {d['name'][:38]:40s} [{d['kind']:9s}] {rel}")
print()
print(f"B) zip: {len(r['old_zips'])}")
for z in r["old_zips"]:
    rel = os.path.relpath(z["path"], OLD)
    print(f"   {'✓' if z['matched'] else '?'} {z['mb']:8.1f}MB  {rel}")
