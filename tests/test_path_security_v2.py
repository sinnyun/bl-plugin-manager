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
    return importlib.import_module("bl_plugin_manager.security.paths")


class PathSecurityTests(unittest.TestCase):
    def test_valid_record_resolves_inside_kind_root(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            result = mod.resolve_record_path(root, "addons/Demo", "addon")
            self.assertEqual(result, Path(root).resolve() / "addons" / "Demo")

    def test_parent_escape_is_rejected_before_filesystem_use(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(mod.UnsafeLibraryPathError):
                mod.resolve_record_path(root, "addons/../outside", "addon")

    def test_absolute_path_is_rejected(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(mod.UnsafeLibraryPathError):
                mod.resolve_record_path(root, str(Path(root).parent / "outside"), "addon")

    def test_symlink_escape_is_rejected_when_supported(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            root_path = Path(root)
            (root_path / "addons").mkdir()
            try:
                (root_path / "addons" / "link").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            with self.assertRaises(mod.UnsafeLibraryPathError):
                mod.resolve_record_path(root, "addons/link/file.py", "addon")


if __name__ == "__main__":
    unittest.main()
