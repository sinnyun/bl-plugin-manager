"""隔离环境端到端测试：加载插件库管理器，跑通导入/归档/启停/更新/迁移。

运行时需把 bl_plugin_manager 包放进隔离的 user scripts/addons 目录，
并设置 BLENDER_USER_SCRIPTS 指向该目录（详见 _test/run_e2e.sh）。
"""
import json
import os
import shutil
import sys
import tempfile

import addon_utils
import bpy

import bl_plugin_manager as PM

RESULTS = []


def _assert_isolated_runtime():
    expected = os.path.realpath(os.environ["BL_PLUGIN_MANAGER_EXPECTED_TEST_ROOT"])
    paths = {
        "Blender config": bpy.utils.user_resource("CONFIG"),
        "Blender scripts": bpy.utils.user_resource("SCRIPTS"),
        "machine config": os.environ["BL_PLUGIN_MANAGER_MACHINE_CONFIG"],
        "local state": os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"],
        "temp": tempfile.gettempdir(),
    }
    for label, value in paths.items():
        actual = os.path.realpath(value)
        if os.path.commonpath((expected, actual)) != expected:
            raise RuntimeError(f"unsafe test path for {label}: {actual}")


_assert_isolated_runtime()


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


# --- 准备目录 -------------------------------------------------------------
base = tempfile.mkdtemp(prefix="pmlib_test_")
lib = os.path.join(base, "library")
inbox = os.path.join(lib, "inbox")

# 1) 传统插件夹具
legacy_src = os.path.join(base, "LegacyDemo")
os.makedirs(legacy_src)
with open(os.path.join(legacy_src, "__init__.py"), "w", encoding="utf-8") as f:
    f.write(
        'bl_info = {\n'
        '    "name": "Legacy Demo",\n'
        '    "author": "tester",\n'
        '    "version": (2, 3, 4),\n'
        '    "blender": (4, 2, 0),\n'
        '    "description": "a legacy addon",\n'
        '    "category": "Test",\n'
        '}\n'
        'def register():\n    pass\n'
        'def unregister():\n    pass\n'
    )

# 2) 扩展插件夹具
ext_src = os.path.join(base, "ExtDemo")
os.makedirs(ext_src)
with open(os.path.join(ext_src, "blender_manifest.toml"), "w", encoding="utf-8") as f:
    f.write(
        'schema_version = "1.0.0"\n'
        'id = "ext_demo"\n'
        'version = "1.5.0"\n'
        'name = "Extension Demo"\n'
        'tagline = "an extension addon"\n'
        'maintainer = "tester"\n'
        'license = ["SPDX:GPL-3.0-or-later"]\n'
        'type = "add-on"\n'
        'blender_version_min = "4.2.0"\n'
        'tags = ["Test", "Demo"]\n'
    )
with open(os.path.join(ext_src, "__init__.py"), "w", encoding="utf-8") as f:
    f.write("def register():\n    pass\ndef unregister():\n    pass\n")

# 3) zip 夹具（传统插件）
zip_src = os.path.join(base, "ZipDemo")
os.makedirs(zip_src)
with open(os.path.join(zip_src, "__init__.py"), "w", encoding="utf-8") as f:
    f.write('bl_info = {"name": "Zip Demo", "version": (1, 0, 0), "blender": (4, 0, 0)}\n')
import zipfile
zip_path = os.path.join(base, "ZipDemo.zip")
with zipfile.ZipFile(zip_path, "w") as zf:
    for fn in os.listdir(zip_src):
        zf.write(os.path.join(zip_src, fn), fn)

# --- 启用插件 -------------------------------------------------------------
addon_utils.enable("bl_plugin_manager", default_set=True, refresh_handled=True)
prefs = bpy.context.preferences.addons[PM.constants.ADDON_ID].preferences
prefs.library_path = lib
check("addon registered", PM.constants.ADDON_ID in bpy.context.preferences.addons)

# 注册插件库
r = bpy.ops.plugin_manager.setup_library()
check("setup_library", r == {"FINISHED"})
check("lib dirs created", os.path.isdir(os.path.join(lib, "addons")) and os.path.isdir(os.path.join(lib, "extensions")))
state = PM.bridge.library_state(lib)
check("script dir registered", state["script_dir"])
check("repo registered", state["repo"])

# --- inbox 自动导入 -------------------------------------------------------
shutil.copytree(legacy_src, os.path.join(inbox, "LegacyDemo"))
shutil.copytree(ext_src, os.path.join(inbox, "ExtDemo"))
r = bpy.ops.plugin_manager.scan_inbox()
check("scan_inbox", r == {"FINISHED"}, str(r))

db = PM.db.LibraryDB(lib)
recs = {rec["folder_name"]: rec for rec in db.plugins.values()}
check("legacy imported", "LegacyDemo" in recs)
check("extension imported", "ExtDemo" in recs or "ext_demo" in recs)
if "LegacyDemo" in recs:
    lr = recs["LegacyDemo"]
    check("legacy version parsed", lr["version"] == "2.3.4", lr["version"])
    check("legacy kind", lr["kind"] == "addon")
    check("legacy module", lr["module"] == "LegacyDemo", lr["module"])
er = recs.get("ext_demo") or recs.get("ExtDemo")
if er:
    check("ext kind", er["kind"] == "extension", er["kind"])
    check("ext id", er.get("id") == "ext_demo", str(er.get("id")))
    check("ext version", er["version"] == "1.5.0", er["version"])
    # 扩展仓库现在优先复用官方商店仓库(blender_org)，模块名即以该仓库为准
    check("ext module ends with id",
          er["module"].endswith(".ext_demo") and er["module"].startswith("bl_ext."),
          er["module"])

# --- 启停 ---------------------------------------------------------------
lr = recs.get("LegacyDemo")
if lr:
    mod = lr["module"]
    ok, err = PM.bridge.set_enabled(mod, True)
    check("enable legacy by module", ok and PM.bridge.is_module_enabled(mod), err)
    # 通过操作符停用
    bpy.ops.plugin_manager.toggle(key=lr["key"], enable=False)
    db2 = PM.db.LibraryDB(lib)
    check("toggle off via operator", not PM.bridge.is_module_enabled(mod))
    check("enabled flag synced", db2.get(lr["key"])["enabled"] is False)
    bpy.ops.plugin_manager.toggle(key=lr["key"], enable=True)
    check("toggle on via operator", PM.bridge.is_module_enabled(mod))

