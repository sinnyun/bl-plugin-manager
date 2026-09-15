"""在新进程中还原启用状态并校验迁移结果（无陈旧模块缓存）。

模块名按插件在磁盘上的实际位置判定：
* <库>/addons/<名>        → 模块名 = <名>
* <库>/extensions/<名>    → 模块名 = bl_ext.pmlib.<名>
"""

from __future__ import annotations

import json
import os

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
JOURNAL = os.path.join(HERE, "journal.json")
TARGET = r"D:\nastongbu\qitaziliao\blender_addons"
DB = os.path.join(TARGET, ".pm", "library.json")

with open(JOURNAL, "r", encoding="utf-8") as f:
    journal = json.load(f)

import bl_plugin_manager as PM

# 彻底重建库元数据：迁移刚完成，尚无用户手写分类/备注，可安全重建
if os.path.isfile(DB):
    os.remove(DB)
for extra in ("processed.json",):
    p = os.path.join(TARGET, ".pm", extra)
    if os.path.isfile(p):
        os.remove(p)

state = PM.bridge.library_state(TARGET)
print("[RESTORE] 库挂载状态:", state)
if not (state["script_dir"] and state["repo"]):
    print("[RESTORE] 重新挂载:", PM.bridge.register_library(TARGET, save=False))


def locate(dest):
    """返回 (实际路径, 模块名) 或 (None, None)。"""
    base = os.path.basename(dest)
    for sub, kind in (("addons", "addon"), ("extensions", "extension")):
        p = os.path.join(TARGET, sub, base)
        if os.path.isdir(p):
            mod = base if kind == "addon" else f"bl_ext.pmlib.{base}"
            return p, mod
    return None, None


want = [m for m in journal["moves"] if m.get("was_enabled")]
print(f"[RESTORE] 需还原启用: {len(want)} 个")

ok, fail, notfound = [], [], []
for m in want:
    path, mod = locate(m["dst"])
    if not path:
        notfound.append(m["dst"])
        continue
    success, err = PM.bridge.set_enabled(mod, True)
    (ok if success else fail).append((mod, err))

print(f"[RESTORE] 成功 {len(ok)}, 失败 {len(fail)}, 未找到 {len(notfound)}")
for mod, err in fail:
    print("[RESTORE] 失败:", mod, "|", (err or "")[:150])
for p in notfound:
    print("[RESTORE] 未找到:", p)

PM.bridge.save_prefs()

from bl_plugin_manager import db as pdb, library

stats = library.sync_library(TARGET, pdb.LibraryDB(TARGET))
print("[RESTORE] 库同步:", json.dumps(stats, ensure_ascii=False))
print("[RESTORE] 当前启用总数:", len(bpy.context.preferences.addons))

db = pdb.LibraryDB(TARGET)
print("[RESTORE] 库内插件数:", len(db.plugins))
print("[RESTORE] @@OK@@", json.dumps([m for m, _ in ok], ensure_ascii=False))
