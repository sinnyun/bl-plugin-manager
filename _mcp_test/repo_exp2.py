"""持久性实验（正确版：不使用 --factory-startup）。"""
import json
import os
import sys

import bpy

phase = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "check"
CFG = bpy.utils.user_resource("CONFIG")
TMP = os.path.abspath(os.path.join(CFG, ".."))
LIB = os.path.join(TMP, "fakelib", "extensions")
os.makedirs(LIB, exist_ok=True)
prefs = bpy.context.preferences


def dump(label):
    rows = []
    for i, r in enumerate(prefs.extensions.repos):
        rows.append({"i": i, "module": r.module, "enabled": r.enabled,
                     "use_custom": r.use_custom_directory,
                     "dir": getattr(r, "directory", ""),
                     "remote": getattr(r, "remote_url", "")})
    print(f"@@{label}@@" + json.dumps(rows, ensure_ascii=False))


if phase == "check":
    dump("CHECK")

elif phase == "repoint":
    # 方案：blender_org 指向库目录并成为在线商店；移除 pmlib（若存在）
    for r in list(prefs.extensions.repos):
        if r.module == "pmlib":
            prefs.extensions.repos.remove(r)
    for r in prefs.extensions.repos:
        if r.module == "blender_org":
            r.use_custom_directory = True
            r.custom_directory = LIB
    dump("AFTER_SET")
    bpy.ops.wm.save_userpref()
    print("@@SAVED@@")

elif phase == "removeorg":
    for r in list(prefs.extensions.repos):
        if r.module == "blender_org":
            prefs.extensions.repos.remove(r)
    dump("AFTER_REMOVE")
    bpy.ops.wm.save_userpref()
    print("@@SAVED@@")

elif phase == "sync":
    # 用 repo_index 同步第一个仓库，看是否拉取到索引
    out = {}
    try:
        bpy.ops.extensions.repo_sync(repo_index=0)
        out["sync0"] = "ok"
    except Exception as e:
        out["sync0"] = f"ERR {type(e).__name__}: {e}"
    # 库目录是否生成索引
    idx = os.path.join(LIB, ".blender_ext", "index.json")
    out["lib_index"] = os.path.isfile(idx)
    if out["lib_index"]:
        d = json.load(open(idx, encoding="utf-8"))
        out["packages"] = len(d.get("data") or [])
    dump("SYNC_REPOS")
    print("@@SYNC@@" + json.dumps(out, ensure_ascii=False))