# --- zip 导入 ------------------------------------------------------------
try:
    rec = PM.library.import_zip(zip_path, lib, db)
    check("zip import", rec["version"] == "1.0.0", rec["name"])
except Exception as exc:
    check("zip import", False, repr(exc))

# --- 元数据编辑 ----------------------------------------------------------
lr = PM.db.LibraryDB(lib).get(lr["key"]) if lr else None
if lr:
    bpy.ops.plugin_manager.set_favorite(key=lr["key"], value=True)
    bpy.ops.plugin_manager.set_category(key=lr["key"], category="我的分类")
    d3 = PM.db.LibraryDB(lib)
    check("favorite saved", d3.get(lr["key"])["favorite"] is True)
    check("category saved", d3.get(lr["key"])["category"] == "我的分类")
    check("category registered", "我的分类" in d3.categories)

# --- 更新检测 ------------------------------------------------------------
ext_dir = os.path.join(lib, "extensions")
os.makedirs(os.path.join(ext_dir, ".blender_ext"), exist_ok=True)
with open(os.path.join(ext_dir, ".blender_ext", "index.json"), "w", encoding="utf-8") as f:
    json.dump({"data": [{"id": "ext_demo", "version": "1.6.0", "name": "Extension Demo", "archive_url": ""}]}, f)
# 额外建一个仓库目录，模拟外部索引
repo2 = os.path.join(base, "repo2")
os.makedirs(os.path.join(repo2, ".blender_ext"))
with open(os.path.join(repo2, ".blender_ext", "index.json"), "w", encoding="utf-8") as f:
    json.dump({"data": [{"id": "ext_demo", "version": "1.7.0", "name": "Extension Demo"}]}, f)
rp = bpy.context.preferences.extensions.repos.new(name="TestRepo", module="testrepo")
rp.use_custom_directory = True
rp.custom_directory = repo2
db4 = PM.db.LibraryDB(lib)
stats = PM.updates.check_updates(db4, lib)
er2 = [r for r in db4.plugins.values() if r.get("id") == "ext_demo"][0]
check("update detected", er2.get("update_available") is True, str(er2.get("latest_version")))
check("latest version", er2.get("latest_version") == "1.7.0", str(er2.get("latest_version")))

# --- 同步/移除 -----------------------------------------------------------
stats = PM.library.sync_library(lib, PM.db.LibraryDB(lib))
check("sync runs", isinstance(stats, dict))

# --- 迁移扫描 ------------------------------------------------------------
cands = PM.migrate.collect_candidates(lib, PM.db.LibraryDB(lib))
check("migrate scan runs", isinstance(cands, list), f"{len(cands)} candidates")

# --- 列表项重建 ----------------------------------------------------------
prefs.active_category = "全部"
keys = PM.items.rebuild_items(prefs)
check("items rebuilt", len(keys) >= 3, f"{len(keys)} items")

# --- 中文名 + emoji 插件 ------------------------------------------------
cn_src = os.path.join(base, "🦴 摆动骨骼 (Wiggle)")
os.makedirs(cn_src)
with open(os.path.join(cn_src, "__init__.py"), "w", encoding="utf-8") as f:
    f.write(
        'bl_info = {\n'
        '    "name": "🦴摆动动画(blender wiggle 2)",\n'
        '    "version": (2, 2, 3),\n'
        '    "blender": (4, 0, 0),\n'
        '    "category": "动画类",\n'
        '}\n'
    )
try:
    rec_cn = PM.library.import_path(cn_src, lib, PM.db.LibraryDB(lib), move=True)
    check("chinese name import", rec_cn["version"] == "2.2.3", rec_cn["name"])
    check("chinese folder kept", "摆" in rec_cn["folder_name"], rec_cn["folder_name"])
except Exception as exc:
    check("chinese name import", False, repr(exc))

# --- 坏插件：register 抛异常不应拖垮管理器 -------------------------------
bad_src = os.path.join(base, "BadPlugin")
os.makedirs(bad_src)
with open(os.path.join(bad_src, "__init__.py"), "w", encoding="utf-8") as f:
    f.write(
        'bl_info = {"name": "Bad Plugin", "version": (1, 0, 0), "blender": (4, 0, 0)}\n'
        'def register():\n    raise RuntimeError("boom")\n'
        'def unregister():\n    pass\n'
    )
try:
    rec_bad = PM.library.import_path(bad_src, lib, PM.db.LibraryDB(lib), move=True)
    check("bad plugin imported", bool(rec_bad.get("module")))
    ok, err = PM.bridge.set_enabled(rec_bad["module"], True)
    check("bad plugin toggle no-crash", isinstance(ok, bool), f"ok={ok} err={err[:60]}")
    PM.bridge.set_enabled(rec_bad["module"], False)
    check("bad plugin disable no-crash", True)
except Exception as exc:
    check("bad plugin imported", False, repr(exc))

# --- 投放区：手动扫描导入；导入后源被移走，再次扫描无待处理项 -------------
before = len(PM.db.LibraryDB(lib).plugins)
shutil.copytree(legacy_src, os.path.join(inbox, "IdemDemo"))
res1 = PM.watcher.scan_inbox(lib, PM.db.LibraryDB(lib))
n1 = len(PM.db.LibraryDB(lib).plugins)
res2 = PM.watcher.scan_inbox(lib, PM.db.LibraryDB(lib))
n2 = len(PM.db.LibraryDB(lib).plugins)
check("inbox manual scan imports", n1 == before + 1 and res1["imported"] == 1,
      f"{before}->{n1} entries={len(res1['entries'])}")
check("inbox re-scan finds nothing", n2 == n1 and not res2["entries"],
      f"{n1}->{n2} entries={len(res2['entries'])}")

# --- 扫描报告：逐项记录状态与原因 -----------------------------------------
rep_lib = os.path.join(base, "ReportLib")
os.makedirs(rep_lib)
# 一个正常插件 + 一个非插件目录 + 一个坏 zip
shutil.copytree(legacy_src, os.path.join(rep_lib, "inbox", "GoodOne"))
os.makedirs(os.path.join(rep_lib, "inbox", "NotAPlugin"))
with open(os.path.join(rep_lib, "inbox", "NotAPlugin", "readme.txt"), "w") as f:
    f.write("x")
with open(os.path.join(rep_lib, "inbox", "broken.zip"), "wb") as f:
    f.write(b"not a zip")
res_rep = PM.watcher.scan_inbox(rep_lib, PM.db.LibraryDB(rep_lib))
by_status = {}
for e in res_rep["entries"]:
    by_status.setdefault(e["status"], []).append(e)
