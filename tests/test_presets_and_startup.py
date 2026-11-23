"""Unit tests for v2.5 decoupled Presets, real-time state, manual/temp protection, and Startup Modes."""

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


class PresetEngineAndStartupTests(unittest.TestCase):
    def setUp(self):
        self._state = tempfile.TemporaryDirectory()
        self._env = os.environ.get("BL_PLUGIN_MANAGER_LOCAL_STATE")
        self._envkey = os.environ.get("BL_PLUGIN_MANAGER_ENVIRONMENT")
        self._mcfg = os.environ.get("BL_PLUGIN_MANAGER_MACHINE_CONFIG")
        os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"] = self._state.name
        os.environ["BL_PLUGIN_MANAGER_ENVIRONMENT"] = "test-presets"
        os.environ["BL_PLUGIN_MANAGER_MACHINE_CONFIG"] = str(Path(self._state.name) / "machine.json")
        presets = _load("bl_plugin_manager.services.presets")
        presets.reset_session()

    def tearDown(self):
        presets = _load("bl_plugin_manager.services.presets")
        presets.reset_session()
        self._state.cleanup()
        for key, prev in (
            ("BL_PLUGIN_MANAGER_LOCAL_STATE", self._env),
            ("BL_PLUGIN_MANAGER_ENVIRONMENT", self._envkey),
            ("BL_PLUGIN_MANAGER_MACHINE_CONFIG", self._mcfg),
        ):
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev

    def _seed_db(self, root):
        db_mod = _load("bl_plugin_manager.db")
        db = db_mod.LibraryDB(root, use_cache=False)
        db.prepare()
        db.merge_scan([
            {"rel": "addons/ModA", "kind": "addon", "name": "Mod A", "folder_name": "ModA"},
            {"rel": "addons/Shared", "kind": "addon", "name": "Shared Tool", "folder_name": "Shared"},
            {"rel": "addons/SculptB", "kind": "addon", "name": "Sculpt B", "folder_name": "SculptB"},
            {"rel": "addons/ManualC", "kind": "addon", "name": "Manual C", "folder_name": "ManualC"},
            {"rel": "addons/BrokenD", "kind": "addon", "name": "Broken D", "folder_name": "BrokenD"},
            {"rel": "builtin/node_wrangler", "kind": "builtin", "name": "Node Wrangler",
             "folder_name": "node_wrangler"},
        ])
        for rec in db.plugins.values():
            rec["module"] = rec["folder_name"]
            rec["enabled"] = False
        db.get("addon:broken-d")["load_state"] = "failed"
        db.save()
        return db

    def test_preset_state_is_derived_live_and_skips_known_broken(self):
        presets = _load("bl_plugin_manager.services.presets")
        with tempfile.TemporaryDirectory() as root:
            db = self._seed_db(root)
            db.add_preset("建模主力", ["addon:mod-a", "addon:shared-tool", "addon:broken-d"])
            db.save()

            # Initially off
            info = presets.list_presets(db)[0]
            self.assertEqual(info["state"], "off")
            self.assertEqual(info["broken"], 1)

            live = {}
            set_en = lambda mod, val: (live.__setitem__(mod, val) or (True, ""))
            is_en = lambda mod: bool(live.get(mod, False))

            # Enable preset -> skips BrokenD automatically, enables ModA + Shared
            plan = presets.plan_preset_toggle(db, "建模主力", True)
            self.assertEqual(plan["enable"], ["addon:mod-a", "addon:shared-tool"])
            self.assertEqual(plan["skipped_broken"], ["addon:broken-d"])
            stats = presets.apply_preset_plan(db, plan, set_enabled_fn=set_en, is_enabled_fn=is_en)
            self.assertEqual(stats["skipped_broken"], 1)
            self.assertFalse(live.get("BrokenD", False))

            # Derived state is now "all" (all runnable members enabled)
            self.assertEqual(presets.list_presets(db)[0]["state"], "all")

            # Manually disable ModA -> derived state immediately becomes "partial" (no stale active!)
            live["ModA"] = False
            db.get("addon:mod-a")["enabled"] = False
            self.assertEqual(presets.list_presets(db)[0]["state"], "partial")

            # Manually disable Shared -> derived state becomes "off"
            live["Shared"] = False
            db.get("addon:shared-tool")["enabled"] = False
            self.assertEqual(presets.list_presets(db)[0]["state"], "off")

    def test_manual_prompt_and_temporary_protection_across_preset_switch(self):
        presets = _load("bl_plugin_manager.services.presets")
        with tempfile.TemporaryDirectory() as root:
            db = self._seed_db(root)
            db.add_preset("基础常驻", ["builtin:node_wrangler"], pinned=True)
            db.add_preset("建模主力", ["addon:mod-a", "addon:shared-tool"])
            db.add_preset("雕刻主力", ["addon:shared-tool", "addon:sculpt-b"])
            db.save()

            live = {}
            set_en = lambda mod, val: (live.__setitem__(mod, val) or (True, ""))
            is_en = lambda mod: bool(live.get(mod, False))

            # Activate Base + Modeling
            presets.apply_preset_plan(db, presets.plan_preset_toggle(db, "基础常驻", True),
                                      set_enabled_fn=set_en, is_enabled_fn=is_en)
            presets.apply_preset_plan(db, presets.plan_preset_toggle(db, "建模主力", True),
                                      set_enabled_fn=set_en, is_enabled_fn=is_en)

            # User manually enables SculptB (which belongs to another preset) & ManualC (no preset)
            live["ManualC"] = True
            db.get("addon:manual-c")["enabled"] = True
            self.assertTrue(presets.note_manual_enable(db, "addon:manual-c"))
            self.assertEqual(presets.pending_prompt(db)["key"], "addon:manual-c")

            # Mark ManualC as temporary -> prompt clears, temporary flag set
            presets.mark_temporary("addon:manual-c")
            self.assertIsNone(presets.pending_prompt(db))
            self.assertTrue(presets.is_temporary("addon:manual-c"))

            # User also manually enables ModA's neighbor outside active presets?
            # Switch to Sculpting -> Base stays (pinned), ModA disables, Shared stays, SculptB enables,
            # AND ManualC stays enabled (never killed by preset switch)!
            switch_plan = presets.plan_preset_switch(db, "雕刻主力", keep_pinned=True, keep_manual=True)
            presets.apply_preset_plan(db, switch_plan, set_enabled_fn=set_en, is_enabled_fn=is_en)
            self.assertTrue(live["node_wrangler"])
            self.assertFalse(live["ModA"])
            self.assertTrue(live["Shared"])
            self.assertTrue(live["SculptB"])
            self.assertTrue(live["ManualC"])

            # Now mark SculptB temporary and close Sculpting -> SculptB is protected as temporary!
            presets.mark_temporary("addon:sculpt-b")
            close_plan = presets.plan_preset_toggle(db, "雕刻主力", False)
            self.assertEqual(close_plan["kept_manual"], ["addon:sculpt-b"])
            presets.apply_preset_plan(db, close_plan, set_enabled_fn=set_en, is_enabled_fn=is_en)
            self.assertTrue(live["SculptB"])

            # Assigning ManualC to a preset clears its temporary tag
            db.add_plugin_to_preset("addon:manual-c", "基础常驻")
            presets.note_assigned_to_preset("addon:manual-c")
            self.assertFalse(presets.is_temporary("addon:manual-c"))

    def test_preset_storage_keeps_startup_machine_local_and_presets_shared(self):
        db_mod = _load("bl_plugin_manager.db")
        presets = _load("bl_plugin_manager.services.presets")
        with tempfile.TemporaryDirectory() as root:
            db = self._seed_db(root)
            db.add_preset("基础常驻", ["addon:mod-a"], pinned=True)
            db.set_preset_state("基础常驻", startup=True)
            db.get("addon:sculpt-b")["startup"] = True
            db.save()

            catalog = json.loads((Path(root) / ".pm" / "catalog.json").read_text(encoding="utf-8"))
            self.assertEqual(len(catalog["presets"]), 1)
            self.assertEqual(catalog["presets"][0]["name"], "基础常驻")
            self.assertNotIn("startup", catalog["presets"][0])
            self.assertNotIn("active", catalog["presets"][0])

            reopened = db_mod.LibraryDB(root, use_cache=False)
            keys, preset_names = presets.effective_startup_targets(reopened)
            self.assertEqual(preset_names, {"基础常驻"})
            self.assertEqual(keys, {"addon:mod-a", "addon:sculpt-b"})

    def test_clean_startup_mode_vs_remember_mode_in_scoped_management(self):
        mc = _load("bl_plugin_manager.storage.machine_config")
        sm = _load("bl_plugin_manager.scoped_management")
        db_mod = _load("bl_plugin_manager.db")

        with tempfile.TemporaryDirectory() as root:
            db = self._seed_db(root)
            db.add_preset("基础常驻", ["addon:mod-a"])
            db.set_preset_state("基础常驻", startup=True)
            db.save()

            live = {"ModA": True, "SculptB": True, "node_wrangler": True}
            fake_bridge = types.SimpleNamespace(
                OFFICIAL_REPO_MODULE="blender_org",
                OFFICIAL_SOURCE_URL="https://extensions.blender.org/api/v1/extensions/",
                capture_scoped_configuration=lambda: {"repositories": [], "script_directories": []},
                apply_scoped_configuration=lambda config: None,
                reset_scoped_configuration=lambda: None,
                is_module_enabled=lambda mod: bool(live.get(mod, False)),
                set_enabled=lambda mod, val: (live.__setitem__(mod, val) or (True, "")),
                set_managed_library_root=lambda value: None,
            )
            orig_bridge = sm.bridge
            orig_mc = sm.machine_config
            orig_db = sm.LibraryDB
            try:
                sm.bridge = fake_bridge
                sm.machine_config = mc
                sm.LibraryDB = db_mod.LibraryDB

                # First activation creates device profile capturing ModA + SculptB + node_wrangler
                res1 = sm.activate(root)
                self.assertEqual(res1["state"], "ACTIVE")

                # Remember mode: even if SculptB was disabled transiently, remember restores profile
                live["SculptB"] = False
                mc.save({"startup_mode": "remember"})
                sm.activate(root)
                self.assertTrue(live["ModA"])
                self.assertTrue(live["SculptB"])

                # Clean mode: starts ONLY startup preset (ModA), disables non-startup library addon
                # (SculptB), and NEVER forcibly disables Blender built-in (node_wrangler)!
                mc.save({"startup_mode": "clean"})
                live["ModA"] = False
                live["SculptB"] = True
                live["node_wrangler"] = True
                sm.activate(root)
                self.assertTrue(live["ModA"])
                self.assertFalse(live["SculptB"])
                self.assertTrue(live["node_wrangler"])
            finally:
                sm.bridge = orig_bridge
                sm.machine_config = orig_mc
                sm.LibraryDB = orig_db

    def test_tristate_filters_enabled_disabled_startup_and_hide_problems(self):
        items_mod = _load("bl_plugin_manager.items")
        presets = _load("bl_plugin_manager.services.presets")
        with tempfile.TemporaryDirectory() as root:
            db = self._seed_db(root)
            db.get("addon:mod-a")["enabled"] = True
            db.get("addon:mod-a")["startup"] = True
            db.add_preset("开机预设", ["addon:shared-tool"])
            db.set_preset_state("开机预设", startup=True)
            db.get("addon:manual-c")["missing"] = True
            db.save()

            startup_keys, _ = presets.effective_startup_targets(db)
            base_prefs = lambda **kw: types.SimpleNamespace(**({
                "kind_filter": "all",
                "only_favorites": False,
                "only_updates": False,
                "only_enabled": False,
                "only_incompatible": False,
                "enabled_filter": "all",
                "startup_filter": "all",
                "issue_filter": "all",
                "search": "",
            } | kw))

            # Enabled vs Disabled
            p_en = base_prefs(enabled_filter="enabled")
            self.assertEqual(
                {k for k, r in db.plugins.items() if items_mod._filter(r, p_en, startup_keys)},
                {"addon:mod-a"},
            )
            p_dis = base_prefs(enabled_filter="disabled")
            self.assertNotIn(
                "addon:mod-a",
                {k for k, r in db.plugins.items() if items_mod._filter(r, p_dis, startup_keys)},
            )

            # Startup (includes both plugin.startup AND startup preset member) vs Not-Startup
            p_st = base_prefs(startup_filter="startup")
            self.assertEqual(
                {k for k, r in db.plugins.items() if items_mod._filter(r, p_st, startup_keys)},
                {"addon:mod-a", "addon:shared-tool"},
            )
            p_nst = base_prefs(startup_filter="not_startup")
            nst_hits = {k for k, r in db.plugins.items() if items_mod._filter(r, p_nst, startup_keys)}
            self.assertNotIn("addon:mod-a", nst_hits)
            self.assertNotIn("addon:shared-tool", nst_hits)
            self.assertIn("addon:sculpt-b", nst_hits)

            # Issues: only vs hide (hide excludes load_failed AND missing)
            p_only = base_prefs(issue_filter="only")
            self.assertEqual(
                {k for k, r in db.plugins.items() if items_mod._filter(r, p_only, startup_keys)},
                {"addon:broken-d"},
            )
            p_hide = base_prefs(issue_filter="hide")
            hide_hits = {k for k, r in db.plugins.items() if items_mod._filter(r, p_hide, startup_keys)}
            self.assertNotIn("addon:broken-d", hide_hits)
            self.assertNotIn("addon:manual-c", hide_hits)
            self.assertIn("addon:mod-a", hide_hits)


if __name__ == "__main__":
    unittest.main()
