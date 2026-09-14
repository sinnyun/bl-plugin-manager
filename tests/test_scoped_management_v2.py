import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _module():
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules["bl_plugin_manager"] = pkg
    sys.modules.pop("bl_plugin_manager.scoped_management", None)
    return importlib.import_module("bl_plugin_manager.scoped_management")


class ScopedManagementTests(unittest.TestCase):
    def test_first_activation_imports_scopes_and_device_intent_then_round_trips(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            machine = {
                "device_id": "device-a", "device_name": "工作站",
                "library_path": root, "management_enabled": False,
            }
            machine_api = types.SimpleNamespace(
                load=lambda: dict(machine),
                save=lambda changes: machine.update(changes),
            )
            state = {"addons/a": True, "addons/b": False}
            records = [
                {"key": "addons/a", "module": "a"},
                {"key": "addons/b", "module": "b"},
            ]
            applied = []
            reset = []
            bridge = types.SimpleNamespace(
                OFFICIAL_REPO_MODULE="blender_org",
                OFFICIAL_SOURCE_URL="https://extensions.blender.org/api/v1/extensions/",
                capture_scoped_configuration=lambda: {
                    "repositories": [{"name": "Team", "module": "team"}],
                    "script_directories": [{"name": "Tools", "directory": "D:/tools"}],
                },
                apply_scoped_configuration=lambda config: applied.append(config),
                reset_scoped_configuration=lambda: reset.append(True),
                is_module_enabled=lambda module: state[f"addons/{module}"],
                set_enabled=lambda module, enabled: (state.__setitem__(f"addons/{module}", enabled) or (True, "")),
                set_managed_library_root=lambda value: None,
            )
            mod.machine_config = machine_api
            mod.bridge = bridge
            mod.LibraryDB = lambda value: types.SimpleNamespace(all=lambda: records)

            report = mod.activate(root)
            self.assertEqual("ACTIVE", report["state"])
            self.assertTrue(machine["management_enabled"])
            profile = mod.DeviceProfileStore(root).load("device-a")
            self.assertEqual({"addons/a"}, set(profile["enabled_plugins"]))
            self.assertTrue(any(r["module"] == "blender_org" for r in profile["repositories"]))
            self.assertTrue(any(Path(s["directory"]) == Path(root)
                                for s in profile["script_directories"]))

            mod.deactivate(root)
            self.assertFalse(machine["management_enabled"])
            self.assertTrue(reset)
            self.assertFalse(state["addons/a"])
            self.assertEqual({"addons/a"}, set(mod.DeviceProfileStore(root).load("device-a")["enabled_plugins"]))

            mod.activate(root)
            self.assertTrue(state["addons/a"])
            self.assertTrue(applied)

    def test_sync_preserves_intent_for_plugins_missing_on_this_computer(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            store = mod.DeviceProfileStore(root)
            store.save("device-a", "工作站", {"addons/missing", "addons/a"},
                       repositories=[], script_directories=[])
            mod.machine_config = types.SimpleNamespace(load=lambda: {
                "device_id": "device-a", "device_name": "工作站",
                "library_path": root, "management_enabled": True,
            })
            mod.bridge = types.SimpleNamespace(
                OFFICIAL_REPO_MODULE="blender_org",
                OFFICIAL_SOURCE_URL="https://extensions.blender.org/api/v1/extensions/",
                capture_scoped_configuration=lambda: {"repositories": [], "script_directories": []},
                apply_scoped_configuration=lambda config: None,
                is_module_enabled=lambda module: False,
            )
            mod.LibraryDB = lambda value: types.SimpleNamespace(
                all=lambda: [{"key": "addons/a", "module": "a"}])
            mod.sync_to_profile(root)
            self.assertEqual({"addons/missing"}, set(store.load("device-a")["enabled_plugins"]))


if __name__ == "__main__":
    unittest.main()