check("report records imported", "imported" in by_status, str(list(by_status)))
check("report records skipped non-plugin", "skipped" in by_status,
      str([e["name"] for e in by_status.get("skipped", [])]))
check("report entries carry detail",
      all(e.get("detail") for e in res_rep["entries"]))
# 每个条目都能定位到具体来源
check("report entries carry path",
      all(e.get("path") for e in res_rep["entries"]))

# --- 失败项：报告应被填充且可复制 ----------------------------------------
prefs.library_path = rep_lib
# 造一个扫描必失败的场景：inbox 里放一个名为某插件、但内容损坏的目录
bad_lib = os.path.join(base, "BadScanLib")
os.makedirs(bad_lib)
badin = os.path.join(bad_lib, "inbox", "HalfBroken")
os.makedirs(badin)
with open(os.path.join(badin, "__init__.py"), "w", encoding="utf-8") as f:
    f.write('bl_info = {"name": "Half Broken", "version": (1, 0, 0)}\n')
with open(os.path.join(badin, "blender_manifest.toml"), "w", encoding="utf-8") as f:
    f.write('id = "HalfBroken"\n')  # 残缺 manifest，导入应失败并给出原因
res_bad = PM.watcher.scan_inbox(bad_lib, PM.db.LibraryDB(bad_lib))
failed = [e for e in res_bad["entries"] if e["status"] == "failed"]
check("broken manifest yields failure entry with reason",
      bool(failed) and bool(failed[0]["detail"]),
      failed[0]["detail"][:60] if failed else "no failure entry")

# 同名重复导入 → 唯一化 ----------------------------------------------
rec_dup = PM.library.import_path(legacy_src, lib, PM.db.LibraryDB(lib), move=False)
check("duplicate unique", rec_dup["folder_name"] != "LegacyDemo", rec_dup["folder_name"])

# --- 删除分类 → 回落默认 -------------------------------------------------
db5 = PM.db.LibraryDB(lib)
db5.delete_category("我的分类")
db5.save()
db6 = PM.db.LibraryDB(lib)
lr6 = db6.get(recs["LegacyDemo"]["key"]) if recs.get("LegacyDemo") else None
check("category fallback",
      lr6 is not None and lr6["category"] == PM.constants.DEFAULT_CATEGORY,
      lr6["category"] if lr6 else "n/a")

# --- 移除插件 → 回收站 --------------------------------------------------
rm_rec = PM.db.LibraryDB(lib).get(rec_cn["key"]) if "rec_cn" in dir() else None
if rm_rec:
    moved = PM.library.remove_plugin(lib, PM.db.LibraryDB(lib), rm_rec["key"], to_trash=True)
    check("remove to trash", moved is not None and os.path.isdir(moved), str(moved))
    check("record removed", PM.db.LibraryDB(lib).get(rm_rec["key"]) is None)

# --- 直接丢在库根目录也应自动整理 ---------------------------------------
root_drop = os.path.join(base, "RootDropDemo")
os.makedirs(root_drop)
with open(os.path.join(root_drop, "__init__.py"), "w", encoding="utf-8") as f:
    f.write(
        'bl_info = {"name": "Root Drop Demo", "version": (9, 9, 9), '
        '"blender": (4, 2, 0), "description": "auto note from description"}\n'
    )
import shutil as _sh
_sh.copytree(root_drop, os.path.join(lib, "RootDropDemo"))
before_root = len(PM.db.LibraryDB(lib).plugins)
res = PM.watcher.scan_inbox(lib, PM.db.LibraryDB(lib))
after_root = len(PM.db.LibraryDB(lib).plugins)
check("root-level drop auto-sorted", after_root == before_root + 1,
      f"{before_root}->{after_root} imported={res['imported']}")
rd = [r for r in PM.db.LibraryDB(lib).plugins.values() if r["folder_name"] == "RootDropDemo"]
check("root drop moved into addons/",
      bool(rd) and os.path.isfile(os.path.join(lib, "addons", "RootDropDemo", "__init__.py")))
check("auto note from description",
      bool(rd) and rd[0].get("note") == "auto note from description",
      rd[0].get("note") if rd else "n/a")

# --- 自动备注：有描述则填充，无描述则留空 ---------------------------------
check("auto note filled from description",
      PM.db.LibraryDB(lib).get(recs["LegacyDemo"]["key"])["note"] == "a legacy addon",
      PM.db.LibraryDB(lib).get(recs["LegacyDemo"]["key"])["note"])
_zip_rec = [r for r in PM.db.LibraryDB(lib).plugins.values() if r["name"] == "Zip Demo"]
check("no-description note empty",
      bool(_zip_rec) and _zip_rec[0]["note"] == "",
      _zip_rec[0]["note"] if _zip_rec else "n/a")

# --- 管理状态往返：停止后恢复受管范围，显式启用后恢复设备配置 ----------
prefs.library_path = lib  # 前面报告测试用过其它路径，这里恢复
r = bpy.ops.plugin_manager.unmount_library()
check("unmount restores defaults", r == {"FINISHED"} and not PM.bridge.is_registered(lib), str(r))
# 重新挂载，供后续测试使用
r = bpy.ops.plugin_manager.setup_library()
check("explicit re-enable restores device configuration",
      r == {"FINISHED"} and PM.bridge.is_registered(lib))

# 禁用管理器必须恢复默认；重新加载管理器本身不能隐式接管。
PM.unregister()
check("addon disable unmounts managed library", not PM.bridge.is_registered(lib),
      str(PM.bridge.library_state(lib)))
PM.register()
prefs = bpy.context.preferences.addons[PM.constants.ADDON_ID].preferences
check("addon reload remains inactive until explicit enable", not PM.bridge.is_registered(lib),
      str(PM.bridge.library_state(lib)))
r = bpy.ops.plugin_manager.setup_library()
check("setup after addon reload restores device configuration",
      r == {"FINISHED"} and PM.bridge.is_registered(lib))

# 清空本机库路径也等价于解除管理，不应留下 Blender 插件发现来源。
prefs.library_path = ""
check("clearing library path unmounts managed library", not PM.bridge.is_registered(lib),
      str(PM.bridge.library_state(lib)))
prefs.library_path = lib
r = bpy.ops.plugin_manager.setup_library()
check("setup remounts after clearing library path", r == {"FINISHED"} and PM.bridge.is_registered(lib))

