"""打印完整审计结果。"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(HERE, "audit_backup.json"), encoding="utf-8"))

for k in ("库中已有", "官方商店已有", "仅备份独有"):
    v = d["groups"][k]
    print(f"\n===== {k} ({len(v)}) =====")
    for x in v:
        print(f"  [{x['kind']:9s}] {x['name'][:36]:38s} v{x['version']:12s} {x['folder']}")

print(f"\n===== 非插件项 ({len(d['non_plugins'])}) =====")
for x in d["non_plugins"]:
    print("  ", x["folder"])
