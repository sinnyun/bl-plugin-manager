"""在运行实例中执行：让官方商店使用插件库。

步骤：
1. 重载最新代码（含 unify_store）
2. 调用 bridge.unify_store_with_library（官方仓库指向库、移除 pmlib、开启联网）
3. 把官方目录里原先的 17 个扩展迁入库
4. 报告结果
"""
import importlib
import io
import json
import os
import shutil
import sys

import bpy

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
out = {}

# --- 1) 重载代码 ---
import bl_plugin_manager as PM
for name in ("constants", "scan", "db", "bridge", "library", "store", "updates",
             "migrate", "watcher", "items", "preferences", "operators", "ui"):
    mod = getattr(PM, name, None)
    if mod is None:
        try:
            mod = importlib.import_module(f"bl_plugin_manager.{name}")
            setattr(PM, name, mod)
        except Exception as e:
            out[f"import_{name}"] = f"ERR {e}"
        continue
    try:
        importlib.reload(mod)
    except Exception as e:
        out[f"reload_{name}"] = f"ERR {e}"

_nul = io.StringIO()
_o, _e = sys.stdout, sys.stderr
sys.stdout = sys.stderr = _nul
try:
    for mod in (PM.preferences, PM.operators, PM.ui):
        for cls in getattr(mod, "classes", ()):
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
            try:
                bpy.utils.register_class(cls)
            except Exception:
                pass
    out["reload"] = "ok"
except Exception as e:
    out["reload"] = f"ERR {e}"
finally:
    sys.stdout, sys.stderr = _o, _e

prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
prefs.library_path = LIB
db = PM.db.LibraryDB(LIB)
out["records_before"] = len(db.plugins)

# --- 2) 记录官方目录并迁移其内容 ---
_, repo = PM.bridge.find_official_repo()
src = ""
if repo is not None:
    src = getattr(repo, "custom_directory", "") or getattr(repo, "directory", "")
out["official_src_before"] = src

moved, failed = [], []
if src and os.path.isdir(src):
    for e in sorted(os.scandir(src), key=lambda x: x.name.lower()):
        if not e.is_dir() or e.name.startswith("."):
            continue
        try:
            rec = PM.library.import_plugin_dir(e.path, LIB, PM.db.LibraryDB(LIB),
                                               move=True, origin="官方商店迁移")
            moved.append(rec.get("name") or e.name)
        except Exception as exc:
            failed.append(f"{e.name}: {exc}")
out["moved"] = moved
out["failed"] = failed

# 清理官方旧目录残留（迁移后应该空了）
cleaned = []
if src and os.path.isdir(src):
    for e in os.scandir(src):
        if e.name.startswith("."):
            continue
        try:
            if e.is_dir():
                shutil.rmtree(e.path, ignore_errors=True)
            else:
                os.remove(e.path)
            cleaned.append(e.name)
        except OSError:
            pass
out["cleaned"] = cleaned

# --- 3) 官方仓库指向库 + 移除 pmlib + 开启联网 ---
res = PM.bridge.unify_store_with_library(LIB)
out["unify"] = res
PM.bridge.save_prefs()

# --- 4) 结果核对 ---
st = PM.bridge.library_state(LIB)
out["state"] = st
out["records_after"] = len(PM.db.LibraryDB(LIB).plugins)
out["online_access"] = bool(bpy.context.preferences.system.use_online_access)
out["repos_now"] = [{"module": r.module, "use_custom": r.use_custom_directory,
                     "dir": getattr(r, "directory", ""),
                     "remote": getattr(r, "remote_url", "")}
                    for r in bpy.context.preferences.extensions.repos]
out["official_dir_after"] = ""
_, repo2 = PM.bridge.find_official_repo()
if repo2 is not None:
    out["official_dir_after"] = getattr(repo2, "custom_directory", "") or getattr(repo2, "directory", "")

print("@@UNIFY@@" + json.dumps(out, ensure_ascii=False, default=str))