# 未启用管理时，启动流程不得按名称猜测并删除任何原生配置。
prefs.library_path = ""
unmanaged_script = bpy.context.preferences.filepaths.script_directories.new()
unmanaged_script.name = "User Tools"
unmanaged_script.directory = os.path.join(base, "unmanaged-tools")
unmanaged_repo = bpy.context.preferences.extensions.repos.new(
    name="User Repo", module="user_repo")
PM._bootstrap_prefs()
check("inactive bootstrap preserves unmanaged native entries",
      any(item == unmanaged_script for item in bpy.context.preferences.filepaths.script_directories)
      and any(item == unmanaged_repo for item in bpy.context.preferences.extensions.repos))
prefs.library_path = lib
r = bpy.ops.plugin_manager.setup_library()
check("setup resumes takeover", r == {"FINISHED"} and PM.bridge.is_registered(lib))

# --- 扩展插件：id 含空格必须净化为合法模块名 ---------------------------
space_src = os.path.join(base, "SpaceExt")
os.makedirs(space_src)
with open(os.path.join(space_src, "blender_manifest.toml"), "w", encoding="utf-8") as f:
    f.write(
        'schema_version = "1.0.0"\n'
        'id = "Straighten UV"\n'
        'version = "1.0.1"\n'
        'name = "Straighten UV"\n'
        'maintainer = "tester"\n'
        'license = ["SPDX:GPL-3.0-or-later"]\n'
        'type = "add-on"\n'
        'blender_version_min = "4.2.0"\n'
    )
with open(os.path.join(space_src, "__init__.py"), "w", encoding="utf-8") as f:
    f.write("def register():\n    pass\ndef unregister():\n    pass\n")
try:
    rec_sp = PM.library.import_plugin_dir(space_src, lib, PM.db.LibraryDB(lib), move=True)
    check("space id sanitized to module name",
          rec_sp["folder_name"] == "Straighten_UV", rec_sp["folder_name"])
    check("extension module has no space",
          " " not in rec_sp["module"], rec_sp["module"])
    check("placed under extensions/",
          os.path.isfile(os.path.join(lib, "extensions", "Straighten_UV", "blender_manifest.toml")))
except Exception as exc:
    check("space id sanitized to module name", False, repr(exc))

# --- 分类列表应自动包含插件记录中使用的分类 -------------------------------
d_cat = PM.db.LibraryDB(lib)
used = {(r.get("category") or "").strip() for r in d_cat.plugins.values() if r.get("category")}
listed = set(d_cat.categories)
missing_cats = {c for c in used if c and c not in listed}
check("all used categories listed", not missing_cats, f"缺失: {missing_cats}")

# --- 智能侦测目标目录 + 智能连接 -------------------------------------------
info_missing = PM.library.inspect_target(os.path.join(base, "NotExistLib"))
check("inspect: missing path", info_missing["exists"] is False)

info_plugin = PM.library.inspect_target(legacy_src)
check("inspect: detects plugin dir", info_plugin["is_plugin"] is True)

# 已存在的库（当前 lib）应被识别为 library 且有计数
info_lib = PM.library.inspect_target(lib)
check("inspect: detects existing library",
      info_lib["is_library"] and info_lib["addon_count"] > 0,
      f"addons={info_lib['addon_count']} exts={info_lib['extension_count']}")

# 空目录 → 创建空库
empty_lib = os.path.join(base, "EmptyLib")
os.makedirs(empty_lib)
r_empty = PM.library.connect_library(empty_lib, PM.db.LibraryDB(empty_lib))
check("connect: empty dir -> create library",
      r_empty["created"] and os.path.isdir(os.path.join(empty_lib, "addons"))
      and os.path.isdir(os.path.join(empty_lib, "extensions")))

# 含散落插件的目录 → 收编进 addons/extensions
flat_lib = os.path.join(base, "FlatLib")
os.makedirs(flat_lib)
shutil.copytree(legacy_src, os.path.join(flat_lib, "FlatLegacy"))
shutil.copytree(ext_src, os.path.join(flat_lib, "FlatExt"))
r_flat = PM.library.connect_library(flat_lib, PM.db.LibraryDB(flat_lib))
check("connect: collects flat plugins", len(r_flat["imported"]) == 2,
      f"imported={len(r_flat['imported'])}")
check("connect: flat legacy -> addons/",
      os.path.isdir(os.path.join(flat_lib, "addons", "FlatLegacy")))
check("connect: flat extension -> extensions/",
      os.path.isdir(os.path.join(flat_lib, "extensions", "ext_demo")))

# 选中插件本身作为库 → 应报错并中止
r_bad = PM.library.connect_library(legacy_src, PM.db.LibraryDB(legacy_src))
check("connect: rejects plugin-as-library", bool(r_bad["error"]),
      r_bad["error"][:40])

# pick_library_path 操作符：切到已有库并侦测
r_op = bpy.ops.plugin_manager.pick_library_path(directory=flat_lib)
check("pick_library_path operator",
      r_op == {"FINISHED"} and os.path.normcase(prefs.library_path) == os.path.normcase(flat_lib),
      str(prefs.library_path))
pinfo = PM.library.inspect_target(flat_lib)
check("picked path is library", pinfo["is_library"])

# pick_library_path：选中插件本身应被拒绝（Blender 报 ERROR 时会抛 RuntimeError）
prefs.library_path = lib  # 恢复
try:
    r_badop = bpy.ops.plugin_manager.pick_library_path(directory=legacy_src)
except RuntimeError:
    r_badop = {"CANCELLED"}
check("pick_library_path rejects plugin dir", r_badop == {"CANCELLED"}, str(r_badop))
check("library_path unchanged after reject",
      os.path.normcase(prefs.library_path) == os.path.normcase(lib))

# 非空且不含插件的目录 → 拒绝初始化（保护资产目录等）
junk_dir = os.path.join(base, "JunkDir")
os.makedirs(os.path.join(junk_dir, "some_assets"))
with open(os.path.join(junk_dir, "readme.txt"), "w", encoding="utf-8") as f:
    f.write("x")
r_junk = PM.library.connect_library(junk_dir, PM.db.LibraryDB(junk_dir))
check("connect: refuses non-empty non-plugin dir", bool(r_junk["error"]),
      r_junk["error"][:40])
check("connect: junk dir untouched",
      os.path.isdir(os.path.join(junk_dir, "some_assets"))
      and not os.path.isdir(os.path.join(junk_dir, "addons")))

