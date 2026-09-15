"""精确核对 备份用 文件夹：区分「真插件 / 非插件」并判定与库/官方商店的关系。"""
import json
import os
import re

OLD = r"D:\nastongbu\qitaziliao\blender"
BAK = os.path.join(OLD, "chajian", "addons", "备份用")
LIB = r"D:\nastongbu\qitaziliao\blender_addons"
USER_EXT = r"C:\Users\sanliD\AppData\Roaming\Blender Foundation\Blender\5.2\extensions"
HERE = os.path.dirname(os.path.abspath(__file__))


def info(p):
    """返回 dict 或 None（非插件）。"""
    man = os.path.join(p, "blender_manifest.toml")
    if os.path.isfile(man):
        t = open(man, encoding="utf-8", errors="replace").read()
        g = lambda k: (re.search(rf'^\s*{k}\s*=\s*["\']([^"\']+)', t, re.M) or [None, ""])[1]
        return {"kind": "extension", "id": g("id") or os.path.basename(p),
                "name": g("name") or "", "version": g("version") or ""}
    init = os.path.join(p, "__init__.py")
    if os.path.isfile(init):
        t = open(init, encoding="utf-8", errors="replace").read(60000)
        if "bl_info" not in t:
            return None
        n = re.search(r'["\']name["\']\s*:\s*["\']([^"\']*)', t)
        v = re.search(r'["\']version["\']\s*:\s*\(([^)]*)\)', t)
        return {"kind": "addon", "id": os.path.basename(p),
                "name": n.group(1) if n else "", "version": ".".join(re.findall(r"\d+", v.group(1))) if v else ""}
    # 单文件插件
    return None


def collect_ids(base):
    ids, folders, names = set(), set(), set()
    if not os.path.isdir(base):
        return ids, folders, names
    for e in os.scandir(base):
        if e.is_dir() and not e.name.startswith("."):
            d = info(e.path)
            if d:
                ids.add(d["id"].lower())
                folders.add(e.name.lower())
                if d["name"]:
                    names.add(d["name"].strip().lower())
    return ids, folders, names


lib_ids, lib_folders, lib_names = collect_ids(os.path.join(LIB, "addons"))
i2, f2, n2 = collect_ids(os.path.join(LIB, "extensions"))
lib_ids |= i2; lib_folders |= f2; lib_names |= n2

off_ids, off_folders, off_names = collect_ids(os.path.join(USER_EXT, "blender_org"))
print(f"库: {len(lib_folders)} folders / 官方商店: {len(off_folders)} folders")

# 只扫 备份用 的直接子目录（平坦集合）
items = []
for e in sorted(os.scandir(BAK), key=lambda x: x.name.lower()):
    if not e.is_dir() or e.name.startswith("."):
        continue
    d = info(e.path)
    if d is None:
        items.append({"folder": e.name, "kind": None})
        continue
    d["folder"] = e.name
    items.append(d)

real = [x for x in items if x.get("kind")]
non = [x for x in items if not x.get("kind")]

print(f"\n备份用 子目录: {len(items)}  其中插件={len(real)}  非插件={len(non)}")


def where(d):
    pid, f, n = d["id"].lower(), d["folder"].lower(), (d.get("name") or "").strip().lower()
    if pid in lib_ids or f in lib_folders or (n and n in lib_names):
        return "库中已有"
    if pid in off_ids or f in off_folders or (n and n in off_names):
        return "官方商店已有"
    return "仅备份独有"


groups = {"库中已有": [], "官方商店已有": [], "仅备份独有": []}
for d in real:
    groups[where(d)].append(d)

for k in ("库中已有", "官方商店已有", "仅备份独有"):
    print(f"\n===== {k}: {len(groups[k])} =====")
    for d in groups[k]:
        print(f"   [{d['kind']:9s}] {d['name'][:34]:36s} v{d['version']:12s} {d['folder']}")

out = {"groups": {k: v for k, v in groups.items()}, "non_plugins": non}
with open(os.path.join(HERE, "audit_backup.json"), "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2)
print("\n-> _migrate/audit_backup.json")
