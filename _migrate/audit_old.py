"""只读核对：把旧目录（除 ass/）里的插件、zip、解压副本逐一对到新库。

输出三张清单：
  A) 旧目录里发现的插件目录 → 是否已在库中
  B) 旧目录里的 zip → 内含插件是否已在库中
  C) 库中插件 → 旧目录是否仍有副本（用于判断能否安全删除）
"""

from __future__ import annotations

import json
import os
import re
import zipfile

OLD = r"D:\nastongbu\qitaziliao\blender"
LIB = r"D:\nastongbu\qitaziliao\blender_addons"
SKIP_TOP = {"ass"}          # 用户明确排除
SKIP_DIRS = {"__pycache__", ".git", ".svn", ".blender_ext", ".pm", ".local", ".cache",
             "node_modules"}

# ---------------------------------------------------------------------------
# 读取新库内容
# ---------------------------------------------------------------------------
def read_manifest_id(p):
    try:
        text = open(os.path.join(p, "blender_manifest.toml"), encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    m = re.search(r'^\s*id\s*=\s*["\']([^"\']+)', text, re.M)
    return m.group(1) if m else None


def read_bl_info_name(p):
    try:
        text = open(os.path.join(p, "__init__.py"), encoding="utf-8", errors="replace").read(40000)
    except OSError:
        return None
    m = re.search(r'["\']name["\']\s*:\s*["\']([^"\']*)', text)
    return m.group(1) if m else None


def read_version(p, kind):
    if kind == "extension":
        try:
            text = open(os.path.join(p, "blender_manifest.toml"), encoding="utf-8", errors="replace").read()
        except OSError:
            return ""
        m = re.search(r'^\s*version\s*=\s*["\']([^"\']+)', text, re.M)
        return m.group(1) if m else ""
    try:
        text = open(os.path.join(p, "__init__.py"), encoding="utf-8", errors="replace").read(40000)
    except OSError:
        return ""
    m = re.search(r'["\']version["\']\s*:\s*\(([^)]*)\)', text)
    return ".".join(re.findall(r"\d+", m.group(1))) if m else ""


def kind_of(p):
    if os.path.isfile(os.path.join(p, "blender_manifest.toml")):
        return "extension"
    if os.path.isfile(os.path.join(p, "__init__.py")):
        return "addon"
    return None


def describe(p):
    k = kind_of(p)
    if not k:
        return None
    ident = (read_manifest_id(p) or os.path.basename(p)) if k == "extension" else os.path.basename(p)
    return {
        "path": p,
        "folder": os.path.basename(p),
        "kind": k,
        "id": ident,
        "name": read_bl_info_name(p) or ident,
        "version": read_version(p, k),
    }


def scan_library():
    lib = {"addons": {}, "extensions": {}, "all_ids": set(), "all_folders": set(),
           "all_names": set()}
    for sub, kind in (("addons", "addon"), ("extensions", "extension")):
        base = os.path.join(LIB, sub)
        if not os.path.isdir(base):
            continue
        for e in sorted(os.scandir(base), key=lambda x: x.name.lower()):
            if not e.is_dir() or e.name.startswith("."):
                continue
            d = describe(e.path)
            if not d:
                # 可能是单文件目录以外的杂物，记录文件夹名即可
                lib["all_folders"].add(e.name.lower())
                continue
            lib[sub][e.name.lower()] = d
            lib["all_ids"].add((d["id"] or "").lower())
            lib["all_folders"].add(e.name.lower())
            if d["name"]:
                lib["all_names"].add(d["name"].strip().lower())
    return lib


# ---------------------------------------------------------------------------
# 扫描旧目录
# ---------------------------------------------------------------------------
def find_in_old():
    plugins, zips, junk_dirs = [], [], []
    for top in sorted(os.listdir(OLD)):
        if top in SKIP_TOP:
            continue
        troot = os.path.join(OLD, top)
        if not os.path.isdir(troot):
            continue
        for root, dirs, files in os.walk(troot):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            # 判定当前目录是否是插件根
            if os.path.basename(root) in SKIP_DIRS or root != troot:
                pass
            d = describe(root)
            if d and root != troot:
                plugins.append(d)
                dirs[:] = []      # 不再深入插件内部
                continue
            for fn in files:
                if fn.lower().endswith(".zip"):
                    zips.append(os.path.join(root, fn))
        # 记录容器内非插件的散装文件
        for root, dirs, files in os.walk(troot):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fn in files:
                p = os.path.join(root, fn)
                if fn.lower().endswith(".zip"):
                    continue
                if fn in ("Y",) or fn.endswith((".txt", ".json", ".jpg", ".png")):
                    junk_dirs.append(p)
    return plugins, zips, junk_dirs


def zip_plugins(zpath):
    """返回 [(内层前缀, kind, id)]。"""
    out = []
    try:
        with zipfile.ZipFile(zpath) as zf:
            names = [n.replace("\\", "/") for n in zf.namelist() if not n.endswith("/")]
    except Exception:
        return out
    roots = []
    for n in names:
        h = n.split("/")[0]
        if h not in roots:
            roots.append(h)
    for prefix in [""] + roots:
        base = (prefix + "/") if prefix else ""
        if base + "blender_manifest.toml" in names:
            out.append((prefix, "extension"))
        elif base + "__init__.py" in names:
            out.append((prefix, "addon"))
    return out


def zip_top_names(zpath):
    try:
        with zipfile.ZipFile(zpath) as zf:
            return sorted({n.replace("\\", "/").split("/")[0] for n in zf.namelist()})
    except Exception:
        return []


# ---------------------------------------------------------------------------
def main():
    lib = scan_library()
    plugins, zips, junk = find_in_old()

    report = {"lib_counts": {"addons": len(lib["addons"]), "extensions": len(lib["extensions"])},
              "old_plugin_dirs": [], "old_zips": [], "unmatched": []}

    def match(d):
        """判断旧目录里的插件是否已在库中。"""
        fid = (d["id"] or "").lower()
        fld = d["folder"].lower()
        nm = (d["name"] or "").strip().lower()
        if fid and fid in lib["all_ids"]:
            return "id匹配"
        if fld in lib["all_folders"]:
            return "文件夹名匹配"
        if nm and nm in lib["all_names"]:
            return "插件名匹配"
        return ""

    print("===== 新库规模 =====")
    print(f"  addons={len(lib['addons'])}  extensions={len(lib['extensions'])}")

    print("\n===== A) 旧目录中发现的插件目录 =====")
    for d in sorted(plugins, key=lambda x: x["path"].lower()):
        how = match(d)
        report["old_plugin_dirs"].append({**d, "in_library_by": how})
        tag = f"✓ 已入库({how})" if how else "✗ 未入库"
        print(f"  {tag:20s} [{d['kind']:9s}] {d['name'][:42]:44s} {os.path.relpath(d['path'], OLD)}")
        if not how:
            report["unmatched"].append({"type": "dir", **d})

    print(f"\n===== B) 旧目录中的 zip ({len(zips)}) =====")
    for z in sorted(zips, key=str.lower):
        rel = os.path.relpath(z, OLD)
        inner = zip_plugins(z)
        tops = zip_top_names(z)
        line = []
        matched = False
        for prefix, kind in inner:
            # 用 zip 内层文件夹名匹配
            pname = prefix or (tops[0] if len(tops) == 1 else "")
            ok = pname.lower() in lib["all_folders"]
            line.append(f"{pname or '(根)'}:{kind}={'✓' if ok else '?'}")
            matched = matched or ok
        mb = round(os.path.getsize(z) / 1048576, 1)
        report["old_zips"].append({"path": z, "mb": mb, "inner": inner, "matched": matched})
        print(f"  {'✓' if matched else '?'} {mb:7.1f}MB  {rel}")
        if inner:
            print(f"        内含: {', '.join(line)}")

    print("\n===== C) 未匹配项（需重点确认）=====")
    if not report["unmatched"]:
        print("  无 —— 旧目录中所有插件都能在库中找到对应")
    else:
        for u in report["unmatched"]:
            print(f"  [{u['kind']}] {u['name']}  {u['path']}")

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_old.json"),
              "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n详细结果 -> _migrate/audit_old.json")


main()
