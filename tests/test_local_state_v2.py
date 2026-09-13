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
    return importlib.import_module("bl_plugin_manager.storage.local_state")


class LocalStateTests(unittest.TestCase):
    def test_state_path_is_scoped_by_library_and_environment(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            old = os.environ.get("BL_PLUGIN_MANAGER_LOCAL_STATE")
            os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"] = root
            try:
                first = mod.StateStore("library-a", "blender-5.2-python-3.13")
                second = mod.StateStore("library-a", "blender-4.3-python-3.11")
                first.save({"enabled": {"a": True}, "load_error": {}})
                self.assertEqual(first.load()["enabled"]["a"], True)
                self.assertEqual(second.load(), {})
                self.assertNotEqual(first.path, second.path)
            finally:
                if old is None:
                    os.environ.pop("BL_PLUGIN_MANAGER_LOCAL_STATE", None)
                else:
                    os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"] = old


if __name__ == "__main__":
    unittest.main()
