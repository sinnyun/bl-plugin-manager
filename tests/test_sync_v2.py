import importlib
import sys
import types
import unittest


def _module():
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module("bl_plugin_manager.services.sync_v2")


class SyncV2Tests(unittest.TestCase):
    def test_scan_merge_preserves_shared_user_fields_and_excludes_runtime_fields(self):
        mod = _module()
        existing = {"addons/demo": {"display_name": "我的别名", "category_id": "tools", "enabled": True}}
        entries = [{"key": "addons/demo", "kind": "addon", "rel": "addons/demo", "name": "Demo", "version": "1"}]
        result = mod.merge_scan(existing, entries)
        record = result["addons/demo"]
        self.assertEqual(record["display_name"], "我的别名")
        self.assertEqual(record["category_id"], "tools")
        self.assertNotIn("enabled", record)
        self.assertEqual(record["name"], "Demo")


if __name__ == "__main__":
    unittest.main()
