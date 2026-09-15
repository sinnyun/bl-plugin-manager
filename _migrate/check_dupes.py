"""检查库内重复（按归一化名称）并统计。"""
import os
import re
import unicodedata


LIB = r"D:\nastongbu\qitaziliao\blender_addons"


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s.lower())


def probe(p):
    man = os.path.join(p, "blender_manifest.toml")
    if os.path.isfile(man):
        t = open(man, encoding="utf-8", errors="replace").read()
        g = lambda k: (re.search(rf'^\s*{k}\s*=\s*["\']([^"\']+)', t, re.M) or [None, ""])[1]
        return "extension", g("id") or os.path.basename(p), g("name") or "", g("version") or ""
    init = os.path.join(p, "__init__.py")
    if os.path.isfile(init):
        t = open(init, encoding="utf-8", errors="replace").read(80000)
        if "bl_info" not in t:
            return None
        n = re.search(r'["\']name["\']\s*:\s*["\']([^"\']*)', t)
        v = re.search(r'["\']version["\']\s*:\s*\(([^)]*)\)', t)
        return "addon", os.path.basename(p), (n.group(1) if n else ""), \
               (".".join(re.findall(r"\d+", v.group(1))) if v else "")
    return None


by_name, by_id = {}, {}
count = {"addon": 0, "extension": 0}
for sub in ("addons", "extensions"):
    base = os.path.join(LIB, sub)
    if not os.path.isdir(base):
        continue
    for e in os.scandir(base):
        if not e.is_dir() or e.name.startswith("."):
            continue
        d = probe(e.path)
        if not d:
            print("  [非插件]", sub, e.name)
            continue
        kind, pid, name, ver = d
        count[kind] += 1
        by_name.setdefault(norm(name) or norm(pid), []).append((sub, e.name, name, ver))
        by_id.setdefault(pid.lower(), []).append((sub, e.name, name, ver))

print(f"库内插件: addon={count['addon']} extension={count['extension']} total={sum(count.values())}")

print("\n=== 按名称重复 ===")
for k, v in by_name.items():
    if len(v) > 1:
        print(f"  {k}: {[x[1] for x in v]}")

print("\n=== 按 id 重复 ===")
for k, v in by_id.items():
    if len(v) > 1:
        print(f"  {k}: {[(x[0], x[1]) for x in v]}")
