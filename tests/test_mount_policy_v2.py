import importlib
import sys
import types
import unittest


def _module():
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module("bl_plugin_manager.services.mounts")


class MountPolicyTests(unittest.TestCase):
    def test_reconcile_keeps_official_and_returns_one_current_owned_entry(self):
        mod = _module()
        entries = [
            {"name": "extensions.blender.org", "module": "blender_org", "directory": "C:/official"},
            {"name": "Plugin Library", "module": "pmlib", "directory": "X:/old"},
            {"name": "Plugin Library.001", "module": "pmlib", "directory": "D:/library/extensions"},
        ]
        result = mod.reconcile_entries(entries, "D:/library/extensions")
        self.assertEqual(sum(e["module"] == "pmlib" for e in result.entries), 1)
        self.assertEqual(result.entries[0]["module"], "blender_org")
        self.assertEqual(result.entries[1]["directory"], "D:/library/extensions")
        self.assertEqual(result.removed, 1)

    def test_missing_current_directory_is_not_ready(self):
        mod = _module()
        result = mod.reconcile_entries([], "X:/does-not-exist")
        self.assertFalse(result.ready)


if __name__ == "__main__":
    unittest.main()
