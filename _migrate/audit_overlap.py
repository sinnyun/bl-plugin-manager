"""交叉核对：备份插件 vs 新库 / 官方商店 / BlenderKit，判断哪些是真正缺失的。"""
import json
import os
import re

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
USER_EXT = r"C:\Users\sanliD\AppData\Roaming\Blender Foundation\Blender\5.2\extensions"
HERE = os.path.dirname(os.path.abspath(__file__))


def ident(p):
    """返回 (kind, id, folder, name)。"""
    man = os.path.join(p, "blender_manifest.toml")
    if os.path.isfile(man):
        t = open(man, encoding="utf-8", errors="replace").read()
        m = re.search(r'^\s*id\s*=\s*["\']([^"\']+)', t, re.M)
        n = re.search(r'^\s*name\s*=\s*["\']([^"\']+)', t, re.M)
        return "extension", (m.group(1) if m else os.path.basename(p)), os.path.basename(p), (n.group(1) if n else "")
    init = os.path.join(p, "__init__.py")
    if os.path.isfile(init):
        t = open(init, encoding="utf-8", errors="replace").read(40000)
        m = re.search(r'["\']name["\']\s*:\s*["\']([^"\']*)', t)
        return "addon", os.path.basename(p), os.path.basename(p), (m.group(1) if m else "")
    return None


def collect(base, label):
    out = {"ids": set(), "folders": set(), "names": set()}
    if not os.path.isdir(base):
        return out
    for e in os.scandir(base):
        if not e.is_dir() or e.name.startswith("."):
            continue
        d = ident(e.path)
        if not d:
            continue
        _, pid, folder, name = d
        out["ids"].add(pid.lower())
        out["folders"].add(folder.lower())
        if name:
            out["names"].add(name.strip().lower())
    return out


lib_a = collect(os.path.join(LIB, "addons"), "lib/addons")
lib_e = collect(os.path.join(LIB, "extensions"), "lib/ext")
lib = {
    "ids": lib_a["ids"] | lib_e["ids"],
    "folders": lib_a["folders"] | lib_e["folders"],
    "names": lib_a["names"] | lib_e["names"],
}
official = collect(os.path.join(USER_EXT, "blender_org"), "official")
bk = collect(os.path.join(USER_EXT, "user_default"), "blenderkit")

r = json.load(open(os.path.join(HERE, "audit_old.json"), encoding="utf-8"))
unmatched = [d for d in r["old_plugin_dirs"] if not d["in_library_by"]]

groups = {"在官方商店已有": [], "在库中已有": [], "仅备份中存在": []}
for d in unmatched:
    pid = (d["id"] or "").lower()
    folder = d["folder"].lower()
    name = (d["name"] or "").strip().lower()
    if pid in official["ids"] or folder in official["folders"] or (name and name in official["names"]):
        groups["在官方商店已有"].append(d)
    elif pid in lib["ids"] or folder in lib["folders"] or (name and name in lib["names"]):
        groups["在库中已有"].append(d)
    else:
        groups["仅备份中存在"].append(d)

for k, v in groups.items():
    print(f"== {k}: {len(v)} ==")
    for d in sorted(v, key=lambda x: x["path"].lower()):
        rel = os.path.relpath(d["path"], r"D:\nastongbu\qitaziliao\blender")
        print(f"   [{d['kind']:9s}] {d['name'][:36]:38s} {rel}")
    print()

print("合计:", {k: len(v) for k, v in groups.items()})
