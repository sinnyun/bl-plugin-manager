"""在隔离的 Blender 实例中：启用插件库管理器 + 挂载真实库 + 启动 BlenderMCP 服务器。

由外部以 GUI 模式启动（GUI 事件循环才能驱动 MCP 的命令队列定时器）：
    BLENDER_USER_CONFIG=... BLENDER_USER_SCRIPTS=... blender.exe --python launch.py
"""

import os
import traceback

import addon_utils
import bpy

LIBRARY = r"D:\nastongbu\qitaziliao\blender_addons"

print("@@LAUNCH@@ begin", flush=True)


def step(label, fn):
    try:
        r = fn()
        print(f"@@LAUNCH@@ OK   {label}: {r}", flush=True)
        return r
    except Exception as exc:
        print(f"@@LAUNCH@@ FAIL {label}: {exc}", flush=True)
        traceback.print_exc()
        return None


# 1) 插件库管理器（位于隔离 scripts/addons）
step("enable bl_plugin_manager", lambda: addon_utils.enable("bl_plugin_manager", default_set=True))

# 2) 指向真实库并挂载（这样库内 134 个插件都可被本实例发现）
def mount():
    prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
    prefs.library_path = LIBRARY
    bpy.ops.plugin_manager.setup_library()
    from bl_plugin_manager import bridge, db
    return bridge.library_state(LIBRARY), len(db.LibraryDB(LIBRARY).plugins)


step("mount library", mount)

# 3) BlenderMCP：其所在目录此时已随库挂载而被发现；register() 会自动启动服务器
step("refresh modules", lambda: addon_utils.modules_refresh())
step("enable blender_mcp", lambda: addon_utils.enable("blender_mcp", default_set=True))


def server_state():
    srv = getattr(bpy.types, "blendermcp_server", None)
    return {"exists": srv is not None, "running": bool(srv and srv.running),
            "port": bpy.context.scene.blendermcp_port}


step("mcp server state", server_state)

# 4) 让 3D 视图侧边栏可见，便于后续触发面板绘制
def show_sidebar():
    import bpy
    wins = list(bpy.context.window_manager.windows)
    out = []
    for w in wins:
        for area in w.screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        space.show_region_ui = True
                out.append(area)
    return f"VIEW_3D areas shown: {len(out)}"


step("show sidebar", show_sidebar)

print("@@LAUNCH@@ ready", flush=True)
