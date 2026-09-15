"""最终核对：旧目录（除 ass/）里每个插件都能在 库 或 官方商店 找到。"""
import os
import re
import unicodedata

OLD = r"D:\nastongbu\qitaziliao\blender"
LIB = r"D:\nastongbu\qitaziliao\blender_addons"
USER_EXT = r"C:\Users\sanliD\AppData\Roaming\Blender Foundation\Blender\5.2\extensions"
SKIP_DIRS = {"__pycache__", ".git", ".svn", ".blender_ext", ".pm", ".local", ".cache",
             "node_modules"}


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s.lower())


def probe(p):
    man = os.path.join(p, "blender_manifest.toml")
    if os.path.isfile(man):
        t = open(man, encoding="utf-8", errors="replace").read()
        g = lambda k: (re.search(rf'^\s*{k}\s*=\s*["\']([^"\']+)', t, re.M) or [None, ""])[1]
        return "extension", g("id") or os.path.basename(p), g("name") or g("id") or os.path.basename(p)
    init = os.path.join(p, "__init__.py")
    if os.path.isfile(init):
        t = open(init, encoding="utf-8", errors="replace").read(80000)
        if "bl_info" not in t:
            return None
        n = re.search(r'["\']name["\']\s*:\s*["\']([^"\']*)', t)
        return "addon", os.path.basename(p), (n.group(1) if n else os.path.basename(p))
    return None


def keys(base, out):
    if not os.path.isdir(base):
        return out
    for e in os.scandir(base):
        if not e.is_dir() or e.name.startswith("."):
            continue
        out.add(norm(e.name))
        d = probe(e.path)
        if d:
            out.add(norm(d[1]))
            out.add(norm(d[2]))
    return out


K = set()
keys(os.path.join(LIB, "addons"), K)
keys(os.path.join(LIB, "extensions"), K)
keys(os.path.join(USER_EXT, "blender_org"), K)
keys(os.path.join(USER_EXT, "user_default"), K)
print("库+商店标识:", len(K))

found_ok, found_missing, non_plugin = [], [], []
counts = {"addon": 0, "extension": 0}

for top in sorted(os.listdir(OLD)):
    if top == "ass":
        continue
    troot = os.path.join(OLD, top)
    if not os.path.isdir(troot):
        continue
    for root, dirs, files in os.walk(troot):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        if root == troot:
            continue
        d = probe(root)
        if d:
            kind, pid, name = d
            counts[kind] += 1
            ok = norm(pid) in K or norm(name) in K or norm(os.path.basename(root)) in K
            (found_ok if ok else found_missing).append((kind, name, root))
            dirs[:] = []

print(f"旧目录插件目录: addon={counts['addon']} extension={counts['extension']}")
print(f"  已找到归属: {len(found_ok)}")
print(f"  未找到: {len(found_missing)}")
for kind, name, p in found_missing:
    print(f"   ! [{kind}] {name}  {os.path.relpath(p, OLD)}")
