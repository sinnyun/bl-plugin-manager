import importlib
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


class LegacyDbGuardTests(unittest.TestCase):
    def test_corrupt_db_is_read_only_and_save_does_not_replace_source(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            pm = Path(root) / ".pm"
            pm.mkdir()
            path = pm / "library.json"
            path.write_text("{broken", encoding="utf-8")
            db = mod.LibraryDB(root, use_cache=False)
            self.assertEqual(db.status, "CORRUPT")
            with self.assertRaises(mod.CorruptDatabaseError):
                db.save()
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_file_signature_contains_identity_and_creation_change_markers(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "library.json"
            path.write_text("{}", encoding="utf-8")
            signature = mod._file_signature(path)
            self.assertEqual(len(signature), 5)
            self.assertEqual(signature[-1], 2)

    def test_save_refuses_to_overwrite_an_external_update(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            first = mod.LibraryDB(root, use_cache=False)
            first.upsert("addons/demo", {"name": "A"})
            first.save()
            stale = mod.LibraryDB(root, use_cache=False)
            fresh = mod.LibraryDB(root, use_cache=False)
            fresh.upsert("addons/demo", {"name": "B"})
            fresh.save()
            stale.upsert("addons/demo", {"name": "A2"})
            with self.assertRaises(mod.DatabaseConflictError):
                stale.save()


if __name__ == "__main__":
    unittest.main()
