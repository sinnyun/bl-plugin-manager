#!/usr/bin/env bash
# Run the isolated store check through the maintained Windows runner.
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if command -v cygpath >/dev/null 2>&1; then
  repo="$(cygpath -w "$repo")"
fi
exec powershell.exe -ExecutionPolicy Bypass -File "$repo/_test/run_regressions.ps1" -Suite store "$@"
