"""迁移规划（只读）：精确列出每个待迁移插件及其目标位置，检测重名冲突。

不复制、不移动、不改配置。产出 plan.json 供审阅。
"""

from __future__ import annotations

import json
import os
import re

BLENDER = r"D:\nastongbu\qitaziliao\blender"
TARGET = r"D:\nastongbu\qitaziliao\blender_addons"
USER_52 = r"C:\Users\sanliD\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons"
USER_45 = r"C:\Users\sanliD\AppData\Roaming\Blender Foundation\Blender\4.5\scripts\addons"

# 分类文件夹（每个都是「根=扩展插件 + addons/ 子目录=传统插件」的混合结构）
CATEGORY_DIRS = [
    os.path.join(BLENDER, n)
    for n in (
        "chajian",
        "gongjuxiaolv_chajian",
        "zidingyi_chajian",
        "donghuaguge_chajian",
        "jianmo_chajian",
        "python_jiaoben",
        "caizhixuanran_chajian",
        "wulimoni_chajian",
        "ass",
        "blender4.com",
    )
]

EXCLUDE_DIRS = {"__pycache__", ".git", ".svn", ".blender_ext", ".pm", ".local", ".cache", "node_modules"}
EXCLUDE_NAMES = {"备份用", "addons", "备份"}  # 备份目录不是插件


def has(p, n):
    return os.path.isfile(os.path.join(p, n))


def kind_of(p):
    if has(p, "blender_manifest.toml"):
        return "extension"
    if has(p, "__init__.py"):
        return "addon"
    return None


def plugin_id(p):
    """扩展插件优先用 manifest 的 id。"""
    mp = os.path.join(p, "blender_manifest.toml")
    if os.path.isfile(mp):
        try:
            with open(mp, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            m = re.search(r'^\s*id\s*=\s*["\']([^"\']+)', text, re.M)
            if m:
                return m.group(1)
        except OSError:
            pass
    return os.path.basename(p)


def scan_container(base, label, source_type):
    """扫描一个容器目录：根级子目录=扩展，addons/ 子目录=传统插件。"""
    found = []
    if not os.path.isdir(base):
        return found

    def consider(path, legacy_hint):
        name = os.path.basename(path)
        if name in EXCLUDE_DIRS or name.startswith(".") or name in EXCLUDE_NAMES:
            return
        k = kind_of(path)
        if k is None:
            return
        found.append({
            "src": path,
            "name": name,
            "kind": k,
            "id": plugin_id(path),
            "origin": label,
            "source_type": source_type,
        })

    for e in sorted(os.scandir(base), key=lambda x: x.name.lower()):
        if not e.is_dir():
            continue
        if e.name == "addons":
            for sub in sorted(os.scandir(e.path), key=lambda x: x.name.lower()):
                if sub.is_dir():
                    consider(sub.path, True)
            continue
        consider(e.path, False)

    # 也处理 base 本身就是一个传统插件容器（用户配置 addons 目录）的情况
    return found


def main():
    plan = {"addons": [], "extensions": [], "skipped_duplicates": [], "conflicts": []}
    seen = {}

    def add(entry):
        key = (entry["kind"], entry["id"].lower())
        if key in seen:
            plan["skipped_duplicates"].append({
                "name": entry["name"],
                "id": entry["id"],
                "kind": entry["kind"],
                "dup_of": seen[key]["src"],
                "skipped_src": entry["src"],
            })
            return
        seen[key] = entry
        plan["addons" if entry["kind"] == "addon" else "extensions"].append(entry)

    # 1) 用户配置默认安装位置（传统插件为主）
    for entry in scan_container(USER_52, "用户配置 addons (5.2)", "user_addons"):
        add(entry)

    # 2) 分类文件夹
    for base in CATEGORY_DIRS:
        label = os.path.basename(base)
        for entry in scan_container(base, label, "category"):
            add(entry)

    # 3) 4.5 用户配置（仅统计，是否迁移由用户决定）——先只记录数量
    legacy45 = scan_container(USER_45, "用户配置 addons (4.5)", "user_addons_45")
    plan["user45_count"] = len(legacy45)
    plan["user45_names"] = [e["name"] for e in legacy45]

    # 目标位置上已存在的插件（避免覆盖）
    for sub, kind in (("addons", "addon"), ("extensions", "extension")):
        base = os.path.join(TARGET, sub)
        if os.path.isdir(base):
            plan.setdefault("existing_in_target", {})[kind] = [
                e.name for e in os.scandir(base) if e.is_dir()
            ]

    n_add = len(plan["addons"])
    n_ext = len(plan["extensions"])
    plan["summary"] = {
        "to_migrate_legacy_addons": n_add,
        "to_migrate_extensions": n_ext,
        "total": n_add + n_ext,
        "duplicates_skipped": len(plan["skipped_duplicates"]),
        "target": TARGET,
    }

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plan.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print("===== 迁移预案 =====")
    print(f"传统插件 → {TARGET}\\addons\\  : {n_add} 个")
    for e in plan["addons"]:
        print(f"    [{e['origin']}] {e['name']}")
    print(f"\n扩展插件 → {TARGET}\\extensions\\ : {n_ext} 个")
    for e in plan["extensions"]:
        print(f"    [{e['origin']}] {e['name']}  (id={e['id']})")
    print(f"\n因重复跳过: {len(plan['skipped_duplicates'])}")
    for d in plan["skipped_duplicates"]:
        print(f"    {d['name']} ← 已在 {d['dup_of']}")
    print(f"\n4.5 用户配置另有 {plan['user45_count']} 个同名插件副本（默认不迁移，避免重复）")
    print("\n汇总:", json.dumps(plan["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
