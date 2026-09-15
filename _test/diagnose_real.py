"""只读诊断：扫描真实 Blender 环境中的插件目录，报告识别率。不导入、不修改。"""
import json
import os
import sys

REPO = r"E:\AI\geren\chajian_guanliqi"
sys.path.insert(0, REPO)

import bpy

from bl_plugin_manager import migrate, scan

lib = os.path.join(os.path.expanduser("~"), "BlenderPluginLibrary_PREVIEW")

print("@@VER@@", bpy.app.version_string)

# 直接扫描脚本目录与本地扩展仓库（不依赖本插件已启用）
results = {"addon_dirs": [], "ext_dirs": []}

try:
    for item in bpy.context.preferences.filepaths.script_directories:
        base = os.path.join(item.directory, "addons")
        for entry in scan.scan_dir(base, kind_hint="addon"):
            results["addon_dirs"].append(
                {
                    "name": entry["name"],
                    "kind": entry["kind"],
                    "valid": entry["valid"],
                    "version": (entry["meta"] or {}).get("version", ""),
                    "display": (entry["meta"] or {}).get("name", ""),
                    "parent": item.name,
                }
            )
except Exception as exc:
    print("@@ERR@@", repr(exc))

try:
    for repo in bpy.context.preferences.extensions.repos:
        module = getattr(repo, "module", "")
        if module in {"blender_org", "system", "user_default", "addons"}:
            continue
        directory = getattr(repo, "custom_directory", "") or getattr(repo, "directory", "")
        if not directory or not os.path.isdir(directory):
            continue
        for entry in scan.scan_dir(directory, kind_hint="extension"):
            results["ext_dirs"].append(
                {
                    "name": entry["name"],
                    "kind": entry["kind"],
                    "valid": entry["valid"],
                    "version": (entry["meta"] or {}).get("version", ""),
                    "display": (entry["meta"] or {}).get("name", ""),
                    "parent": getattr(repo, "name", module),
                }
            )
except Exception as exc:
    print("@@ERR@@", repr(exc))

valid = [r for r in results["addon_dirs"] + results["ext_dirs"] if r["valid"]]
invalid = [r for r in results["addon_dirs"] + results["ext_dirs"] if not r["valid"]]
print("@@COUNT@@", len(results["addon_dirs"]), len(results["ext_dirs"]))
print("@@VALID@@", len(valid))
print("@@INVALID@@", json.dumps(invalid, ensure_ascii=False))
print("@@SAMPLE@@", json.dumps(valid[:12], ensure_ascii=False))

# 迁移候选（真实）
try:
    from bl_plugin_manager.db import LibraryDB

    db = LibraryDB(lib)  # 该路径不存在时为空库
    cands = migrate.collect_candidates(lib, db)
    print("@@CAND@@", len(cands))
    print("@@CANDSAMPLE@@", json.dumps(cands[:15], ensure_ascii=False))
except Exception as exc:
    print("@@CANDERR@@", repr(exc))
