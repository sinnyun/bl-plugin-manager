import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _db_module():
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module("bl_plugin_manager.db")


def _catalog_module():
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module("bl_plugin_manager.storage.catalog")


class CorruptGuardTests(unittest.TestCase):
    def test_corrupt_catalog_is_read_only_and_save_does_not_replace_source(self):
        mod = _db_module()
        with tempfile.TemporaryDirectory() as root:
            pm = Path(root) / ".pm"
            pm.mkdir()
            path = pm / "catalog.json"
            path.write_text("{broken", encoding="utf-8")
            db = mod.LibraryDB(root, use_cache=False)
            self.assertEqual(db.status, "CORRUPT")
            with self.assertRaises(mod.CorruptDatabaseError):
                db.save()
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_corrupt_legacy_library_also_blocks_save(self):
        """升级前的旧库损坏时，绝不能在旁边新建 catalog 覆盖掉它的语义。"""
        mod = _db_module()
        with tempfile.TemporaryDirectory() as root:
            pm = Path(root) / ".pm"
            pm.mkdir()
            legacy = pm / "library.json"
            legacy.write_text("{broken", encoding="utf-8")
            db = mod.LibraryDB(root, use_cache=False)
            self.assertEqual(db.status, "CORRUPT")
            with self.assertRaises(mod.CorruptDatabaseError):
                db.save()
            self.assertEqual(legacy.read_text(encoding="utf-8"), "{broken")

    def test_file_signature_contains_identity_and_creation_change_markers(self):
        mod = _db_module()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "catalog.json"
            path.write_text("{}", encoding="utf-8")
            signature = mod._file_signature(path)
            self.assertEqual(len(signature), 5)
            self.assertEqual(signature[-1], 2)

    def test_stale_handle_absorbs_external_update_before_saving(self):
        mod = _catalog_module()
        with tempfile.TemporaryDirectory() as root:
            first = mod.Catalog(root)
            first.initialize()
            first.set_plugin("addon:demo", {"name": "A"})
            first.save()
            stale = mod.Catalog(root)
            stale.load()
            fresh = mod.Catalog(root)
            fresh.load()
            fresh.set_plugin("addon:demo", {"name": "B"})
            fresh.set_plugin("addon:other", {"name": "Other"})
            fresh.save()

            self.assertTrue(stale.refresh_if_stale())
            stale.set_plugin("addon:demo", {"name": "A2"})
            stale.save()

            reopened = mod.Catalog(root)
            reopened.load()
            self.assertEqual(reopened.plugins["addon:demo"]["name"], "A2")
            self.assertIn("addon:other", reopened.plugins)


if __name__ == "__main__":
    unittest.main()