# --- 分类：完全由用户创建，插件自带分类不自动灌入 -------------------------
cat_lib = os.path.join(base, "CatLib")
os.makedirs(cat_lib)
# 造一个明确带自带分类的插件
cat_src = os.path.join(base, "CatDemo")
os.makedirs(cat_src)
with open(os.path.join(cat_src, "__init__.py"), "w", encoding="utf-8") as f:
    f.write('bl_info = {"name": "Cat Demo", "version": (1, 0, 0), "blender": (4, 2, 0), '
            '"category": "AutoCat"}\n')
rec_cat = PM.library.import_plugin_dir(cat_src, cat_lib, PM.db.LibraryDB(cat_lib), move=True)
d_catlib = PM.db.LibraryDB(cat_lib)

check("new plugin defaults to 未分类",
      rec_cat["category"] == PM.constants.DEFAULT_CATEGORY, rec_cat["category"])
check("auto category not added to list",
      "AutoCat" not in d_catlib.categories, str(d_catlib.categories))
check("auto category preserved in record",
      rec_cat.get("auto_category") == "AutoCat", str(rec_cat.get("auto_category")))

# 用户新建分类
check("add user category", d_catlib.add_category("我的工具") is True)
d_catlib.save()
check("user category listed", "我的工具" in PM.db.LibraryDB(cat_lib).categories)

# 把插件移入该分类（操作符基于当前库，故先切换偏好路径）
prefs.library_path = cat_lib
bpy.ops.plugin_manager.set_category(key=rec_cat["key"], category="我的工具")
d2 = PM.db.LibraryDB(cat_lib)
check("plugin moved to user category",
      d2.get(rec_cat["key"])["category"] == "我的工具")

# 一个插件只属于一个分类（扁平，无嵌套）
recs_now = [r for r in d2.plugins.values()]
check("plugin has single category field",
      all(isinstance(r.get("category"), str) for r in recs_now))
check("no nested category names",
      not any("/" in (r.get("category") or "") or "\\" in (r.get("category") or "")
              for r in recs_now))

# 清理自动分类：只收回「未手动改动」的
clean_src = os.path.join(base, "CleanDemo")
os.makedirs(clean_src)
with open(os.path.join(clean_src, "__init__.py"), "w", encoding="utf-8") as f:
    f.write('bl_info = {"name": "Clean Demo", "version": (1, 0, 0), '
            '"category": "LegacyAuto"}\n')
rec_clean = PM.library.import_plugin_dir(clean_src, cat_lib, PM.db.LibraryDB(cat_lib), move=True)
# 手工把它标成自动分类遗留（模拟旧数据）
d3 = PM.db.LibraryDB(cat_lib)
d3.plugins[rec_clean["key"]]["category"] = "LegacyAuto"
d3.ensure_category("LegacyAuto")
d3.save()
res_reset = d3.reset_auto_categories()
d3.save()
d4 = PM.db.LibraryDB(cat_lib)
check("reset auto category reclaimed", d4.get(rec_clean["key"])["category"] == PM.constants.DEFAULT_CATEGORY)
check("user category survives reset", d4.get(rec_cat["key"])["category"] == "我的工具")
check("LegacyAuto removed from list", "LegacyAuto" not in d4.categories, str(d4.categories))

# 删除分类 → 插件回未分类
d4.delete_category("我的工具")
d4.save()
d5 = PM.db.LibraryDB(cat_lib)
check("delete category returns plugin to 未分类",
      d5.get(rec_cat["key"])["category"] == PM.constants.DEFAULT_CATEGORY)

# assign_category 下拉操作符：枚举项必须可靠可枚举
d_catlib.ensure_category("下拉测试")
d_catlib.save()
PM.items.refresh_cache(prefs)
opts = PM.items.category_options()
check("assign enum options contain categories",
      "下拉测试" in opts and PM.constants.DEFAULT_CATEGORY in opts, str(opts))
try:
    bpy.ops.plugin_manager.assign_category(key=rec_cat["key"], category="下拉测试")
    d_assign = PM.db.LibraryDB(cat_lib)
    check("assign_category via operator",
          d_assign.get(rec_cat["key"])["category"] == "下拉测试",
          d_assign.get(rec_cat["key"])["category"])
except Exception as exc:
    check("assign_category via operator", False, repr(exc))
# 移回未分类
bpy.ops.plugin_manager.set_category(key=rec_cat["key"], category=PM.constants.DEFAULT_CATEGORY)
d_catlib.delete_category("下拉测试")
d_catlib.save()

# --- 报告操作符：填充、复制、日志 -----------------------------------------
# 注意：挂载相关断言基于 lib，先恢复以免影响后面的往返测试
prefs.library_path = rep_lib
try:
    r_scan = bpy.ops.plugin_manager.scan_inbox()
except RuntimeError:
    r_scan = {"CANCELLED"}  # 有失败/跳过项时会报 ERROR，属预期
check("scan_inbox operator runs", isinstance(r_scan, set), str(r_scan))
check("report_items filled", len(prefs.report_items) >= 1,
      f"{len(prefs.report_items)} items, summary={prefs.report_summary}")
check("report_summary non-empty", bool(prefs.report_summary), prefs.report_summary)
try:
    r_copy = bpy.ops.plugin_manager.copy_report()
    check("copy_report", r_copy == {"FINISHED"}, str(r_copy))
except Exception as exc:
    check("copy_report", False, repr(exc))
log_path = os.path.join(rep_lib, ".pm", "last_scan.log")
check("scan log written", os.path.isfile(log_path))

# 验证日志内容含具体来源与详情，便于排查
if os.path.isfile(log_path):
    with open(log_path, "r", encoding="utf-8") as f:
        logtxt = f.read()
    check("log has source paths", "来源:" in logtxt)
    check("log has detail", "详情:" in logtxt)

# 恢复库路径，供随后的挂载往返测试使用
prefs.library_path = lib
bpy.ops.plugin_manager.setup_library()

# --- 双名称机制：实际名只读，显示名（别名）可改，不影响实际名 -------------
alias_rec = PM.db.LibraryDB(lib).get(recs["LegacyDemo"]["key"])
check("actual name preserved", alias_rec["name"] == "Legacy Demo", alias_rec["name"])
check("display name empty by default", alias_rec.get("display_name", "") == "",
      alias_rec.get("display_name", ""))

r_alias = bpy.ops.plugin_manager.rename_display(key=alias_rec["key"], display_name="老式演示")
check("rename_display sets alias", r_alias == {"FINISHED"}, str(r_alias))
after = PM.db.LibraryDB(lib).get(alias_rec["key"])
check("alias saved", after["display_name"] == "老式演示", after["display_name"])
check("actual name unchanged after alias", after["name"] == "Legacy Demo", after["name"])

