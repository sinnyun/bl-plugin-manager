"""校验源码里所有 icon="..." 字面量在 Blender 图标枚举中是否存在。

这类错误（用了不存在的图标名）只在面板实际绘制时才抛异常，
静态检查能在开发阶段就拦住。

用法：
    blender --background --python _test/check_icons.py
"""

import ast
import os
import re
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(os.path.dirname(HERE), "bl_plugin_manager")


def icon_enum():
    """取 UILayout 的 icon 枚举（label/prop/operator 共用同一套）。"""
    for fn_name in ("label", "prop", "operator"):
        try:
            fn = bpy.types.UILayout.bl_rna.functions[fn_name]
            prop = fn.parameters["icon"]
            return {i.identifier for i in prop.enum_items}
        except Exception:
            continue
    return set()


def collect_icons():
    """从源码中提取图标字面量。

    覆盖 `icon="XXX"`、`icon = "XXX"`，以及三元表达式
    （如 `icon="RADIOBUT_ON" if cond else "RADIOBUT_OFF"`）里的图标名：
    对任何提到 icon 的行，取出其中形如全大写标识符的字符串。
    """
    found = []
    strict = re.compile(r'icon\s*=\s*"([A-Za-z_0-9]+)"')
    loose = re.compile(r'"([A-Z][A-Z0-9_]{2,})"')
    for fn in sorted(os.listdir(PKG)):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(PKG, fn)
        with open(path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if "icon" not in line:
                    continue
                hits = set(m.group(1) for m in strict.finditer(line))
                hits |= set(m.group(1) for m in loose.finditer(line))
                for ic in sorted(hits):
                    found.append((fn, i, ic))
    return found


def main():
    valid = icon_enum()
    if not valid:
        print("@@ICON_CHECK@@ 无法获取图标枚举，跳过")
        return
    print(f"[ICON] Blender 提供 {len(valid)} 个图标")

    icons = collect_icons()
    print(f"[ICON] 源码引用 {len(icons)} 处")

    bad = [(f, ln, ic) for f, ln, ic in icons if ic not in valid]
    used = sorted({ic for _, _, ic in icons})
    print(f"[ICON] 去重后共 {len(used)} 个图标名")

    if bad:
        print("@@ICON_CHECK@@ FAIL")
        for f, ln, ic in bad:
            print(f"   ! {f}:{ln}  无效图标 '{ic}'")
        # 给出相近候选，便于替换
        for _, _, ic in bad[:3]:
            close = [v for v in sorted(valid) if ic.split("_")[0] in v][:8]
            print(f"   '{ic}' 的相近候选: {close}")
        sys.exit(1)

    print("@@ICON_CHECK@@ OK")
    print(f"[ICON] 使用中的图标: {used}")


main()
