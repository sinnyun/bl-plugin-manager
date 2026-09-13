import importlib
import sys
import types
import tempfile
import unittest
from pathlib import Path


def _storage_module(name):
    """Load storage modules without importing Blender-only addon __init__."""
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module(name)


class StorageV2Tests(unittest.TestCase):
    def test_new_library_has_schema_2_and_default_category(self):
        mod = _storage_module("bl_plugin_manager.storage.shared_db")
        with tempfile.TemporaryDirectory() as root:
            db = mod.SharedDatabase(Path(root))

            report = db.initialize()

            self.assertEqual(report.status, "CREATED")
            self.assertEqual(db.data["schema"], 2)
            self.assertTrue(db.data["library_id"])
            self.assertEqual(db.data["categories"], [{"id": "uncategorized", "name": "未分类", "order": 0}])


    def test_legacy_database_is_archived_without_reading_fields(self):
        mod = _storage_module("bl_plugin_manager.storage.shared_db")
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            pm = root / ".pm"
            pm.mkdir()
            old = pm / "library.json"
            old.write_text('{"schema": 1, "plugins": {"secret": {"category": "旧分类"}}}', encoding="utf-8")

            db = mod.SharedDatabase(root)
            report = db.initialize()

            self.assertEqual(report.status, "ARCHIVED_AND_CREATED")
            self.assertTrue(old.exists())
            self.assertEqual(__import__("json").loads(old.read_text(encoding="utf-8"))["schema"], 2)
            archived = list((pm / "archive").glob("library.pre-2.0.*.json"))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_text(encoding="utf-8"), '{"schema": 1, "plugins": {"secret": {"category": "旧分类"}}}')
            self.assertNotIn("secret", db.data["plugins"])


    def test_corrupt_database_is_not_replaced(self):
        mod = _storage_module("bl_plugin_manager.storage.shared_db")
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            pm = root / ".pm"
            pm.mkdir()
            old = pm / "library.json"
            old.write_text("{broken", encoding="utf-8")

            db = mod.SharedDatabase(root)
            report = db.initialize()

            self.assertEqual(report.status, "CORRUPT")
            self.assertTrue(old.exists())
            self.assertEqual(old.read_text(encoding="utf-8"), "{broken")
            self.assertEqual(db.status, "CORRUPT")

    def test_shared_record_rejects_machine_specific_fields(self):
        mod = _storage_module("bl_plugin_manager.storage.shared_db")
        with tempfile.TemporaryDirectory() as root:
            db = mod.SharedDatabase(Path(root))
            db.initialize()
            with self.assertRaises(mod.InvalidSharedFieldError):
                db.update_plugin("demo", {"display_name": "Demo", "enabled": True})


if __name__ == "__main__":
    unittest.main()