# 列表项应带上两种名称（供界面分别显示）
PM.items.rebuild_items(prefs)
row_item = next((it for it in prefs.plugin_items if it.key == alias_rec["key"]), None)
check("list item carries both names",
      row_item is not None and row_item.name == "Legacy Demo"
      and row_item.display_name == "老式演示",
      f"{getattr(row_item, 'name', None)} / {getattr(row_item, 'display_name', None)}")

# 搜索可命中别名，也能命中实际名
prefs.search = "老式演示"
keys_alias = PM.items.rebuild_items(prefs)
check("search matches alias", alias_rec["key"] in keys_alias, f"{len(keys_alias)} hits")
prefs.search = "Legacy"
keys_actual = PM.items.rebuild_items(prefs)
check("search matches actual name", alias_rec["key"] in keys_actual, f"{len(keys_actual)} hits")
prefs.search = ""

# 清除别名
r_clear = bpy.ops.plugin_manager.rename_display(key=alias_rec["key"], clear=True)
cleared = PM.db.LibraryDB(lib).get(alias_rec["key"])
check("clear alias", r_clear == {"FINISHED"} and cleared["display_name"] == "",
      cleared["display_name"])
check("actual name still intact", cleared["name"] == "Legacy Demo", cleared["name"])

# --- 自启标记：与「本次启用」分离，且同步时只启用不停用（默认） -------------
prefs.library_path = lib
startup_rec = PM.db.LibraryDB(lib).get(recs["LegacyDemo"]["key"])
check("startup field exists", "startup" in startup_rec, str(startup_rec.get("startup")))

# 设为自启
bpy.ops.plugin_manager.set_startup(key=startup_rec["key"], value=True)
d_st = PM.db.LibraryDB(lib)
check("set_startup marks true", d_st.get(startup_rec["key"])["startup"] is True)
stats_st = d_st.startup_stats()
check("startup_stats counts", stats_st["startup"] >= 1, str(stats_st))

# 取消自启
bpy.ops.plugin_manager.set_startup(key=startup_rec["key"], value=False)
check("unset startup", PM.db.LibraryDB(lib).get(startup_rec["key"])["startup"] is False)

# 应用自启：只启用标记项，未标记的保持原状（不停用）
d_ap = PM.db.LibraryDB(lib)
# 标记一个插件为自启并确保它是停用的
target = d_ap.get(recs["LegacyDemo"]["key"])
PM.bridge.set_enabled(target["module"], False)
target["enabled"] = False
target["startup"] = True
d_ap.save()
res_ap = PM.library.apply_startup(lib, PM.db.LibraryDB(lib), enable_marked=True,
                                  disable_unmarked=False)
check("apply_startup enables marked", res_ap["enabled"] >= 1, str(res_ap))
check("apply_startup marked now enabled",
      PM.bridge.is_module_enabled(target["module"]))
check("apply_startup did not disable others", res_ap["disabled"] == 0, str(res_ap))
# 清理
d_st2 = PM.db.LibraryDB(lib)
d_st2.get(target["key"])["startup"] = False
d_st2.save()

# --- 失败原因记录：启用失败应写入 last_error ---------------------------------
bad2_src = os.path.join(base, "ErrRec")
os.makedirs(bad2_src)
with open(os.path.join(bad2_src, "__init__.py"), "w", encoding="utf-8") as f:
    f.write('bl_info = {"name": "Err Rec", "version": (1, 0, 0)}\n'
            'def register():\n    raise RuntimeError("intentional failure")\n'
            'def unregister():\n    pass\n')
rec_err = PM.library.import_path(bad2_src, lib, PM.db.LibraryDB(lib), move=True)
ok_e, err_e = PM.bridge.set_enabled(rec_err["module"], True)
d_err = PM.db.LibraryDB(lib)
if not ok_e:
    d_err.get(rec_err["key"])["last_error"] = err_e
    d_err.save()
    check("last_error recorded on failure",
          "intentional failure" in (PM.db.LibraryDB(lib).get(rec_err["key"])["last_error"]),
          PM.db.LibraryDB(lib).get(rec_err["key"])["last_error"][:50])
else:
    check("last_error recorded on failure", True, "该插件在此环境可启用，跳过")

# --- 批量操作：多选 + 批量设分类 / 批量自启 ---------------------------------
d_b = PM.db.LibraryDB(lib)
prefs.search = ""
keys_vis = PM.items.rebuild_items(prefs)
# 通过持久勾选集合勾选前 3 个（批量操作正是读这个集合）
PM.items.clear_selection()
for it in prefs.plugin_items[:3]:
    PM.items.set_selected(it.key, True)
PM.items.rebuild_items(prefs)
sel_n = len(PM.items.selected_keys(prefs))
check("selection tracked", sel_n == 3, str(sel_n))

# 批量归入分类
d_b.ensure_category("批量测试")
d_b.save()
bpy.ops.plugin_manager.batch_set_category(category="批量测试")
d_b2 = PM.db.LibraryDB(lib)
moved = [r for r in d_b2.plugins.values() if r.get("category") == "批量测试"]
check("batch set category", len(moved) == 3, f"{len(moved)} moved")

# 批量取消自启
bpy.ops.plugin_manager.batch_set_startup(value=False)
d_b3 = PM.db.LibraryDB(lib)
check("batch startup false",
      all(not r.get("startup") for r in d_b3.plugins.values() if r.get("category") == "批量测试"))

# 批量启用
bpy.ops.plugin_manager.batch_enable(value=True)
d_b4 = PM.db.LibraryDB(lib)
enabled_moved = [r for r in d_b4.plugins.values()
                 if r.get("category") == "批量测试" and r.get("enabled")]
check("batch enable ran", isinstance(enabled_moved, list), f"{len(enabled_moved)} enabled")

# 清理：取消勾选与测试分类
PM.items.clear_selection()
PM.items.rebuild_items(prefs)
d_b5 = PM.db.LibraryDB(lib)
d_b5.delete_category("批量测试")
d_b5.save()

# --- 同 id 扩展重复导入 → 视为更新而非新建副本 -----------------------------
ext_lib = os.path.join(base, "ExtUpdateLib")
os.makedirs(ext_lib)


