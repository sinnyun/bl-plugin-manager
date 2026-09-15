"""性能剖析：给各操作计时，定位界面卡死的真正来源。"""
import json
import time

import addon_utils
import bpy

from bl_plugin_manager import bridge, db as pdb, items, library

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
prefs.library_path = LIB

out = {}
timings = []


def t(label, fn, repeat=1):
    best = None
    total = 0.0
    for _ in range(repeat):
        t0 = time.perf_counter()
        try:
            fn()
        except Exception as e:
            timings.append((label, f"ERR {e}"))
            return
        dt = time.perf_counter() - t0
        total += dt
        best = dt if best is None else min(best, dt)
    timings.append((label, round(total / repeat, 4)))
    return best


# --- 单项开销 ---
t("addon_utils.modules_refresh()", lambda: addon_utils.modules_refresh(), 2)
t("bpy.ops.preferences.addon_refresh()", lambda: bpy.ops.preferences.addon_refresh(), 2)
t("bpy.utils.refresh_script_paths()", lambda: bpy.utils.refresh_script_paths(), 2)
t("bridge._module_index()", lambda: bridge._module_index(), 3)

# --- 最可疑：整仓刷新 ---
t("bpy.ops.extensions.repo_refresh_all()", lambda: bpy.ops.extensions.repo_refresh_all(), 1)

# --- 整体的 refresh_blender ---
t("bridge.refresh_blender() 全体", lambda: bridge.refresh_blender(), 1)

# --- 库同步（解析 152 个插件） ---
t("library.sync_library()", lambda: library.sync_library(LIB, pdb.LibraryDB(LIB)), 1)
t("LibraryDB.load()", lambda: pdb.LibraryDB(LIB), 3)

# --- 列表重建 ---
t("items.rebuild_items()", lambda: items.rebuild_items(prefs), 3)

# --- 单个启停往返 ---
d = pdb.LibraryDB(LIB)
sample = next((r for r in d.plugins.values()
               if r.get("kind") == "extension" and not r.get("missing")), None)
if sample:
    mod = sample["module"]
    was = bridge.is_module_enabled(mod)

    def round_trip():
        bridge.set_enabled(mod, True)
        bridge.set_enabled(mod, was)

    t("set_enabled 往返(1个扩展)", round_trip, 1)
    t("is_module_enabled() x10", lambda: [bridge.is_module_enabled(mod) for _ in range(10)], 3)

# --- 更新检测的索引读取 ---
from bl_plugin_manager import store, updates
t("store.read_catalog()", lambda: store.read_catalog(), 2)
t("updates.collect_known_versions()", lambda: updates.collect_known_versions(""), 2)

for label, v in timings:
    print(f"[PERF] {label:42s} {v}s")
