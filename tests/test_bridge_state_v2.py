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


class BridgeStateTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
