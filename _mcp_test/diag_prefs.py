import json
import bpy

prefs = bpy.context.preferences
out = {}
out["script_dirs"] = [i.directory for i in prefs.filepaths.script_directories]
out["repos"] = [
    {"module": r.module, "name": r.name, "custom": getattr(r, "custom_directory", ""),
     "dir": getattr(r, "directory", ""), "enabled": r.enabled}
    for r in prefs.extensions.repos
]
enabled = sorted(a.module for a in prefs.addons)
out["enabled_count"] = len(enabled)
out["enabled"] = enabled

# 是否存在指向已删除旧路径的启用项
stale = [m for m in enabled if "nastongbu" in m or "pmlib" in m]
out["pm_or_old"] = stale

# 我的插件偏好
try:
    p = prefs.addons["bl_plugin_manager"].preferences
    out["pm_prefs"] = {"library_path": p.library_path, "auto_scan": p.auto_scan,
                       "active_category": p.active_category,
                       "items": len(p.plugin_items)}
except Exception as e:
    out["pm_prefs_error"] = repr(e)

# 该实例是否能看到库里插件（抽查若干）
import addon_utils
mods = {m.__name__ for m in addon_utils.modules()}
out["sample_found"] = {k: (k in mods) for k in (
    "bl_ext.pmlib.retopoflow", "bl_ext.pmlib.photographer", "Petik", "blender_mcp")}
out["addons_paths"] = bpy.utils.script_paths(subdir="addons")

print(json.dumps(out, ensure_ascii=False, default=str))
