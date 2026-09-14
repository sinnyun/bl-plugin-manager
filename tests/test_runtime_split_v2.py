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


class RuntimeSplitTests(unittest.TestCase):
    def test_schema2_runtime_fields_are_saved_locally_not_shared(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"] = state
            os.environ["BL_PLUGIN_MANAGER_ENVIRONMENT"] = "blender-test"
            try:
                pm = Path(root) / ".pm"
                pm.mkdir()
                (pm / "library.json").write_text(json.dumps({
                    "schema": 2, "library_id": "lib-a", "revision": 0,
                    "categories": [{"id": "uncategorized", "name": "未分类", "order": 0}],
                    "plugins": {"addons/demo": {
                        "key": "addons/demo", "name": "Demo", "rel": "addons/demo",
                        "enabled": True, "module": "Demo", "last_checked": "2026-09-14 10:00:00",
                        "update_available": True, "latest_version": "2.0.0",
                        "latest_url": "https://example.invalid/demo.zip",
                        "origin_path": "D:/this-computer/inbox/demo.zip",
                    }},
                }), encoding="utf-8")
                db = mod.LibraryDB(root, use_cache=False)
                db.save()
                shared = json.loads((pm / "library.json").read_text(encoding="utf-8"))
                self.assertNotIn("enabled", shared["plugins"]["addons/demo"])
                self.assertNotIn("last_checked", shared["plugins"]["addons/demo"])
                self.assertNotIn("update_available", shared["plugins"]["addons/demo"])
                self.assertNotIn("latest_version", shared["plugins"]["addons/demo"])
                self.assertNotIn("latest_url", shared["plugins"]["addons/demo"])
                self.assertNotIn("origin_path", shared["plugins"]["addons/demo"])
                self.assertTrue(db.get("addons/demo")["enabled"])
                self.assertTrue(db.get("addons/demo")["update_available"])
                self.assertEqual("2.0.0", db.get("addons/demo")["latest_version"])
                self.assertTrue(list(Path(state).rglob("*.json")))
            finally:
                os.environ.pop("BL_PLUGIN_MANAGER_LOCAL_STATE", None)
                os.environ.pop("BL_PLUGIN_MANAGER_ENVIRONMENT", None)


if __name__ == "__main__":
    unittest.main()
