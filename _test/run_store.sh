#!/usr/bin/env bash
# 在线商店测试（需联网）：下载、安装、更新、保留用户字段。
set -u
BL="${BLENDER_EXE:-/c/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe}"
REPO="E:/AI/geren/chajian_guanliqi"
ROOT="$(mktemp -d)"
mkdir -p "$ROOT/config" "$ROOT/scripts/addons"
cp -r "$REPO/bl_plugin_manager" "$ROOT/scripts/addons/"
rm -rf "$ROOT/scripts/addons/bl_plugin_manager/__pycache__"

BLENDER_USER_CONFIG="$(cygpath -w "$ROOT/config")" \
BLENDER_USER_SCRIPTS="$(cygpath -w "$ROOT/scripts")" \
  "$BL" --background --factory-startup \
  --python "$(cygpath -w "$REPO/_test/test_store.py")" 2>&1 \
  | grep -E "@@STORE@@|Traceback|Error"

rm -rf "$ROOT"
