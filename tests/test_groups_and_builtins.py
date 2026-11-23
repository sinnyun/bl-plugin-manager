"""Unit tests for composable plugin groups, group startup, and built-in virtual links."""

import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _load(name):
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module(name)


class GroupEngineTests(unittest.TestCase):
    def setUp(self):
        self._state = tempfile.TemporaryDirectory()
        self._env = os.environ.get("BL_PLUGIN_MANAGER_LOCAL_STATE")
        self._envkey = os.environ.get("BL_PLUGIN_MANAGER_ENVIRONMENT")
        os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"] = self._state.name
        os.environ["BL_PLUGIN_MANAGER_ENVIRONMENT"] = "test-groups"

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

    def _seed_db(self, root):
        db_mod = _load("bl_plugin_manager.db")
        db = db_mod.LibraryDB(root, use_cache=False)
        db.prepare()
        db.merge_scan([
            {"rel": "addons/ModA", "kind": "addon", "name": "Mod A", "folder_name": "ModA"},
            {"rel": "addons/Shared", "kind": "addon", "name": "Shared Tool", "folder_name": "Shared"},
            {"rel": "addons/SculptB", "kind": "addon", "name": "Sculpt B", "folder_name": "SculptB"},
            {"rel": "builtin/node_wrangler", "kind": "builtin", "name": "Node Wrangler",
             "folder_name": "node_wrangler"},
        ])
        for rec in db.plugins.values():
            rec["module"] = rec["folder_name"]
            rec["enabled"] = False
        db.save()
        return db

    def test_existing_single_category_migrates_into_groups_and_supports_multi_group(self):
        db_mod = _load("bl_plugin_manager.db")
        with tempfile.TemporaryDirectory() as root:
            db = self._seed_db(root)
            k_mod = "addon:mod-a"
            k_shared = "addon:shared-tool"
            db.ensure_category("建模")
            db.get(k_mod)["category"] = "建模"
            db.save()

            reopened = db_mod.LibraryDB(root, use_cache=False)
            self.assertEqual(reopened.plugin_groups(k_mod), ["建模"])

            reopened.set_plugin_groups(k_shared, ["建模", "雕刻"])
            reopened.save()

            reopened2 = db_mod.LibraryDB(root, use_cache=False)
            self.assertEqual(reopened2.plugin_groups(k_shared), ["建模", "雕刻"])
            self.assertEqual(reopened2.get(k_shared)["category"], "建模")
            counts = reopened2.category_counts()
            self.assertEqual(counts["建模"], 2)
            self.assertEqual(counts["雕刻"], 1)

    def test_multi_group_stacking_and_union_protection_on_close(self):
        groups = _load("bl_plugin_manager.services.groups")
        with tempfile.TemporaryDirectory() as root:
            db = self._seed_db(root)
            db.set_plugin_groups("addon:mod-a", ["建模"])
            db.set_plugin_groups("addon:shared-tool", ["建模", "雕刻"])
            db.set_plugin_groups("addon:sculpt-b", ["雕刻"])
            db.save()

            live = {}
            def set_en(mod, val):
                live[mod] = val
                return True, ""
            def is_en(mod):
                return bool(live.get(mod, False))

            # Enable Modeling group
            p1 = groups.plan_group_toggle(db, "建模", True)
            groups.apply_group_plan(db, p1, set_enabled_fn=set_en, is_enabled_fn=is_en)
            self.assertTrue(live["ModA"])
            self.assertTrue(live["Shared"])
            self.assertFalse(live.get("SculptB", False))
            self.assertTrue(db.get_group_state("建模")["active"])

            # Stack Sculpting group on top
            p2 = groups.plan_group_toggle(db, "雕刻", True)
            groups.apply_group_plan(db, p2, set_enabled_fn=set_en, is_enabled_fn=is_en)
            self.assertTrue(live["ModA"])
            self.assertTrue(live["Shared"])
            self.assertTrue(live["SculptB"])

            # Close Modeling while Sculpting stays open -> Shared MUST stay enabled!
            p3 = groups.plan_group_toggle(db, "建模", False)
            self.assertEqual(p3["disable"], ["addon:mod-a"])
            self.assertEqual(p3["kept_shared"], ["addon:shared-tool"])
            groups.apply_group_plan(db, p3, set_enabled_fn=set_en, is_enabled_fn=is_en)
            self.assertFalse(live["ModA"])
            self.assertTrue(live["Shared"])
            self.assertTrue(live["SculptB"])
            self.assertFalse(db.get_group_state("建模")["active"])
            self.assertTrue(db.get_group_state("雕刻")["active"])

            # Close Sculpting -> Shared and SculptB both disable
            p4 = groups.plan_group_toggle(db, "雕刻", False)
            groups.apply_group_plan(db, p4, set_enabled_fn=set_en, is_enabled_fn=is_en)
            self.assertFalse(live["Shared"])
            self.assertFalse(live["SculptB"])

    def test_solo_switch_preserves_pinned_base_group(self):
        groups = _load("bl_plugin_manager.services.groups")
        with tempfile.TemporaryDirectory() as root:
            db = self._seed_db(root)
            db.set_plugin_groups("builtin:node_wrangler", ["基础常驻"])
            db.set_plugin_groups("addon:mod-a", ["建模"])
            db.set_plugin_groups("addon:sculpt-b", ["雕刻"])
            db.set_group_pinned("基础常驻", True)
            db.save()

            live = {}
            set_en = lambda mod, val: (live.__setitem__(mod, val) or (True, ""))
            is_en = lambda mod: bool(live.get(mod, False))

            # Open Base + Modeling
            groups.apply_group_plan(db, groups.plan_group_toggle(db, "基础常驻", True),
                                    set_enabled_fn=set_en, is_enabled_fn=is_en)
            groups.apply_group_plan(db, groups.plan_group_toggle(db, "建模", True),
                                    set_enabled_fn=set_en, is_enabled_fn=is_en)

            # Solo switch to Sculpting -> closes Modeling, keeps Base, opens Sculpting
            solo = groups.plan_group_solo(db, "雕刻", keep_pinned=True)
            groups.apply_group_plan(db, solo, set_enabled_fn=set_en, is_enabled_fn=is_en)
            self.assertTrue(live["node_wrangler"])
            self.assertFalse(live["ModA"])
            self.assertTrue(live["SculptB"])
            self.assertTrue(db.get_group_state("基础常驻")["active"])
            self.assertFalse(db.get_group_state("建模")["active"])
            self.assertTrue(db.get_group_state("雕刻")["active"])

    def test_group_startup_combines_with_individual_startup_and_stays_machine_local(self):
        db_mod = _load("bl_plugin_manager.db")
        groups = _load("bl_plugin_manager.services.groups")
        with tempfile.TemporaryDirectory() as root:
            db = self._seed_db(root)
            db.set_plugin_groups("addon:mod-a", ["建模"])
            db.get("addon:sculpt-b")["startup"] = True
            db.set_group_state("建模", startup=True)
            db.save()

            keys, startup_groups = groups.effective_startup_targets(db)
            self.assertEqual(startup_groups, {"建模"})
            self.assertEqual(keys, {"addon:mod-a", "addon:sculpt-b"})

            # Group runtime state must NOT leak into shared catalog.json
            catalog = json.loads((Path(root) / ".pm" / "catalog.json").read_text(encoding="utf-8"))
            for item in catalog["categories"]:
                self.assertNotIn("active", item)
                self.assertNotIn("startup", item)

            # Reopening on the same machine restores group startup state
            reopened = db_mod.LibraryDB(root, use_cache=False)
            self.assertTrue(reopened.get_group_state("建模")["startup"])

    def test_builtin_virtual_link_cannot_resolve_disk_path_and_survives_disk_scan(self):
        db_mod = _load("bl_plugin_manager.db")
        paths = _load("bl_plugin_manager.security.paths")
        with tempfile.TemporaryDirectory() as root:
            db = self._seed_db(root)
            rec = db.get("builtin:node_wrangler")
            self.assertEqual(rec["kind"], "builtin")
            rec["display_name"] = "节点管理"
            db.set_plugin_groups("builtin:node_wrangler", ["材质"])
            db.save()

            # resolve_record_path must reject builtin so file ops cannot touch Blender install
            with self.assertRaises(paths.UnsafeLibraryPathError):
                paths.resolve_record_path(root, rec["rel"], rec["kind"])

            # A complete disk scan of addons/extensions must NOT mark built-in link as missing
            db.merge_scan([
                {"rel": "addons/ModA", "kind": "addon", "name": "Mod A", "folder_name": "ModA"},
            ], complete=True, persist=True)
            reopened = db_mod.LibraryDB(root, use_cache=False)
            self.assertFalse(reopened.get("builtin:node_wrangler")["missing"])
            self.assertEqual(reopened.get("builtin:node_wrangler")["display_name"], "节点管理")
            self.assertEqual(reopened.plugin_groups("builtin:node_wrangler"), ["材质"])

    def test_editing_legacy_v23_catalog_without_groups_field_does_not_conflict(self):
        """Regression: v2.3.x catalog records lack 'groups'; editing them must not false-conflict."""
        db_mod = _load("bl_plugin_manager.db")
        with tempfile.TemporaryDirectory() as root:
            pm = Path(root) / ".pm"
            pm.mkdir(parents=True)
            # Write a v2.3.2-style catalog.json (no 'groups' key on plugin record)
            (pm / "catalog.json").write_text(json.dumps({
                "schema": 1,
                "library_id": "lib-v23",
                "revision": 4,
                "categories": [{"id": "uncategorized", "name": "未分类", "order": 0}],
                "plugins": {
                    "addon:demo": {
                        "plugin_id": "addon:demo",
                        "kind": "addon",
                        "name": "Demo Addon",
                        "category": "未分类",
                    },
                },
            }, ensure_ascii=False), encoding="utf-8")
            # Seed local binding for addon:demo
            state_dir = Path(self._state.name) / "lib-v23"
            state_dir.mkdir(parents=True)
            (state_dir / "test-groups.json").write_text(json.dumps({
                "schema": 2,
                "bindings": {"addon:demo": "addons/Demo"},
                "plugins": {"addon:demo": {"module": "Demo"}},
            }), encoding="utf-8")

            db = db_mod.LibraryDB(root, use_cache=False)
            self.assertEqual(db.status, "OK")
            db.ensure_category("动画与运镜")
            db.get("addon:demo")["display_name"] = "演示插件"
            db.set_plugin_groups("addon:demo", ["动画与运镜"])
            # Must save cleanly without raising CatalogConflictError!
            db.save()

            reopened = db_mod.LibraryDB(root, use_cache=False)
            self.assertEqual(reopened.get("addon:demo")["display_name"], "演示插件")
            self.assertEqual(reopened.plugin_groups("addon:demo"), ["动画与运镜"])


if __name__ == "__main__":
    unittest.main()
