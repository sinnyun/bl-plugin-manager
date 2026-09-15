"""把旧目录（除 ass/）中所有尚未入库的插件统一收编进插件库。

去重策略（比文件夹名更可靠）：
* 提取每个插件的 (kind, id, 显示名, 版本)；
* 与库 + 官方商店中的 id / 文件夹名 / 显示名 比对；
* 同一次运行内的候选再按显示名去重，同名的保留版本更高的。

用法：
    blender --background --python import_leftovers.py -- --dry
    blender --background --python import_leftovers.py -- --run
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
OLD = r"D:\nastongbu\qitaziliao\blender"
HERE = os.path.dirname(os.path.abspath(__file__))
OFFICIAL = r"C:\Users\sanliD\AppData\Roaming\Blender Foundation\Blender\5.2\extensions\blender_org"
USER_EXT = r"C:\Users\sanliD\AppData\Roaming\Blender Foundation\Blender\5.2\extensions"

SKIP_DIRS = {"__pycache__", ".git", ".svn", ".blender_ext", ".pm", ".local", ".cache",
             "node_modules"}
# 整棵子树跳过：属于其它插件的安装包内部组件 / 已在库中的 Auto-Rig Pro 家族
SKIP_SUBTREES = ("ARP相关", "AutoRigPro", "inference", "_internal")

sys.path.insert(0, r"E:\AI\geren\chajian_guanliqi")
from bl_plugin_manager import scan as pm_scan  # noqa: E402


def norm_key(s: str) -> str:
    """名称归一化：忽略 emoji/空格/标点差异，只留中文、字母、数字。"""
    import unicodedata

    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s.lower())


# ---------------------------------------------------------------------------
# 插件探测
# ---------------------------------------------------------------------------
def probe(path: str, fallback_name: str = "") -> dict | None:
    """返回 {kind,id,name,version,folder}；不是插件返回 None。"""
    man = os.path.join(path, "blender_manifest.toml")
    if os.path.isfile(man):
        try:
            t = open(man, encoding="utf-8", errors="replace").read()
        except OSError:
            return None
        g = lambda k: (re.search(rf'^\s*{k}\s*=\s*["\']([^"\']+)', t, re.M) or [None, ""])[1]
        pid = g("id") or fallback_name or os.path.basename(path)
        return {"kind": "extension", "id": pid, "name": g("name") or pid,
                "version": g("version") or "", "folder": pid}
    init = os.path.join(path, "__init__.py")
    if os.path.isfile(init):
        try:
            t = open(init, encoding="utf-8", errors="replace").read(80000)
        except OSError:
            return None
        if "bl_info" not in t:
            return None
        n = re.search(r'["\']name["\']\s*:\s*["\']([^"\']*)', t)
        v = re.search(r'["\']version["\']\s*:\s*\(([^)]*)\)', t)
        fld = fallback_name or os.path.basename(path)
        return {"kind": "addon", "id": fld, "name": (n.group(1) if n else fld),
                "version": ".".join(re.findall(r"\d+", v.group(1))) if v else "",
                "folder": fld}
    return None


def keyset_of(base: str, out: set) -> set:
    """收集目录下所有插件的 id/文件夹名/显示名（小写）到 out。"""
    if not os.path.isdir(base):
        return out
    for e in os.scandir(base):
        if not e.is_dir() or e.name.startswith("."):
            continue
        out.add(e.name.lower())
        d = probe(e.path)
        if d:
            out.add(d["id"].lower())
            out.add(norm_key(d["name"]))
            out.add(norm_key(d["id"]))
    return out


# ---------------------------------------------------------------------------
# 采集候选
# ---------------------------------------------------------------------------
def gather(keep: set) -> tuple[list[dict], list[dict], list[str], list[str]]:
    todo, skipped, tmpdirs, subtree_skipped = [], [], [], []

    def consider(path, origin, src, fallback="", tmp=None):
        d = probe(path, fallback)
        if not d:
            return
        d.update({"path": path, "origin": origin, "src": src, "tmp": tmp})
        k = norm_key(d["name"]) or norm_key(d["id"])
        if (d["id"].lower() in keep) or (d["folder"].lower() in keep) or (k and k in keep):
            skipped.append(d)
        else:
            todo.append(d)

    for top in sorted(os.listdir(OLD)):
        if top == "ass":
            continue
        troot = os.path.join(OLD, top)
        if not os.path.isdir(troot):
            continue
        for root, dirs, files in os.walk(troot):
            if any(s in root for s in SKIP_SUBTREES):
                if root != troot and os.path.basename(root) in ("ARP相关", "AutoRigPro"):
                    subtree_skipped.append(root)
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            # 目录形式的插件
            d = probe(root)
            if d and root != troot:
                consider(root, "旧目录", root)
                dirs[:] = []
                continue
            # zip
            for fn in files:
                if not fn.lower().endswith(".zip"):
                    continue
                zp = os.path.join(root, fn)
                if not zipfile.is_zipfile(zp):
                    continue
                tmp = tempfile.mkdtemp(prefix="pm_imp_")
                tmpdirs.append(tmp)
                try:
                    with zipfile.ZipFile(zp) as z:
                        z.extractall(tmp)
                except Exception:
                    continue
                fallback = os.path.splitext(fn)[0]
                found = tmp if probe(tmp, fallback) else ""
                if not found:
                    for e in os.scandir(tmp):
                        if e.is_dir() and probe(e.path):
                            found = e.path
                            break
                if found:
                    consider(found, "旧zip", zp, fallback, tmp)

    dropped = {}
    unique = []
    for d in todo:
        k = norm_key(d["name"]) or norm_key(d["id"])
        if k in dropped:
            prev = dropped[k]
            # 版本更高者胜；版本无法比较时保留目录形式
            if pm_scan.compare_versions(d["version"], prev["version"]) > 0:
                unique.remove(prev)
                unique.append(d)
                dropped[k] = d
            continue
        dropped[k] = d
        unique.append(d)
    return unique, skipped, tmpdirs, subtree_skipped


def main():
    from bl_plugin_manager import db as pdb, library

    dry = "--dry" in sys.argv or "--run" not in sys.argv

    keep = set()
    keyset_of(os.path.join(LIB, "addons"), keep)
    keyset_of(os.path.join(LIB, "extensions"), keep)
    keyset_of(OFFICIAL, keep)
    keyset_of(os.path.join(USER_EXT, "user_default"), keep)
    print(f"[IMP] 现有标识: {len(keep)}")

    todo, skipped, tmpdirs, subtrees = gather(keep)
    print(f"[IMP] 待导入 {len(todo)}，已存在跳过 {len(skipped)}，跳过子树 {len(subtrees)}")

    for d in sorted(todo, key=lambda x: x["name"].lower()):
        print(f"   + [{d['kind']:9s}] {d['name'][:40]:42s} v{d['version']:12s} <- {os.path.relpath(d['src'], OLD)}")

    if dry:
        for t in tmpdirs:
            shutil.rmtree(t, ignore_errors=True)
        print("[IMP] [DRY] 未修改")
        return

    db = pdb.LibraryDB(LIB)
    journal = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "imported": [], "failed": []}
    for d in todo:
        try:
            rec = library.import_plugin_dir(d["path"], LIB, db, move=True,
                                            origin=d["origin"], origin_path=d["src"])
            journal["imported"].append({"name": rec.get("name"), "kind": rec.get("kind"),
                                        "version": rec.get("version"), "from": d["src"]})
        except Exception as exc:
            journal["failed"].append({"name": d["name"], "from": d["src"], "err": str(exc)})
    for t in tmpdirs:
        shutil.rmtree(t, ignore_errors=True)

    with open(os.path.join(HERE, "journal_import.json"), "w", encoding="utf-8") as f:
        json.dump(journal, f, ensure_ascii=False, indent=2)

    stats = library.sync_library(LIB, pdb.LibraryDB(LIB))
    print(f"[IMP] 成功 {len(journal['imported'])}，失败 {len(journal['failed'])}")
    for x in journal["failed"]:
        print(f"   ! {x['name']}: {x['err'][:110]}")
    print("[IMP] 库内总数:", len(pdb.LibraryDB(LIB).plugins), stats)


main()
