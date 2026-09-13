import importlib
import io
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path


def _module():
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module("bl_plugin_manager.security.archive")


class ArchiveSecurityTests(unittest.TestCase):
    def test_rejects_parent_member_before_extract(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../outside.txt", "bad")
            with self.assertRaises(mod.UnsafeArchiveError):
                mod.inspect_archive(archive)

    def test_rejects_too_many_members(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / "many.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                for i in range(3):
                    zf.writestr(f"plugin/{i}.txt", "x")
            with self.assertRaises(mod.ArchiveLimitError):
                mod.inspect_archive(archive, max_members=2)


if __name__ == "__main__":
    unittest.main()
