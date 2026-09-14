import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _module():
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module("bl_plugin_manager.storage.device_profiles")


class DeviceProfileTests(unittest.TestCase):
    def test_devices_keep_independent_activation_intent_and_names(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            store = mod.DeviceProfileStore(root)
            store.save("device-a", "主机", {"addons/a", "extensions/x"})
            store.save("device-b", "笔记本", {"addons/b"})
            self.assertEqual({"addons/a", "extensions/x"}, set(store.load("device-a")["enabled_plugins"]))
            self.assertEqual("笔记本", store.load("device-b")["device_name"])
            self.assertEqual(2, len(store.list_devices()))

    def test_scoped_repository_and_script_configuration_round_trips(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            store = mod.DeviceProfileStore(root)
            repositories = [{"module": "third_party", "name": "第三方", "remote_url": "https://repo.invalid/index.json", "enabled": True}]
            scripts = [{"name": "工具", "directory": "D:/blender-tools"}]
            store.save("device-a", "主机", set(), repositories=repositories,
                       script_directories=scripts)
            profile = store.load("device-a")
            self.assertEqual(repositories, profile["repositories"])
            self.assertEqual(scripts, profile["script_directories"])

    def test_missing_plugins_remain_in_activation_intent(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            store = mod.DeviceProfileStore(root)
            repositories = [{"module": "team", "name": "团队库"}]
            scripts = [{"name": "工具", "directory": "D:/tools"}]
            store.save("device-a", "主机", {"addons/missing"},
                       repositories=repositories, script_directories=scripts)
            store.update_enabled("device-a", "主机", "addons/available", True)
            profile = store.load("device-a")
            self.assertEqual(
                {"addons/missing", "addons/available"},
                set(profile["enabled_plugins"]),
            )
            self.assertEqual(repositories, profile["repositories"])
            self.assertEqual(scripts, profile["script_directories"])

    def test_invalid_device_id_cannot_escape_devices_directory(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            store = mod.DeviceProfileStore(root)
            with self.assertRaises(ValueError):
                store.save("../escape", "bad", set())
            self.assertFalse((Path(root) / ".pm" / "escape.json").exists())

    def test_corrupt_profile_is_not_overwritten(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / ".pm" / "devices" / "device-a.json"
            path.parent.mkdir(parents=True)
            path.write_text("{broken", encoding="utf-8")
            store = mod.DeviceProfileStore(root)
            with self.assertRaises(mod.CorruptDeviceProfileError):
                store.update_enabled("device-a", "主机", "addons/a", True)
            self.assertEqual("{broken", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
