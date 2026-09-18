"""总资料库视图与旧数据迁移。"""

import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _module():
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module("bl_plugin_manager.db")


class CatalogViewTests(unittest.TestCase):
    def setUp(self):
        self._state = tempfile.TemporaryDirectory()
        self._env = os.environ.get("BL_PLUGIN_MANAGER_LOCAL_STATE")
        self._envkey = os.environ.get("BL_PLUGIN_MANAGER_ENVIRONMENT")
        os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"] = self._state.name
        os.environ["BL_PLUGIN_MANAGER_ENVIRONMENT"] = "test-env"

    def tearDown(self):
        self._state.cleanup()
        if self._env is None:
            os.environ.pop("BL_PLUGIN_MANAGER_LOCAL_STATE", None)
        else:
            os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"] = self._env
        if self._envkey is None:
            os.environ.pop("BL_PLUGIN_MANAGER_ENVIRONMENT", None)
        else:
            os.environ["BL_PLUGIN_MANAGER_ENVIRONMENT"] = self._envkey

    def test_merge_scan_writes_identity_to_catalog_and_path_to_local_state(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            db = mod.LibraryDB(root, use_cache=False)
            db.prepare()
            db.merge_scan([{
                "rel": "addons/Demo", "kind": "addon", "name": "Demo",
                "folder_name": "Demo", "version": "1.0", "description": "d",
            }])
            plugin_id = next(iter(db.plugins))
            record = db.get(plugin_id)
            record["display_name"] = "别名"
            record["category"] = "工具"
            record["enabled"] = True
            record["module"] = "Demo"
            record["startup"] = True
            record["origin_path"] = "D:/this-machine/Demo.zip"
            db.save()

            catalog = json.loads(
                (Path(root) / ".pm" / "catalog.json").read_text(encoding="utf-8"))
            shared = catalog["plugins"][plugin_id]
            self.assertEqual(shared["display_name"], "别名")
            self.assertEqual(shared["category"], "工具")
            self.assertNotIn("rel", shared, "本机路径不得进入总资料库")
            self.assertNotIn("module", shared)
            self.assertNotIn("enabled", shared)
            self.assertNotIn("startup", shared, "自启是本机行为，不随之同步")
            self.assertNotIn("origin_path", shared)

            # 本机绑定与运行状态保存在 LOCALAPPDATA，而不是库内。
            state_files = list(Path(self._state.name).rglob("*.json"))
            self.assertTrue(state_files)
            state = json.loads(state_files[0].read_text(encoding="utf-8"))
            self.assertEqual(state["bindings"][plugin_id], "addons/Demo")
            self.assertEqual(state["plugins"][plugin_id]["module"], "Demo")
            self.assertTrue(state["plugins"][plugin_id]["startup"])

    def test_catalog_data_is_shared_but_paths_are_per_machine(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            db = mod.LibraryDB(root, use_cache=False)
            db.prepare()
            db.merge_scan([{"rel": "addons/MeshTools", "kind": "addon",
                            "name": "Mesh Tools", "folder_name": "MeshTools"}])
            pid = next(iter(db.plugins))
            db.get(pid)["display_name"] = "网格工具"
            db.get(pid)["category"] = "建模"
            db.get(pid)["startup"] = True
            db.get(pid)["enabled"] = True
            db.save()

            # 模拟另一台电脑：不同本机状态目录、同一总资料库、不同目录名。
            other = tempfile.TemporaryDirectory()
            try:
                os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"] = other.name
                fresh = mod.LibraryDB(root, use_cache=False)
                self.assertEqual(fresh.plugins, {}, "别的电脑尚未安装该插件")
                fresh.merge_scan([{"rel": "addons/网格工具_v2", "kind": "addon",
                                   "name": "Mesh Tools", "folder_name": "网格工具_v2"}])
                self.assertEqual(len(fresh.plugins), 1, "应连回同一条资料库条目")
                rec = fresh.get(pid)
                self.assertEqual(rec["display_name"], "网格工具")
                self.assertEqual(rec["category"], "建模")
                self.assertEqual(rec["rel"], "addons/网格工具_v2")
                # 自启与启用属于本机行为：A 电脑的设置不得带到 B 电脑。
                self.assertFalse(rec.get("startup"), "自启不应跨电脑同步")
                self.assertFalse(rec.get("enabled"), "启用状态不应跨电脑同步")
            finally:
                other.cleanup()

    def test_remove_unbinds_locally_but_keeps_catalog_entry(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            db = mod.LibraryDB(root, use_cache=False)
            db.prepare()
            db.merge_scan([{"rel": "addons/Demo", "kind": "addon",
                            "name": "Demo", "folder_name": "Demo"}])
            pid = next(iter(db.plugins))
            db.get(pid)["display_name"] = "别名"
            db.save()

            db.remove(pid)
            db.save()

            reopened = mod.LibraryDB(root, use_cache=False)
            self.assertNotIn(pid, reopened.plugins, "本机不再显示该插件")
            catalog = json.loads(
                (Path(root) / ".pm" / "catalog.json").read_text(encoding="utf-8"))
            self.assertEqual(catalog["plugins"][pid]["display_name"], "别名",
                             "总资料库条目必须保留，供其它电脑与重装使用")


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self._state = tempfile.TemporaryDirectory()
        self._env = os.environ.get("BL_PLUGIN_MANAGER_LOCAL_STATE")
        self._envkey = os.environ.get("BL_PLUGIN_MANAGER_ENVIRONMENT")
        os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"] = self._state.name
        os.environ["BL_PLUGIN_MANAGER_ENVIRONMENT"] = "test-env"

    def tearDown(self):
        self._state.cleanup()
        if self._env is None:
            os.environ.pop("BL_PLUGIN_MANAGER_LOCAL_STATE", None)
        else:
            os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"] = self._env
        if self._envkey is None:
            os.environ.pop("BL_PLUGIN_MANAGER_ENVIRONMENT", None)
        else:
            os.environ["BL_PLUGIN_MANAGER_ENVIRONMENT"] = self._envkey

    def test_legacy_library_is_migrated_into_catalog_and_archived(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            pm = Path(root) / ".pm"
            pm.mkdir()
            legacy = {
                "schema": 2, "library_id": "old-lib", "revision": 5,
                "categories": [{"id": "uncategorized", "name": "未分类", "order": 0},
                               {"id": "建模", "name": "建模", "order": 1}],
                "plugins": {
                    "addons/MeshTools_v2.1": {
                        "key": "addons/MeshTools_v2.1", "kind": "addon",
                        "rel": "addons/MeshTools_v2.1", "name": "Mesh Tools",
                        "folder_name": "MeshTools_v2.1", "version": "2.1",
                        "display_name": "网格工具", "category": "建模",
                        "note": "常用", "tags": ["mesh"], "favorite": True,
                        "startup": True, "module": "MeshTools_v2.1",
                        "enabled": True, "origin_path": "D:/old/path.zip",
                    },
                },
            }
            (pm / "library.json").write_text(json.dumps(legacy), encoding="utf-8")

            db = mod.LibraryDB(root, use_cache=False)
            self.assertEqual(db.prepare(), "OK")

            pid = next(iter(db.plugins))
            rec = db.get(pid)
            self.assertEqual(rec["display_name"], "网格工具")
            self.assertEqual(rec["category"], "建模")
            self.assertEqual(rec["note"], "常用")
            self.assertEqual(rec["tags"], ["mesh"])
            self.assertTrue(rec["favorite"])
            self.assertTrue(rec["startup"], "自启迁移到本机状态后仍应显示")
            self.assertEqual(rec["rel"], "addons/MeshTools_v2.1")
            self.assertIn("建模", db.categories)

            # 旧文件已归档，新资料库不含本机字段。
            self.assertFalse((pm / "library.json").exists())
            archive = list((pm / "archive").glob("library.pre-catalog.*.json"))
            self.assertEqual(len(archive), 1)
            catalog = json.loads((pm / "catalog.json").read_text(encoding="utf-8"))
            shared = catalog["plugins"][pid]
            self.assertNotIn("startup", shared)
            self.assertNotIn("module", shared)
            self.assertNotIn("origin_path", shared)

    def test_migration_does_not_duplicate_a_plugin_already_in_catalog(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            db = mod.LibraryDB(root, use_cache=False)
            db.prepare()
            db.merge_scan([{"rel": "addons/MeshTools", "kind": "addon",
                            "name": "Mesh Tools", "folder_name": "MeshTools",
                            "version": "3.0"}])
            pid = next(iter(db.plugins))
            db.save()

            legacy = {
                "schema": 2, "library_id": "old", "revision": 1,
                "categories": [],
                "plugins": {"addons/MeshTools": {
                    "kind": "addon", "rel": "addons/MeshTools",
                    "name": "Mesh Tools", "folder_name": "MeshTools",
                    "version": "2.1", "display_name": "网格工具",
                }},
            }
            db.absorb_legacy(legacy)

            self.assertEqual(len(db.plugins), 1, "同一插件不得因迁移产生重复条目")
            self.assertEqual(db.get(pid)["display_name"], "网格工具")


if __name__ == "__main__":
    unittest.main()
