"""验证简化后的 UI：菜单注册、面板/菜单真实绘制、操作符覆盖审计。"""
import importlib
import io
import json
import re
import sys

import bpy

out = {}

# 重载 ui（含菜单）
import bl_plugin_manager as PM
for name in ("constants", "scan", "db", "bridge", "library", "store", "updates",
             "migrate", "watcher", "items", "preferences", "operators", "ui", "header"):
    mod = getattr(PM, name, None)
    if mod is None:
        try:
            mod = importlib.import_module(f"bl_plugin_manager.{name}")
            setattr(PM, name, mod)
        except Exception as e:
            out[f"import_{name}"] = f"ERR {e}"
        continue
    try:
        importlib.reload(mod)
    except Exception as e:
        out[f"reload_{name}"] = f"ERR {e}"

_nul = io.StringIO()
_o, _e = sys.stdout, sys.stderr
sys.stdout = sys.stderr = _nul
try:
    for mod in (PM.preferences, PM.operators, PM.ui):
        for cls in getattr(mod, "classes", ()):
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
            try:
                bpy.utils.register_class(cls)
            except Exception as e:
                out.setdefault("reg_errors", []).append(f"{cls.__name__}: {e}")
    try:
        PM.header.unregister()
    except Exception:
        pass
    PM.header.register()
    out["reregister"] = "ok"
except Exception as e:
    out["reregister"] = f"ERR {e}"
finally:
    sys.stdout, sys.stderr = _o, _e

# 1) 面板与菜单是否注册
out["panels"] = [c for c in ("PM_PT_main", "PM_PT_detail", "PM_PT_batch",
                             "PM_PT_actions", "PM_PT_tools") if hasattr(bpy.types, c)]
out["menus"] = [c for c in ("PM_MT_library", "PM_MT_category", "PM_MT_plugin")
                if hasattr(bpy.types, c)]

# 2) 覆盖审计：ui.py 中引用的所有 plugin_manager.* 操作符是否都已注册
ui_src = open(r"E:\AI\geren\chajian_guanliqi\bl_plugin_manager\ui.py",
              encoding="utf-8").read()
referenced = sorted(set(re.findall(r'"plugin_manager\.([a-z_]+)"', ui_src)))
missing = [r for r in referenced if not hasattr(bpy.ops.plugin_manager, r)]
out["ui_referenced_ops"] = len(referenced)
out["ui_missing_ops"] = missing

# 3) 设计上只出现在对话框/弹出面板里的操作符（不算缺失）
dialog_only = {"store_install", "store_open", "store_sync", "show_report",
               "copy_report", "open_report_log", "assign_category", "set_category",
               "rename_category", "delete_category", "add_category", "reset_auto_categories",
               "pick_category", "enable_pack", "disable_pack", "set_category",
               "edit_meta", "rename_display", "import_plugin", "import_zip",
               "scan_inbox", "check_updates", "update_all", "unify_store",
               "pick_library_path", "setup_library", "refresh", "show_warnings",
               "unmount_library", "clear_updates", "toggle", "set_startup",
               "toggle_select", "select_all", "batch_enable", "batch_set_category",
               "batch_set_startup", "open_folder", "remove_plugin", "set_favorite",
               "scan_candidates", "import_candidates", "apply_startup", "header_popup"}

# 4) 真实绘制：面板 + 菜单
class L:
    """模拟布局：所有嵌套层级共享一个全局计数器，便于判断绘制深度。"""

    total = 0
    labels = []

    def __getattr__(self, k):
        def m(*a, **kw):
            L.total += 1
            t = kw.get("text")
            if isinstance(t, str) and t:
                L.labels.append(t)
            elif a and isinstance(a[0], str):
                L.labels.append(a[0])
            return L()
        return m


drawn = {}
prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
# 确保有选中项与分类，才能走到最深的绘制分支
from bl_plugin_manager import items as _it
_it.rebuild_items(prefs)
if prefs.plugin_items:
    prefs.plugin_items[0].selected = True
    prefs.active_index = 0
    prefs.selected_key = prefs.plugin_items[0].key

for cls_name in ("PM_PT_main", "PM_PT_detail", "PM_PT_batch", "PM_PT_actions", "PM_PT_tools"):
    cls = getattr(PM.ui, cls_name, None)
    if not cls:
        drawn[cls_name] = "MISSING"
        continue
    L.total = 0
    L.labels = []
    try:
        cls.draw(type("S", (), {"layout": L()})(), bpy.context)
        drawn[cls_name] = f"ok calls={L.total} labels={len(L.labels)}"
    except Exception as e:
        drawn[cls_name] = f"ERR {e}"
out["panels_drawn"] = drawn

for cls_name in ("PM_MT_library", "PM_MT_category", "PM_MT_plugin"):
    cls = getattr(PM.ui, cls_name, None)
    if not cls:
        out[f"menu_{cls_name}"] = "MISSING"
        continue
    class FakeMenu:
        pass

    L.total = 0
    L.labels = []
    fm = FakeMenu()
    fm.layout = L()
    try:
        cls.draw(fm, bpy.context)
        out[f"menu_{cls_name}"] = f"ok calls={L.total} labels={len(L.labels)}"
    except Exception as e:
        out[f"menu_{cls_name}"] = f"ERR {e}"

out["selected_key"] = prefs.selected_key
out["items"] = len(prefs.plugin_items)
out["detail_labels"] = L.labels[:0]

print("@@UI@@" + json.dumps(out, ensure_ascii=False, default=str))
