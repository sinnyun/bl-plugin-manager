"""检查旧目录所有 zip 的内容与库的对应关系。"""
import os
import re
import zipfile

OLD = r"D:\nastongbu\qitaziliao\blender"
LIB = r"D:\nastongbu\qitaziliao\blender_addons"


def ids_of(base):
    s = set()
    if not os.path.isdir(base):
        return s
    for e in os.scandir(base):
        if e.is_dir() and not e.name.startswith("."):
            s.add(e.name.lower())
            m = os.path.join(e.path, "blender_manifest.toml")
            if os.path.isfile(m):
                t = open(m, encoding="utf-8", errors="replace").read()
                mm = re.search(r'^\s*id\s*=\s*["\']([^"\']+)', t, re.M)
                if mm:
                    s.add(mm.group(1).lower())
    return s


lib = ids_of(os.path.join(LIB, "addons")) | ids_of(os.path.join(LIB, "extensions"))
print("库标识数:", len(lib))
print("\n=== 旧目录所有 zip ===")

for top in os.listdir(OLD):
    if top == "ass":
        continue
    troot = os.path.join(OLD, top)
    if not os.path.isdir(troot):
        continue
    for root, dirs, files in os.walk(troot):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", ".blender_ext")]
        for fn in files:
            if not fn.lower().endswith(".zip"):
                continue
            p = os.path.join(root, fn)
            try:
                with zipfile.ZipFile(p) as z:
                    names = [n.replace("\\", "/") for n in z.namelist() if not n.endswith("/")]
            except Exception as e:
                print("  [坏zip]", fn, e)
                continue
            roots = sorted({n.split("/")[0] for n in names})
            has_plug = any(n.endswith("blender_manifest.toml") for n in names) or any(
                n.endswith("__init__.py") for n in names
            )
            mb = round(os.path.getsize(p) / 1048576, 1)
            hit = [r for r in roots if r.lower() in lib]
            rel = os.path.relpath(p, OLD)
            print(f"  {mb:7.1f}MB {'插件包' if has_plug else '非插件'} | 根={roots[:5]} | 命中={hit} | {rel}")
