"""真实测试：从商店下载安装一个扩展、校验、然后更新，最后清理。"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, r"E:\AI\geren\chajian_guanliqi")

import bpy

import bl_plugin_manager as PM
from bl_plugin_manager import db as pdb, store

out = {}
base = tempfile.mkdtemp(prefix="store_test_")
lib = os.path.join(base, "library")

# 目录为空时（例如隔离环境读不到商店索引），临时挂一个指向真实索引的仓库，
# 使本测试不依赖运行环境是否已同步过索引。
catalog = store.read_catalog()
if not catalog:
    idx_src = os.environ.get(
        "PM_STORE_INDEX_DIR",
        r"D:\nastongbu\qitaziliao\blender_addons\extensions",
    )
    src_idx = os.path.join(idx_src, ".blender_ext", "index.json")
    if os.path.isfile(src_idx):
        fake = os.path.join(base, "repo")
        os.makedirs(os.path.join(fake, ".blender_ext"), exist_ok=True)
        shutil.copy2(src_idx, os.path.join(fake, ".blender_ext", "index.json"))
        try:
            r = bpy.context.preferences.extensions.repos.new(name="teststore", module="teststore")
            r.use_custom_directory = True
            r.custom_directory = fake
            r.enabled = True
            out["index_source"] = "临时仓库"
        except Exception as e:
            out["index_source"] = f"挂载失败 {e}"
        catalog = store.read_catalog()
out["catalog"] = len(catalog)

# 选一个体积小、兼容、add-on 类型的包
cands = [p for p in catalog
         if p.get("type") == "add-on" and store.is_compatible(p)
         and 0 < p.get("archive_size", 0) < 60000]
cands.sort(key=lambda p: p["archive_size"])
pkg = cands[0] if cands else None
out["picked"] = {"id": pkg["id"], "name": pkg["name"], "ver": pkg["version"],
                 "size": pkg["archive_size"]} if pkg else None

if pkg:
    # 1) 下载
    tmpzip = os.path.join(base, "pkg.zip")
    prog = []
    ok, err = store.download(pkg["archive_url"], tmpzip,
                             pkg.get("archive_hash", ""), pkg.get("archive_size", 0),
                             progress=lambda p: prog.append(p))
    out["download"] = {"ok": ok, "err": err, "size": os.path.getsize(tmpzip) if os.path.isfile(tmpzip) else 0,
                       "progress_steps": len(prog)}

    # 2) 安装到库
    db = pdb.LibraryDB(lib)
    ok2, err2, rec = store.install_package(pkg, lib, db)
    out["install"] = {"ok": ok2, "err": err2,
                      "name": rec.get("name") if rec else None,
                      "ver": rec.get("version") if rec else None,
                      "kind": rec.get("kind") if rec else None,
                      "module": rec.get("module") if rec else None}
    if rec:
        out["installed_dir"] = os.path.isdir(os.path.join(lib, rec["rel"].replace("/", os.sep)))

    # 3) 重复安装应被识别为已安装
    res = store.search(catalog, pdb.LibraryDB(lib), query=pkg["id"])
    out["search_finds_installed"] = [{"id": x["id"], "installed": x["installed"],
                                      "installed_version": x["installed_version"]}
                                     for x in res if x["id"] == pkg["id"]]

    # 4) 模拟更新：把库内版本改低，再用同包"更新"，验证保留用户字段
    if rec:
        d2 = pdb.LibraryDB(lib)
        rr = d2.get(rec["key"])
        rr["version"] = "0.0.1"
        rr["category"] = "我的分类"
        rr["note"] = "用户备注"
        rr["display_name"] = "别名X"
        rr["startup"] = True
        d2.upsert(rec["key"], rr)
        d2.ensure_category("我的分类")
        d2.save()

        ok3, detail = store.update_record(pdb.LibraryDB(lib).get(rec["key"]), pkg, lib, pdb.LibraryDB(lib))
        d3 = pdb.LibraryDB(lib)
        rr3 = d3.get(rec["key"])
        out["update"] = {
            "ok": ok3, "detail": detail,
            "version_after": rr3.get("version") if rr3 else None,
            "category_kept": rr3.get("category") if rr3 else None,
            "note_kept": rr3.get("note") if rr3 else None,
            "display_kept": rr3.get("display_name") if rr3 else None,
            "startup_kept": rr3.get("startup") if rr3 else None,
        }

# 5) 不兼容的包应被过滤
inc = [p for p in catalog if not store.is_compatible(p)]
out["incompatible_count"] = len(inc)
if inc:
    out["incompatible_sample"] = {"id": inc[0]["id"], "min": inc[0]["blender_version_min"]}

shutil.rmtree(base, ignore_errors=True)
print("@@STORE@@" + json.dumps(out, ensure_ascii=False, default=str))
