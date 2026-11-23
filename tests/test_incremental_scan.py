"""Unit tests for fingerprint-based incremental library scanning."""

import importlib
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def _load(name):
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    # Stub bpy so library.py can be imported in pure Python unit tests
    if "bpy" not in sys.modules:
        bpy_stub = types.ModuleType("bpy")
        bpy_stub.app = types.SimpleNamespace(version=(5, 2, 0))
        sys.modules["bpy"] = bpy_stub
    if "addon_utils" not in sys.modules:
        sys.modules["addon_utils"] = types.ModuleType("addon_utils")
    return importlib.import_module(name)


def _write_addon(base: Path, folder: str, name: str, version=(1, 0, 0)):
    d = base / "addons" / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "__init__.py").write_text(
        f'bl_info = {{"name": "{name}", "author": "tester", "version": {version}, "blender": (4, 2, 0)}}\n',
        encoding="utf-8",
    )
    return d


class IncrementalScanTests(unittest.TestCase):
    def setUp(self):
        self._state = tempfile.TemporaryDirectory()
        self._env = os.environ.get("BL_PLUGIN_MANAGER_LOCAL_STATE")
        self._envkey = os.environ.get("BL_PLUGIN_MANAGER_ENVIRONMENT")
        os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"] = self._state.name
        os.environ["BL_PLUGIN_MANAGER_ENVIRONMENT"] = "test-inc"

    def tearDown(self):
        self._state.cleanup()
        if self._env is None:
            os.environ.pop("BL_PLUGIN_MANAGER_LOCAL_STATE", None)
        else:
            os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"] = self._env
        if self._envkey is None:
            os.environ.pop("BL_PLUGIN_MANAGER_ENVIRONMENT", None)
        else:
            os.environ["BL_PLUGIN_MANAGER_ENVIRONMENT"] = self._envkey

    def test_unchanged_plugins_skip_metadata_file_reads(self):
        db_mod = _load("bl_plugin_manager.db")
        lib_mod = _load("bl_plugin_manager.library")
        scan_mod = _load("bl_plugin_manager.scan")

        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            _write_addon(root_path, "Alpha", "Alpha Tool")
            _write_addon(root_path, "Beta", "Beta Tool")

            db = db_mod.LibraryDB(root, use_cache=False)
            db.prepare()
            first = lib_mod.scan_library_entries(root, existing_db=db, skip_unchanged=True)
            db.merge_scan(first, persist=True)
            self.assertEqual(db.scan_stats["added"], 2)
            self.assertEqual(db.scan_stats["unchanged"], 0)

            # Second scan with zero changes: read_meta must NOT be called at all!
            db2 = db_mod.LibraryDB(root, use_cache=False)
            with patch.object(scan_mod, "read_meta", side_effect=AssertionError("should not re-read unchanged")):
                second = lib_mod.scan_library_entries(root, existing_db=db2, skip_unchanged=True)
            db2.merge_scan(second, persist=True)
            self.assertEqual(db2.scan_stats["unchanged"], 2)
            self.assertEqual(db2.scan_stats["updated"], 0)
            self.assertEqual(db2.scan_stats["added"], 0)

    def test_only_changed_or_added_plugin_is_reparsed(self):
        db_mod = _load("bl_plugin_manager.db")
        lib_mod = _load("bl_plugin_manager.library")
        scan_mod = _load("bl_plugin_manager.scan")

        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            _write_addon(root_path, "Alpha", "Alpha Tool")
            _write_addon(root_path, "Beta", "Beta Tool")

            db = db_mod.LibraryDB(root, use_cache=False)
            db.prepare()
            db.merge_scan(lib_mod.scan_library_entries(root, existing_db=db), persist=True)

            # Modify Alpha's metadata (same version!) and add Gamma; leave Beta untouched
            time.sleep(0.02)
            _write_addon(root_path, "Alpha", "Alpha Renamed Tool")
            os.utime(root_path / "addons" / "Alpha" / "__init__.py", None)
            _write_addon(root_path, "Gamma", "Gamma Tool")

            parsed_dirs = []
            orig_read = scan_mod.read_meta

            def spy_read(plugin_dir, kind):
                parsed_dirs.append(os.path.basename(plugin_dir))
                return orig_read(plugin_dir, kind)

            db2 = db_mod.LibraryDB(root, use_cache=False)
            with patch.object(scan_mod, "read_meta", side_effect=spy_read):
                entries = lib_mod.scan_library_entries(root, existing_db=db2, skip_unchanged=True)
            db2.merge_scan(entries, persist=True)

            self.assertEqual(sorted(parsed_dirs), ["Alpha", "Gamma"])
            self.assertEqual(db2.scan_stats["unchanged"], 1)
            self.assertEqual(db2.scan_stats["updated"], 1)
            self.assertEqual(db2.scan_stats["added"], 1)
            self.assertEqual(db2.get("addon:alpha-tool")["name"], "Alpha Renamed Tool")


if __name__ == "__main__":
    unittest.main()
