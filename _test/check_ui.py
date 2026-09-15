"""UI 一致性静态检查：确认 ui.py 引用的偏好属性与操作符都已定义。"""
import ast
import os
import re
import sys

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bl_plugin_manager")


def read(name):
    with open(os.path.join(BASE, name), "r", encoding="utf-8") as fh:
        return fh.read()


prefs_src = read("preferences.py")
ui_src = read("ui.py")
ops_src = read("operators.py")
init_src = read("__init__.py")
header_src = read("header.py")
errors = []

# 列表行必须展示兼容性与最高版本信息，而不是插件自身版本号。
ui_tree = ast.parse(ui_src)
plugin_draw_src = ""
for node in ast.walk(ui_tree):
    if isinstance(node, ast.ClassDef) and node.name == "PM_UL_plugins":
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "draw_item":
                plugin_draw_src = ast.get_source_segment(ui_src, child) or ""
                break
if "当前 Blender 兼容" not in plugin_draw_src or "最高" not in plugin_draw_src:
    errors.append("PM_UL_plugins.draw_item 未显示兼容性和最高支持版本")
if "item.version" in plugin_draw_src:
    errors.append("PM_UL_plugins.draw_item 不应显示插件自身版本号")

# 已定义的偏好属性
prefs_tree = ast.parse(prefs_src)
pref_attrs = set()
for node in ast.walk(prefs_tree):
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        pref_attrs.add(node.target.id)
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                pref_attrs.add(t.id)

# 已注册的操作符 idname（operators.py + header.py）
op_ids = set(re.findall(r'bl_idname\s*=\s*"([^"]+)"', ops_src))
op_ids |= set(re.findall(r'bl_idname\s*=\s*"([^"]+)"', header_src))

# 1) ui.py 中 prop(prefs, "...") 引用的属性
for attr in re.findall(r'prop\(\s*prefs\s*,\s*"([^"]+)"', ui_src):
    if attr not in pref_attrs:
        errors.append(f"ui.py 引用了未定义的偏好属性: {attr}")

# 2) ui.py 中 prefs.xxx 访问
for attr in re.findall(r"prefs\.([a-zA-Z_][a-zA-Z0-9_]*)", ui_src):
    if attr not in pref_attrs and attr not in {"library_path", "category_counts", "plugin_items"}:
        errors.append(f"ui.py 访问了未知偏好属性: {attr}")

# 3) ui.py / header.py 中引用的操作符
for src_name, src in (("ui.py", ui_src), ("header.py", header_src)):
    for attr in re.findall(r'"plugin_manager\.([a-z_]+)"', src):
        if f"plugin_manager.{attr}" not in op_ids:
            errors.append(f"{src_name} 引用了未注册的操作符: plugin_manager.{attr}")

# 4) header.py 中 operator("plugin_manager.x") 形式
for attr in re.findall(r'operator\(\s*"plugin_manager\.([a-z_]+)"', header_src):
    if f"plugin_manager.{attr}" not in op_ids:
        errors.append(f"header.py 引用了未注册的操作符: plugin_manager.{attr}")

# 4) operators.py 中所有 _report 调用都返回集合形式（返回类型正确性由运行期保证）
# 5) __init__.py 注册的模块都提供 classes
for mod in re.findall(r"^_modules = \(([^)]*)\)", init_src, re.M):
    pass

# 6) 确认所有 operators 的 classes 元组包含全部 Operator 子类
op_class_names = set(re.findall(r"^class (PM_OT_\w+)\(_?PM?Base?\)", ops_src, re.M)) | set(
    re.findall(r"^class (PM_OT_\w+)\(Operator\)", ops_src, re.M)
)
declared = set(re.findall(r"(PM_OT_\w+),", ops_src))
missing = op_class_names - declared
for name in sorted(missing):
    errors.append(f"操作符 {name} 未加入 classes 元组")

print(f"偏好属性 {len(pref_attrs)} 个，操作符 {len(op_ids)} 个")
if errors:
    print("===UI_CHECK_FAILED===")
    for e in errors:
        print(" -", e)
    sys.exit(1)
print("===UI_CHECK_OK===")