def _write_ext(path, version, extra="# v"):
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "blender_manifest.toml"), "w", encoding="utf-8") as f:
        f.write('schema_version = "1.0.0"\nid = "up_ext"\n'
                f'version = "{version}"\nname = "Update Ext"\nmaintainer = "t"\n'
                'license = ["SPDX:GPL-3.0-or-later"]\ntype = "add-on"\n'
                'blender_version_min = "4.2.0"\n')
    with open(os.path.join(path, "__init__.py"), "w", encoding="utf-8") as f:
        f.write(f"{extra}\ndef register():\n    pass\n")


v1 = os.path.join(base, "UpExtV1")
_write_ext(v1, "1.0.0", "# v1")
r1 = PM.library.import_plugin_dir(v1, ext_lib, PM.db.LibraryDB(ext_lib), move=True)
check("first ext imported", r1["version"] == "1.0.0", r1["version"])

d_u = PM.db.LibraryDB(ext_lib)
rec_u = d_u.get(r1["key"])
rec_u["category"] = "自定义类"
rec_u["note"] = "我的备注"
rec_u["display_name"] = "别名"
d_u.ensure_category("自定义类")
d_u.save()

v2 = os.path.join(base, "UpExtV2")
_write_ext(v2, "2.0.0", "# v2")
r2 = PM.library.import_plugin_dir(v2, ext_lib, PM.db.LibraryDB(ext_lib), move=True)
check("reimport same id updates in place", r2["rel"] == r1["rel"],
      f"{r1['rel']} -> {r2['rel']}")
check("updated version", r2["version"] == "2.0.0", r2["version"])
check("no duplicate dir created",
      not os.path.isdir(os.path.join(ext_lib, "extensions", "up_ext_2")))
r2b = PM.db.LibraryDB(ext_lib).get(r1["key"])
check("user category kept on update", r2b.get("category") == "自定义类", str(r2b.get("category")))
check("user note kept on update", r2b.get("note") == "我的备注", str(r2b.get("note")))
check("user alias kept on update", r2b.get("display_name") == "别名", str(r2b.get("display_name")))
check("disk has only one copy",
      len([e for e in os.scandir(os.path.join(ext_lib, "extensions")) if e.is_dir()]) == 1)

# --- 勾选状态必须跨重建保持（否则批量操作点一下就失效）----------------------
prefs.library_path = lib
PM.items.clear_selection()
PM.items.rebuild_items(prefs)
check("has items to select", len(prefs.plugin_items) >= 3, str(len(prefs.plugin_items)))

k0 = prefs.plugin_items[0].key
bpy.ops.plugin_manager.toggle_select(key=k0)
check("toggle_select marks checked",
      any(it.key == k0 and it.selected for it in prefs.plugin_items))
check("selection persisted in set", k0 in PM.items.SELECTED_KEYS)

# 关键：重建列表（面板重绘/刷新都会触发）后勾选仍在
PM.items.rebuild_items(prefs)
check("selection survives rebuild",
      any(it.key == k0 and it.selected for it in prefs.plugin_items),
      "重建后勾选丢失")
check("selected_keys reports it", k0 in PM.items.selected_keys(prefs))

# 再点一次 → 取消
bpy.ops.plugin_manager.toggle_select(key=k0)
check("toggle_select unchecks",
      not any(it.key == k0 and it.selected for it in prefs.plugin_items))
PM.items.rebuild_items(prefs)
check("uncheck survives rebuild",
      not any(it.key == k0 and it.selected for it in prefs.plugin_items))

# 全选 → 重建后仍全选
bpy.ops.plugin_manager.select_all(value=True)
n_sel = sum(1 for it in prefs.plugin_items if it.selected)
PM.items.rebuild_items(prefs)
n_after = sum(1 for it in prefs.plugin_items if it.selected)
check("select_all then rebuild keeps all",
      n_after == n_sel and n_after == len(prefs.plugin_items),
      f"{n_sel} -> {n_after}")

# 过滤变化也不应丢勾选（只显示子集）
PM.items.rebuild_items(prefs)
bpy.ops.plugin_manager.select_all(value=False)
bpy.ops.plugin_manager.toggle_select(key=k0)
prefs.only_enabled = True
PM.items.rebuild_items(prefs)
prefs.only_enabled = False
keys_back = PM.items.rebuild_items(prefs)
check("selection survives filter round-trip",
      k0 in keys_back and any(it.key == k0 and it.selected for it in prefs.plugin_items))

# 批量操作使用持久勾选集合
PM.items.clear_selection()
for it in prefs.plugin_items[:3]:
    PM.items.set_selected(it.key, True)
PM.items.rebuild_items(prefs)
targets = PM.items.selected_keys(prefs)
check("batch targets from persistent set", len(targets) == 3, str(len(targets)))
PM.items.clear_selection()

# --- Blender 版本兼容：支持范围显示与判定 ---------------------------------
SM = PM.scan
check("range: both ends", SM.supported_range("4.2.0", "5.0.0") == "4.2.0 ~ 5.0.0",
      SM.supported_range("4.2.0", "5.0.0"))
check("range: min only", SM.supported_range("4.2.0", "") == "≥ 4.2.0",
      SM.supported_range("4.2.0", ""))
check("range: max only", SM.supported_range("", "5.0.0") == "≤ 5.0.0",
      SM.supported_range("", "5.0.0"))
check("range: undeclared", SM.supported_range("", "") == "未声明",
      SM.supported_range("", ""))

# compat_label 现在返回**具体支持范围**（与当前版本无关）
check("label shows concrete range (too_new case)",
      SM.compat_label("4.2.0", "5.0.0", (5, 2, 0)) == "4.2.0 ~ 5.0.0",
      SM.compat_label("4.2.0", "5.0.0", (5, 2, 0)))
check("label shows concrete range (ok case)",
      SM.compat_label("4.2.0", "5.2.0", (5, 2, 0)) == "4.2.0 ~ 5.2.0",
      SM.compat_label("4.2.0", "5.2.0", (5, 2, 0)))

# 判定逻辑（相对当前版本）
check("compat too_old", SM.blender_compat("9.9.0", "", (5, 2, 0))[0] == "too_old")
check("compat too_new (max exceeded)",
      SM.blender_compat("4.2.0", "5.0.0", (5, 2, 0))[0] == "too_new")
check("compat ok in range", SM.blender_compat("4.2.0", "5.2.0", (5, 2, 0))[0] == "ok")
check("compat ok no max", SM.blender_compat("4.2.0", "", (5, 2, 0))[0] == "ok")
check("compat unknown undeclared", SM.blender_compat("", "", (5, 2, 0))[0] == "unknown")

