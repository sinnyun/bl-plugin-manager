"""真实卡顿验证：在运行实例中实测各操作耗时（用户能感知的 Wall-clock）。"""
import importlib
import json
import time

import bpy

import bl_plugin_manager as PM
for n in ("scan", "db", "bridge", "library", "store", "updates", "migrate",
          "watcher", "items", "preferences", "operators", "ui"):
    try:
        importlib.reload(getattr(PM, n))
    except Exception:
        pass

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
prefs.library_path = LIB
out = {}


def timed(label, fn):
    t0 = time.perf_counter()
    try:
        fn()
        dt = time.perf_counter() - t0
        out[label] = round(dt, 3)
        return dt
    except Exception as e:
        out[label] = f"ERR {e}"
        return None


# 用户点一下就卡的操作
timed("刷新列表(refresh)", lambda: bpy.ops.plugin_manager.refresh())
timed("启用/修复(setup_library)", lambda: bpy.ops.plugin_manager.setup_library())
timed("检查更新(check_updates)", lambda: bpy.ops.plugin_manager.check_updates())
timed("扫描投放区(scan_inbox)", lambda: bpy.ops.plugin_manager.scan_inbox())
timed("扫描可收编(scan_candidates)", lambda: bpy.ops.plugin_manager.scan_candidates())
timed("同步自启(apply_startup)", lambda: bpy.ops.plugin_manager.apply_startup(
    disable_unmarked=False))
timed("清理自动分类(reset)", lambda: bpy.ops.plugin_manager.reset_auto_categories())

# 面板重绘（每次界面刷新都会走）
timed("面板重绘 x10", lambda: [PM.items.rebuild_items(prefs) for _ in range(10)])

# 单个启停
d = PM.db.LibraryDB(LIB)
ext = next((r for r in d.plugins.values()
            if r.get("kind") == "extension" and not r.get("missing")), None)
if ext:
    was = PM.bridge.is_module_enabled(ext["module"])
    timed("单个启停往返", lambda: (bpy.ops.plugin_manager.toggle(key=ext["key"], enable=True),
                              bpy.ops.plugin_manager.toggle(key=ext["key"], enable=was)))

# 批量勾选若干后批量启停
recs = sorted(d.plugins.values(), key=lambda r: r.get("name") or "")[:5]
PM.items.clear_selection()
for r in recs:
    PM.items.set_selected(r["key"], True)
PM.items.rebuild_items(prefs)
out["batch_targets"] = len(PM.items.all_selected_keys())
timed("批量启停(5个)", lambda: bpy.ops.plugin_manager.batch_enable(value=True))
PM.items.clear_selection()
PM.items.rebuild_items(prefs)

out["records"] = len(PM.db.LibraryDB(LIB).plugins)
print("@@WALL@@" + json.dumps(out, ensure_ascii=False))
