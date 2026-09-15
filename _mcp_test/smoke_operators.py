"""通过 MCP 在真实 GUI 中调用插件库管理器的各操作符，捕获错误。"""
import json
import traceback

import bpy

from bl_plugin_manager import db as pdb

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
d = pdb.LibraryDB(LIB)
recs = sorted(d.plugins.values(), key=lambda r: r.get("name") or "")
addon_rec = next((r for r in recs if r.get("kind") == "addon"), None)
ext_rec = next((r for r in recs if r.get("kind") == "extension"), None)

results = {}


def call(label, fn):
    try:
        r = fn()
        results[label] = {"ok": True, "ret": list(r) if isinstance(r, set) else str(r)}
    except Exception as e:
        results[label] = {"ok": False, "err": f"{type(e).__name__}: {e}",
                          "tb": traceback.format_exc()[-500:]}


# 无参数 / 可安全执行的操作符
call("show_warnings", lambda: bpy.ops.plugin_manager.show_warnings())
call("refresh", lambda: bpy.ops.plugin_manager.refresh())
call("setup_library", lambda: bpy.ops.plugin_manager.setup_library())
call("scan_candidates", lambda: bpy.ops.plugin_manager.scan_candidates())
call("scan_inbox", lambda: bpy.ops.plugin_manager.scan_inbox())
call("check_updates", lambda: bpy.ops.plugin_manager.check_updates())
call("clear_updates", lambda: bpy.ops.plugin_manager.clear_updates())

# 带参数
if addon_rec:
    key = addon_rec["key"]
    call("set_favorite_true", lambda: bpy.ops.plugin_manager.set_favorite(key=key, value=True))
    call("set_favorite_false", lambda: bpy.ops.plugin_manager.set_favorite(key=key, value=False))
    call("set_category", lambda: bpy.ops.plugin_manager.set_category(key=key, category="测试分类"))
    call("open_folder", lambda: bpy.ops.plugin_manager.open_folder(key=key))

# 分类
call("add_category", lambda: bpy.ops.plugin_manager.add_category(name="MCP测试"))
call("pick_category", lambda: bpy.ops.plugin_manager.pick_category(category="全部"))

# edit_meta（execute 直接设属性）
if addon_rec:
    call("edit_meta_execute", lambda: bpy.ops.plugin_manager.edit_meta(
        key=addon_rec["key"], display_name="", note="MCP备注", category="", tags="a, b"))

# 启停往返：挑一个扩展插件
if ext_rec:
    mod = ext_rec.get("module")
    results["target_ext"] = {"module": mod, "enabled_before": ext_rec.get("enabled")}
    call("toggle_ext_on", lambda: bpy.ops.plugin_manager.toggle(key=ext_rec["key"], enable=True))
    call("toggle_ext_off", lambda: bpy.ops.plugin_manager.toggle(key=ext_rec["key"], enable=False))

# 列表与过滤重建（面板依赖）
try:
    from bl_plugin_manager import items
    prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
    prefs.active_category = "全部"
    prefs.search = ""
    keys = items.rebuild_items(prefs)
    results["rebuild_items"] = {"ok": True, "count": len(keys),
                                "categories": list(prefs.category_items.keys()) and
                                [c.name for c in prefs.category_items]}
except Exception as e:
    results["rebuild_items"] = {"ok": False, "err": repr(e), "tb": traceback.format_exc()[-400:]}

# 过滤条件逐个试（面板筛选开关）
for attr in ("only_favorites", "only_updates", "only_enabled"):
    try:
        setattr(prefs, attr, True)
        items.rebuild_items(prefs)
        setattr(prefs, attr, False)
        items.rebuild_items(prefs)
        results[f"filter_{attr}"] = {"ok": True}
    except Exception as e:
        results[f"filter_{attr}"] = {"ok": False, "err": repr(e)}

print(json.dumps(results, ensure_ascii=False, default=str))
