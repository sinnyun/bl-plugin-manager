#!/usr/bin/env bash
# 在隔离的 Blender 用户目录中运行端到端测试，不会影响真实安装。
set -euo pipefail

BL="${BLENDER_EXE:-/c/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe}"
REPO="E:/AI/geren/chajian_guanliqi"
ROOT="$(mktemp -d)"
CFG="$ROOT/config"
SCRIPTS="$ROOT/scripts"
LOG="$ROOT/e2e.log"
mkdir -p "$CFG" "$SCRIPTS/addons"

cleanup() {
  rm -rf "$ROOT"
}
trap cleanup EXIT

# 把被测插件包复制进隔离的 scripts/addons
cp -r "$REPO/bl_plugin_manager" "$SCRIPTS/addons/"
rm -rf "$SCRIPTS/addons/bl_plugin_manager/__pycache__"

if BLENDER_USER_CONFIG="$(cygpath -w "$CFG")" \
   BLENDER_USER_SCRIPTS="$(cygpath -w "$SCRIPTS")" \
   "$BL" --background --factory-startup \
   --python "$(cygpath -w "$REPO/_test/test_e2e.py")" >"$LOG" 2>&1; then
  STATUS=0
else
  STATUS=$?
fi

# 保留原测试的简洁输出，同时让 traceback/启动错误可见。
grep -E "^\[PASS\]|^\[FAIL\]|===SUMMARY===|^\{|Traceback|^  File |^[A-Za-z]*Error" "$LOG" || true

# Blender 可能吞掉 Python 异常并返回 0；没有完整 summary 也必须失败。
if [ "$STATUS" -ne 0 ]; then
  exit "$STATUS"
fi
if ! grep -q '^===SUMMARY===$' "$LOG"; then
  echo "[FAIL] e2e test did not emit ===SUMMARY==="
  exit 1
fi
SUMMARY="$(grep -E '^\{' "$LOG" | tail -n 1 || true)"
if [ -z "$SUMMARY" ]; then
  echo "[FAIL] e2e test emitted no JSON summary"
  exit 1
fi
if echo "$SUMMARY" | grep -Eq '"failed"[[:space:]]*:[[:space:]]*[1-9][0-9]*'; then
  echo "[FAIL] e2e summary reports failed checks"
  exit 1
fi
exit 0
