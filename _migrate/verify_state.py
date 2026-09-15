"""全新进程验证：读取已持久化的偏好，报告最终状态。"""

from __future__ import annotations

import json
import os

import bpy

TARGET = r"D:\nastongbu\qitaziliao\blender_addons"

prefs = bpy.context.preferences
sd = [i.directory for i in prefs.filepaths.script_directories]
repos = [
    {
        "name": r.name, "module": r.module, "enabled": r.enabled,
        "dir": getattr(r, "custom_directory", "") or getattr(r, "directory", ""),
    }
    for r in prefs.extensions.repos
]
enabled = sorted(a.module for a in prefs.addons)

print("@@SD@@", json.dumps(sd, ensure_ascii=False))
print("@@REPOS@@", json.dumps(repos, ensure_ascii=False))
print("@@ENABLED_COUNT@@", len(enabled))
print("@@ENABLED@@", json.dumps(enabled, ensure_ascii=False))
print("@@HAS_TARGET_SD@@", any(os.path.normcase(TARGET) == os.path.normcase(p) for p in sd))
print("@@HAS_PMLIB_REPO@@", any(r["module"] == "pmlib" for r in repos))
print("@@OLD_D_REFS@@", [p for p in sd if "nastongbu" in p.lower()])