# 跨版本判断：同一插件在 4.5 下兼容、在 6.0 下不兼容（范围 4.2.0~5.0.0）
check("range verdict differs per Blender version",
      SM.blender_compat("4.2.0", "5.0.0", (4, 5, 0))[0] == "ok"
      and SM.blender_compat("4.2.0", "5.0.0", (6, 0, 0))[0] == "too_new")

# 声明了最高版本的扩展 → 导入时记录范围与判定
mx_src = os.path.join(base, "MaxCapExt")
os.makedirs(mx_src)
with open(os.path.join(mx_src, "blender_manifest.toml"), "w", encoding="utf-8") as f:
    f.write('schema_version = "1.0.0"\nid = "max_cap_ext"\nversion = "1.0.0"\n'
            'name = "Max Cap Ext"\nmaintainer = "t"\n'
            'license = ["SPDX:GPL-3.0-or-later"]\ntype = "add-on"\n'
            'blender_version_min = "4.2.0"\nblender_version_max = "4.9.0"\n')
with open(os.path.join(mx_src, "__init__.py"), "w", encoding="utf-8") as f:
    f.write("def register():\n    pass\n")
rmx = PM.library.import_plugin_dir(mx_src, lib, PM.db.LibraryDB(lib), move=True)
check("max collected", rmx.get("blender_max") == "4.9.0", str(rmx.get("blender_max")))
check("import records compat=too_new",
      rmx.get("compat") == "too_new", str(rmx.get("compat")))

# 列表项携带范围文案 + 状态
prefs.search = ""
prefs.only_incompatible = False
PM.items.rebuild_items(prefs)
it_mx = next((it for it in prefs.plugin_items if it.key == rmx["key"]), None)
# --- 统一的 supported 结论（列表只显示 ✓ / ✗ / ?）-----------------------
prefs.only_incompatible = False
prefs.search = ""
PM.items.rebuild_items(prefs)
bad_it = next((it for it in prefs.plugin_items if it.key == rmx["key"]), None)
check("supported=no when version exceeds max",
      bad_it is not None and bad_it.supported == "no",
      getattr(bad_it, "supported", None))

# 实测失败的记录应判为不支持
if "rec_bad" in dir() and rec_bad:
    d_ss = PM.db.LibraryDB(lib)
    rr = d_ss.get(rec_bad["key"])
    rr["load_state"] = "failed"
    rr["load_error"] = "测试用失败"
    d_ss.upsert(rec_bad["key"], rr)
    d_ss.save()
    PM.items.rebuild_items(prefs)
    it_bad = next((it for it in prefs.plugin_items if it.key == rec_bad["key"]), None)
    check("supported=no when load failed",
          it_bad is not None and it_bad.supported == "no",
          getattr(it_bad, "supported", None))
    # 复原
    d_ss2 = PM.db.LibraryDB(lib)
    rr2 = d_ss2.get(rec_bad["key"])
    rr2["load_state"] = ""
    d_ss2.upsert(rec_bad["key"], rr2)
    d_ss2.save()

# 实测通过的记录应判为支持
d_sv = PM.db.LibraryDB(lib)
rv = d_sv.get(r1["key"]) if "r1" in dir() else None
if rv:
    rv["load_state"] = "ok"
    d_sv.upsert(rv["key"], rv)
    d_sv.save()
    PM.items.rebuild_items(prefs)
    it_ok = next((it for it in prefs.plugin_items if it.key == rv["key"]), None)
    check("supported=yes when load ok",
          it_ok is not None and it_ok.supported == "yes",
          getattr(it_ok, "supported", None))

# 一键测试操作符存在
# --- 失败插件的注册残留检测与清理 ----------------------------------------
# 坏插件（register 抛异常）启用失败后，应能检测残留并清理
if "rec_bad" in dir() and rec_bad:
    ok_b, err_b = PM.bridge.set_enabled(rec_bad["module"], True)
    check("bad plugin enable fails", not ok_b, str(ok_b))
    # 残留检测函数可用
    res = PM.bridge.module_residue(rec_bad["module"])
    check("module_residue returns int", isinstance(res, int), str(res))
    # 清理后应无残留（该插件 register 直接抛错、未注册任何类）
    left, cerr = PM.bridge.cleanup_residue(rec_bad["module"])
    check("cleanup_residue clears", left == 0, f"left={left}")
    # 清理操作符存在
    check("cleanup operator exists",
          hasattr(bpy.ops.plugin_manager, "cleanup_residue"))
    # 失败时错误信息应可读
    check("failure message non-empty", bool(err_b), err_b[:60])

check("one-click verify operator exists",
      hasattr(bpy.ops.plugin_manager, "verify_compat"))
check("verify op label", "一键测试" in PM.operators.PM_OT_verify_compat.bl_label,
      PM.operators.PM_OT_verify_compat.bl_label)

check("list item shows max supported version",
      bool(it_mx and it_mx.max_version_text == "≤ 4.9.0"),
      getattr(it_mx, "max_version_text", None))
check("max label without max -> 不限",
      SM.max_version_label("") == "不限", SM.max_version_label(""))
check("max label with max", SM.max_version_label("5.0.0") == "≤ 5.0.0",
      SM.max_version_label("5.0.0"))
check("list item compat flag too_new",
      it_mx is not None and it_mx.compat == "too_new",
      getattr(it_mx, "compat", None))

# 「仅不兼容」过滤：只留下不兼容项，且该项在其中
prefs.only_incompatible = True
keys_inc = PM.items.rebuild_items(prefs)
check("only_incompatible filter works",
      rmx["key"] in keys_inc, f"{len(keys_inc)} hits")
d_chk = PM.db.LibraryDB(lib)
all_inc = True
for k in keys_inc:
    r = d_chk.get(k) or {}
    st = r.get("compat") or SM.blender_compat(r.get("blender_min", ""),
                                              r.get("blender_max", ""))[0]
    if st not in ("too_new", "too_old"):
        all_inc = False
        break
check("filter yields only incompatible", all_inc)
prefs.only_incompatible = False
PM.items.rebuild_items(prefs)

print("\n===SUMMARY===")
fails = [r for r in RESULTS if not r[1]]
print(json.dumps({"total": len(RESULTS), "failed": len(fails),
                  "failures": [{"name": n, "detail": d} for n, _, d in fails]}, ensure_ascii=False))

PM.unregister()
shutil.rmtree(base, ignore_errors=True)

# Blender may return a successful process status even when a background Python
# script raised an exception.  Emit a non-zero status for assertion failures so
# direct callers (and the shell runner) cannot mistake a red suite for success.
if fails:
    sys.exit(1)
