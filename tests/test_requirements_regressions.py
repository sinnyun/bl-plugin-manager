import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def modules():
    package = types.ModuleType("bl_plugin_manager")
    package.__path__ = ["bl_plugin_manager"]
    sys.modules["bl_plugin_manager"] = package
    return importlib.import_module("bl_plugin_manager.db"), importlib.import_module(
        "bl_plugin_manager.services.catalog_sync")


def extension(pkg_id="example", version="1.0.0"):
    return {"rel": "extensions/example", "kind": "extension", "folder_name": "example",
            "pkg_id": pkg_id, "name": "Example", "version": version}


class RequirementsRegressions(unittest.TestCase):
    def test_stale_handles_merge_independent_fields_and_categories(self):
        db, _ = modules()
        with tempfile.TemporaryDirectory() as path, patch.dict(
                "os.environ", {"BL_PLUGIN_MANAGER_LOCAL_STATE": str(Path(path) / "local")}):
            seed = db.LibraryDB(path)
            seed.merge_scan([extension()])
            key = seed.assignments["extensions/example"]
            first, second = db.LibraryDB(path), db.LibraryDB(path)
            first.get(key)["note"] = "note from first"
            first.add_category("First")
            first.save()
            second.get(key)["favorite"] = True
            second.add_category("Second")
            second.save()
            reopened = db.LibraryDB(path)
            self.assertEqual(reopened.get(key)["note"], "note from first")
            self.assertTrue(reopened.get(key)["favorite"])
            self.assertEqual(set(reopened.categories), {"未分类", "First", "Second"})

    def test_install_versions_are_local_and_load_result_expires(self):
        db, _ = modules()
        with tempfile.TemporaryDirectory() as path:
            with patch.dict("os.environ", {"BL_PLUGIN_MANAGER_LOCAL_STATE": str(Path(path) / "a")}):
                a = db.LibraryDB(path)
                a.merge_scan([extension(version="2.0.0")])
                key = a.assignments["extensions/example"]
                a.get(key)["load_state"] = "ok"
                a.save()
            with patch.dict("os.environ", {"BL_PLUGIN_MANAGER_LOCAL_STATE": str(Path(path) / "b")}):
                db.LibraryDB(path).merge_scan([extension(version="1.0.0")])
            with patch.dict("os.environ", {"BL_PLUGIN_MANAGER_LOCAL_STATE": str(Path(path) / "a")}):
                a = db.LibraryDB(path)
                self.assertEqual(a.get(key)["version"], "2.0.0")
                a.merge_scan([extension(version="3.0.0")])
                self.assertFalse(a.get(key).get("load_state"))

    def test_conflicting_edits_keep_the_published_value_and_a_recovery_copy(self):
        db, _ = modules()
        with tempfile.TemporaryDirectory() as path, patch.dict(
                "os.environ", {"BL_PLUGIN_MANAGER_LOCAL_STATE": str(Path(path) / "local")}):
            seed = db.LibraryDB(path)
            seed.merge_scan([extension()])
            key = seed.assignments["extensions/example"]
            first, second = db.LibraryDB(path), db.LibraryDB(path)
            first.get(key)["note"] = "published"
            first.save()
            second.get(key)["note"] = "still needed"
            with self.assertRaises(db.catalog_store.CatalogConflictError):
                second.save()
            self.assertEqual(db.LibraryDB(path).get(key)["note"], "published")
            recovery, = list(Path(path, "local").rglob("catalog-conflict-*.json"))
            conflict, = json.loads(recovery.read_text(encoding="utf-8"))["conflicts"]
            self.assertEqual((conflict["current"], conflict["proposed"]), ("published", "still needed"))

    def test_distinct_manifest_ids_never_match_by_name_or_folder(self):
        _, sync = modules()
        first = sync.link_scan({}, {}, [extension("old")])
        old_key = first.assignment["extensions/example"]
        first.plugins[old_key]["display_name"] = "Old alias"
        second = sync.link_scan(first.plugins, first.bindings, [extension("new")])
        new_key = second.assignment["extensions/example"]
        self.assertNotEqual(old_key, new_key)
        self.assertEqual(second.plugins[old_key]["display_name"], "Old alias")
        self.assertNotIn(old_key, second.bindings)

    def test_manual_relink_keeps_catalog_identity_after_another_scan(self):
        db, _ = modules()
        with tempfile.TemporaryDirectory() as path, patch.dict(
                "os.environ", {"BL_PLUGIN_MANAGER_LOCAL_STATE": str(Path(path) / "local")}):
            library = db.LibraryDB(path)
            old = {"rel": "addons/same", "kind": "addon", "name": "Old Name", "folder_name": "same"}
            library.merge_scan([old])
            key = library.assignments[old["rel"]]
            library.get(key)["note"] = "keep me"
            library.save()
            new = dict(old, name="Unverified New Name")
            library.merge_scan([new])
            provisional = library.assignments[old["rel"]]
            self.assertNotEqual(key, provisional)
            self.assertEqual(library.relink_addon(provisional, key)["note"], "keep me")
            library.merge_scan([new])
            self.assertEqual(library.assignments[old["rel"]], key)

    def test_schema_one_migrates_and_future_schema_stays_in_place(self):
        db, _ = modules()
        for schema in (1, 88):
            with self.subTest(schema=schema), tempfile.TemporaryDirectory() as path, patch.dict(
                    "os.environ", {"BL_PLUGIN_MANAGER_LOCAL_STATE": str(Path(path) / "local")}):
                legacy = Path(path) / ".pm" / "library.json"
                legacy.parent.mkdir()
                legacy.write_text(json.dumps({
                    "schema": schema, "categories": ["Original"],
                    "plugins": {"extensions/example": dict(extension(), note="Original note",
                                                          category="Original", startup=True)},
                }), encoding="utf-8")
                library = db.LibraryDB(path)
                if schema == 1:
                    self.assertEqual(library.prepare(), "OK")
                    self.assertFalse(legacy.exists())
                    restored = db.LibraryDB(path)
                    self.assertIn("Original", restored.categories)
                    self.assertEqual(restored.all()[0]["note"], "Original note")
                    self.assertTrue(restored.all()[0]["startup"])
                else:
                    self.assertEqual(library.prepare(), "UNSUPPORTED")
                    self.assertTrue(legacy.exists())
                    self.assertFalse(library.catalog.path.exists())
