import importlib
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
    return importlib.import_module("bl_plugin_manager.storage.machine_config")


class MachineConfigTests(unittest.TestCase):
    def test_path_and_name_changes_preserve_device_identity(self):
        from unittest.mock import patch
        mod = _module()
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {
            'BL_PLUGIN_MANAGER_MACHINE_CONFIG': str(Path(root) / 'machine.json'),
        }):
            mod.save({'library_path': 'D:/library'})
            identity = mod.load()['device_id']
            mod.save({'library_path': 'E:/library', 'device_name': '工作站',
                      'management_enabled': True})
            self.assertEqual(identity, mod.load()['device_id'])
            self.assertEqual('工作站', mod.load()['device_name'])
            self.assertTrue(mod.load()['management_enabled'])

    def test_round_trip_and_only_allowed_fields(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "machine.json"
            old = os.environ.get("BL_PLUGIN_MANAGER_MACHINE_CONFIG")
            os.environ["BL_PLUGIN_MANAGER_MACHINE_CONFIG"] = str(path)
            try:
                mod.save({"library_path": "\\\\server\\共享\\插件", "unexpected": "discard"})
                loaded = mod.load()
            finally:
                if old is None:
                    os.environ.pop("BL_PLUGIN_MANAGER_MACHINE_CONFIG", None)
                else:
                    os.environ["BL_PLUGIN_MANAGER_MACHINE_CONFIG"] = old
            self.assertEqual(loaded["library_path"], "\\\\server\\共享\\插件")
            self.assertNotIn("unexpected", loaded)
            self.assertTrue(loaded["device_id"])
            self.assertFalse(loaded["management_enabled"])

    def test_corrupt_config_returns_unconfigured_without_overwriting(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "machine.json"
            path.write_text("{broken", encoding="utf-8")
            old = os.environ.get("BL_PLUGIN_MANAGER_MACHINE_CONFIG")
            os.environ["BL_PLUGIN_MANAGER_MACHINE_CONFIG"] = str(path)
            try:
                loaded = mod.load()
            finally:
                if old is None:
                    os.environ.pop("BL_PLUGIN_MANAGER_MACHINE_CONFIG", None)
                else:
                    os.environ["BL_PLUGIN_MANAGER_MACHINE_CONFIG"] = old
            self.assertIsNone(loaded["library_path"])
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
