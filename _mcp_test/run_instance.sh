#!/usr/bin/env bash
# 启动一个隔离的 GUI Blender 实例，启用插件库管理器 + BlenderMCP，并把控制台输出落盘。
# 不触碰用户正在运行的 Blender（使用独立的 BLENDER_USER_CONFIG / BLENDER_USER_SCRIPTS）。
set -u

REPO="E:/AI/geren/chajian_guanliqi"
WORK="$REPO/_mcp_test"
BL="${BLENDER_EXE:-/c/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe}"

CFG="$WORK/config"
SCR="$WORK/scripts"
LOG="$WORK/blender.log"

rm -rf "$CFG" "$SCR"
mkdir -p "$CFG" "$SCR/addons"

# 只放插件库管理器；blender_mcp 通过挂载真实插件库被发现
cp -r "$REPO/bl_plugin_manager" "$SCR/addons/"
rm -rf "$SCR/addons/bl_plugin_manager/__pycache__"

: > "$LOG"

BLENDER_USER_CONFIG="$(cygpath -w "$CFG")" \
BLENDER_USER_SCRIPTS="$(cygpath -w "$SCR")" \
  "$BL" --python "$(cygpath -w "$WORK/launch.py")" >> "$LOG" 2>&1 &

echo "started pid=$! log=$LOG"
