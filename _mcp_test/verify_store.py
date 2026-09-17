"""在运行实例中验证在线商店：目录浏览、搜索、弹窗绘制、真实安装与更新。"""
import io
import json
import os
import sys

import bpy

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
out = {}

# 重载（含新 store 模块）
import importlib
import bl_plugin_manager as PM
for name in ("constants", "scan", "db", "bridge", "library", "store", "updates",
             "migrate", "watcher", "items", "preferences", "operators", "ui"):
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
            except Exception:
                pass
    out["reregister"] = "ok"
except Exception as e:
    out["reregister"] = f"ERR {e}"
finally:
    sys.stdout, sys.stderr = _o, _e

prefs = bpy.context.preferences.addons["bl_plugin_manager"].preferences
prefs.library_path = LIB

# 目录与搜索
cat = PM.store.read_catalog()
out["catalog"] = len(cat)
db = PM.db.LibraryDB(LIB)
res = PM.store.search(cat, db, "node")
out["search_node"] = len(res)
res_new = PM.store.search(cat, db, "", only_compatible=True, only_new=True)
out["new_compatible"] = len(res_new)
out["installed_in_catalog"] = sum(1 for p in PM.store.search(cat, db, "") if p["installed"])
out["update_candidates_in_lib"] = sum(1 for r in db.plugins.values() if r.get("update_available"))

# 操作符存在性
for opn in ("store_open", "store_sync", "store_install", "update_all"):
    out[f"op_{opn}"] = hasattr(bpy.ops.plugin_manager, opn)


# 弹窗 draw 冒烟（模拟 layout）
class L:
    def __init__(self):
        self.n = 0
    def __getattr__(self, k):
        def m(*a, **kw):
            self.n += 1
            return L()
        return m


class FakeOp:
    bl_idname = "plugin_manager.store_open"
    def __init__(self, layout):
        self.layout = layout


lay = L()
try:
    PM.operators.PM_OT_store_open.draw(FakeOp(lay), None)
    out["store_popup_draw"] = f"ok calls={lay.n}"
except Exception as e:
    out["store_popup_draw"] = f"ERR {e}"

# 真实安装一个很小的扩展，然后卸载清理
small = [p for p in cat if p.get("type") == "add-on" and PM.store.is_compatible(p)
         and 0 < p.get("archive_size", 0) < 30000]
small.sort(key=lambda p: p["archive_size"])
if small:
    pkg = small[0]
    out["test_pkg"] = {"id": pkg["id"], "name": pkg["name"], "v": pkg["version"]}
    ok, err, rec = PM.store.install_package(pkg, LIB, PM.db.LibraryDB(LIB))
    out["real_install"] = {"ok": ok, "err": err,
                           "name": rec.get("name") if rec else None,
                           "module": rec.get("module") if rec else None}
    if ok and rec:
        d = PM.db.LibraryDB(LIB)
        d.remove(rec["key"])
        d.save()
        import shutil
        p = os.path.join(LIB, rec["rel"].replace("/", os.sep))
        shutil.rmtree(p, ignore_errors=True)
        PM.bridge.refresh_blender()
        out["cleanup"] = "ok"
        out["records_after"] = len(PM.db.LibraryDB(LIB).plugins)

print("@@STORE_LIVE@@" + json.dumps(out, ensure_ascii=False, default=str))
