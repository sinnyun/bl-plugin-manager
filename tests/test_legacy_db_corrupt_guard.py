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


if __name__ == "__main__":
    unittest.main()
