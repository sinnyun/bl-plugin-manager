import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _bridge():
    sys.modules.pop("bl_plugin_manager.bridge", None)
    package = types.ModuleType("bl_plugin_manager")
    package.__path__ = ["bl_plugin_manager"]
    sys.modules["bl_plugin_manager"] = package
    sys.modules["addon_utils"] = types.ModuleType("addon_utils")
    script_dirs = []
    repos = []
    prefs = types.SimpleNamespace(
        filepaths=types.SimpleNamespace(script_directories=script_dirs),
        extensions=types.SimpleNamespace(repos=repos),
    )
    bpy = types.ModuleType("bpy")
    bpy.context = types.SimpleNamespace(preferences=prefs)
    sys.modules["bpy"] = bpy
    return importlib.import_module("bl_plugin_manager.bridge")


class _Collection(list):
    def __init__(self, factory):
        super().__init__()
        self.factory = factory

    def new(self, *args, **kwargs):
        value = self.factory(*args, **kwargs)
        self.append(value)
        return value

    def remove(self, value):
        super().remove(value)


class BridgeStateTests(unittest.TestCase):
    def test_manager_never_saves_global_preferences(self):
        from unittest.mock import Mock
        bridge = _bridge()
        save = Mock()
        bridge.bpy.ops = types.SimpleNamespace(wm=types.SimpleNamespace(save_userpref=save))
        bridge.save_prefs()
        save.assert_not_called()

    def test_missing_library_is_never_reported_as_registered(self):
        bridge = _bridge()
        missing = Path(tempfile.gettempdir()) / "plugin-manager-v2-missing-library"
        bridge._script_dirs().append(types.SimpleNamespace(directory=str(missing), name="Plugin Library"))
        bridge.bpy.context.preferences.extensions.repos.append(
            types.SimpleNamespace(
                module="pmlib", directory=str(missing / "extensions"),
                custom_directory="", enabled=True,
            )
        )
        state = bridge.library_state(str(missing), force=True)
        self.assertEqual(
            {"script_dir": False, "repo": False, "official": False}, state,
        )
        self.assertFalse(bridge.is_registered(str(missing)))

    def test_reset_changes_only_repository_and_script_scopes(self):
        bridge = _bridge()
        scripts = _Collection(lambda: types.SimpleNamespace(name="", directory=""))
        scripts.extend([types.SimpleNamespace(name="custom", directory="D:/tools")])
        repos = _Collection(lambda name, module: types.SimpleNamespace(
            name=name, module=module, directory="", custom_directory="",
            remote_url="", enabled=True, use_custom_directory=False,
            use_remote_url=False,
        ))
        official = repos.new("Official changed", "blender_org")
        official.custom_directory = "D:/managed/extensions"
        official.use_custom_directory = True
        repos.new("Team", "team_repo")
        builtin = repos.new("System", "system")
        prefs = bridge.bpy.context.preferences
        prefs.filepaths.script_directories = scripts
        prefs.extensions.repos = repos
        prefs.system = types.SimpleNamespace(use_online_access=False)
        prefs.theme_sentinel = object()
        sentinel = prefs.theme_sentinel

        bridge.reset_scoped_configuration()

        self.assertEqual([], list(scripts))
        self.assertEqual({"blender_org", "system"}, {r.module for r in repos})
        self.assertEqual("", official.custom_directory)
        self.assertFalse(official.use_custom_directory)
        self.assertEqual(bridge.OFFICIAL_SOURCE_URL, official.remote_url)
        self.assertIs(builtin, next(r for r in repos if r.module == "system"))
        self.assertFalse(prefs.system.use_online_access)
        self.assertIs(sentinel, prefs.theme_sentinel)

    def test_capture_and_apply_scoped_configuration_round_trip(self):
        bridge = _bridge()
        scripts = _Collection(lambda: types.SimpleNamespace(name="", directory=""))
        repos = _Collection(lambda name, module: types.SimpleNamespace(
            name=name, module=module, directory="", custom_directory="",
            remote_url="", enabled=True, use_custom_directory=False,
            use_remote_url=False,
        ))
        bridge.bpy.context.preferences.filepaths.script_directories = scripts
        bridge.bpy.context.preferences.extensions.repos = repos
        config = {
            "repositories": [{"name": "Team", "module": "team_repo",
                              "remote_url": "https://repo.invalid/index.json",
                              "custom_directory": "D:/repo", "enabled": False,
                              "use_custom_directory": True, "use_remote_url": True}],
            "script_directories": [{"name": "Tools", "directory": "D:/tools"}],
        }
        bridge.apply_scoped_configuration(config)
        self.assertEqual(config, bridge.capture_scoped_configuration())


if __name__ == "__main__":
    unittest.main()
