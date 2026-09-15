"""关键验证：把远程仓库(官方商店)的目录设为自定义目录后，
从该仓库安装扩展，文件是否落到自定义目录？
"""
import json
import os
import shutil
import sys

import bpy

phase = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "setup"
CFG = bpy.utils.user_resource("CONFIG")
TMP = os.path.abspath(os.path.join(CFG, ".."))
LIB = os.path.join(TMP, "fakelib", "extensions")
os.makedirs(LIB, exist_ok=True)
prefs = bpy.context.preferences
out = {"lib": LIB}


def find_repo(module):
    for r in prefs.extensions.repos:
        if r.module == module:
            return r
    return None


if phase == "setup":
    # 移除其它 USER 仓库，避免干扰
    for r in list(prefs.extensions.repos):
        if r.module == "pmlib":
            prefs.extensions.repos.remove(r)
    r = find_repo("blender_org")
    r.use_custom_directory = True
    r.custom_directory = LIB
    r.enabled = True
    # 允许联网访问（安装/更新所需）
    try:
        prefs.system.use_online_access = True
        out["online"] = True
    except Exception as e:
        out["online"] = f"ERR {e}"
    bpy.ops.wm.save_userpref()
    out["set"] = "ok"

elif phase == "install":
    # 先同步索引
    try:
        bpy.ops.extensions.repo_sync_all()
        out["sync"] = "ok"
    except Exception as e:
        out["sync"] = f"ERR {e}"
    idx = os.path.join(LIB, ".blender_ext", "index.json")
    out["index"] = os.path.isfile(idx)
    if out["index"]:
        d = json.load(open(idx, encoding="utf-8"))
        data = d.get("data") or []
        out["catalog"] = len(data)
        # 找一个小包安装
        small = sorted([x for x in data if x.get("type") == "add-on"
                        and (x.get("archive_size") or 9e9) < 20000],
                       key=lambda x: x.get("archive_size") or 0)
        if small:
            pkg = small[0]
            out["target"] = {"id": pkg["id"], "ver": pkg["version"]}
            try:
                bpy.ops.extensions.package_install(repo_index=0, pkg_id=pkg["id"])
                out["install_op"] = "ok"
            except Exception as e:
                out["install_op"] = f"ERR {type(e).__name__}: {e}"
            # 检查是否落到 LIB
            landed = [e.name for e in os.scandir(LIB) if e.is_dir() and not e.name.startswith(".")]
            out["landed_in_lib"] = landed
            out["target_in_lib"] = pkg["id"] in landed

elif phase == "cleanup":
    for e in os.scandir(LIB):
        if e.is_dir():
            shutil.rmtree(e.path, ignore_errors=True)
    out["cleaned"] = True

print("@@INST@@" + json.dumps(out, ensure_ascii=False, default=str))
