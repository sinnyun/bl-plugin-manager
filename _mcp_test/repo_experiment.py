"""可行性实验（隔离配置，不动真实环境）：
1) USER 仓库能否用官方 API 作为 remote 并成功 sync？
2) 给 blender_org 设 custom_directory 能否持久化？
3) 移除 blender_org / 禁用，重启后是否被重置？
"""
import json
import os
import sys

import bpy

phase = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "check"
CFG = bpy.utils.user_resource("CONFIG")
TMP = os.path.join(CFG, "..", "_exproot")
TMP = os.path.abspath(TMP)
LIB = os.path.join(TMP, "fakelib", "extensions")
os.makedirs(LIB, exist_ok=True)

prefs = bpy.context.preferences


def dump(label):
    rows = []
    for r in prefs.extensions.repos:
        rows.append({
            "module": r.module, "source": r.source, "enabled": r.enabled,
            "use_custom": r.use_custom_directory,
            "custom": getattr(r, "custom_directory", ""),
            "dir": getattr(r, "directory", ""),
            "use_remote": getattr(r, "use_remote_url", None),
            "remote": getattr(r, "remote_url", ""),
        })
    print(f"@@{label}@@" + json.dumps(rows, ensure_ascii=False))


if phase == "setup":
    # 造一个 USER 远程仓库（模拟 pmlib 改成在线商店）
    r = prefs.extensions.repos.new(name="pmlib", module="pmlib")
    r.use_custom_directory = True
    r.custom_directory = LIB
    r.use_remote_url = True
    r.remote_url = "https://extensions.blender.org/api/v1/extensions/"
    r.enabled = True
    # 官方仓库指向一个独立目录
    off = os.path.join(TMP, "off")
    os.makedirs(off, exist_ok=True)
    b = prefs.extensions.repos.new(name="extensions.blender.org", module="blender_org")
    b.use_custom_directory = True
    b.custom_directory = off
    b.use_remote_url = True
    b.remote_url = "https://extensions.blender.org/api/v1/extensions/"
    b.enabled = True
    dump("SETUP")
    bpy.ops.wm.save_userpref()
    print("@@SAVED@@")

elif phase == "check":
    dump("PERSIST")

elif phase == "sync":
    # 测 pmlib 远程 sync
    res = {}
    try:
        bpy.ops.extensions.repo_sync(repo="pmlib")
        res["sync_pmlib"] = "ok"
    except Exception as e:
        res["sync_pmlib"] = f"ERR {type(e).__name__}: {e}"
    idx = os.path.join(LIB, ".blender_ext", "index.json")
    res["index_exists"] = os.path.isfile(idx)
    res["index_size"] = os.path.getsize(idx) if os.path.isfile(idx) else 0
    if res["index_exists"]:
        try:
            d = json.load(open(idx, encoding="utf-8"))
            res["packages"] = len(d.get("data") or [])
        except Exception as e:
            res["packages"] = f"ERR {e}"
    print("@@SYNC@@" + json.dumps(res, ensure_ascii=False))

elif phase == "repoint":
    # 把 blender_org 指到 LIB，移除 pmlib（方案 E 预演）
    for r in list(prefs.extensions.repos):
        if r.module == "pmlib":
            prefs.extensions.repos.remove(r)
        elif r.module == "blender_org":
            r.use_custom_directory = True
            r.custom_directory = LIB
    dump("REPOINT")
    bpy.ops.wm.save_userpref()
    print("@@SAVED@@")

elif phase == "remove_org":
    for r in list(prefs.extensions.repos):
        if r.module == "blender_org":
            prefs.extensions.repos.remove(r)
    dump("REMOVED")
    bpy.ops.wm.save_userpref()
    print("@@SAVED@@")
