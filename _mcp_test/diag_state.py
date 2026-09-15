import json
import bpy

out = {}
out["blender"] = bpy.app.version_string
out["mcp_enabled"] = "blender_mcp" in [a.module for a in bpy.context.preferences.addons]
out["pm_enabled"] = "bl_plugin_manager" in [a.module for a in bpy.context.preferences.addons]

try:
    prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
    out["library_path"] = prefs.library_path
    from bl_plugin_manager import bridge, db
    out["library_state"] = bridge.library_state(prefs.library_path)
    out["library_records"] = len(db.LibraryDB(prefs.library_path).plugins)
    out["registered_panels"] = [c for c in dir(bpy.types) if c.startswith("PM_PT")]
except Exception as e:
    out["pm_error"] = repr(e)

# 窗口/区域结构
wins = []
for w in bpy.context.window_manager.windows:
    areas = []
    for a in w.screen.areas:
        areas.append({"type": a.type, "regions": [r.type for r in a.regions]})
    wins.append({"areas": areas})
out["windows"] = wins

# 是否可用 redraw_timer
out["wm_ops"] = [o for o in dir(bpy.ops.wm) if "redraw" in o.lower() or "draw" in o.lower()]

# 是否已注册我的面板类
out["PM_PT_main_registered"] = hasattr(bpy.types, "PM_PT_main")
out["PM_UL_plugins_registered"] = hasattr(bpy.types, "PM_UL_plugins")

print(json.dumps(out, ensure_ascii=False, default=str))
