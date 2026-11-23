"""Unit tests for migrate candidate discovery (user_default, default addons, temp/backup filtering)."""

import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _load_migrate():
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    bpy_stub = sys.modules.get("bpy")
    if bpy_stub is None:
        bpy_stub = types.ModuleType("bpy")
        sys.modules["bpy"] = bpy_stub
    if not hasattr(bpy_stub, "app"):
        bpy_stub.app = types.SimpleNamespace(version=(5, 2, 0))
    if not hasattr(bpy_stub, "utils"):
        bpy_stub.utils = types.SimpleNamespace(user_resource=lambda *a, **kw: "")
    if not hasattr(bpy_stub, "context"):
        bpy_stub.context = types.SimpleNamespace(
            preferences=types.SimpleNamespace(
                filepaths=types.SimpleNamespace(script_directories=[]),
                extensions=types.SimpleNamespace(repos=[]),
                addons={},
            )
        )
    if "addon_utils" not in sys.modules:
        sys.modules["addon_utils"] = types.ModuleType("addon_utils")
    return importlib.import_module("bl_plugin_manager.migrate")


class MigrateDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._state = tempfile.TemporaryDirectory()
        self._env = os.environ.get("BL_PLUGIN_MANAGER_LOCAL_STATE")
        self._envkey = os.environ.get("BL_PLUGIN_MANAGER_ENVIRONMENT")
        os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"] = self._state.name
        os.environ["BL_PLUGIN_MANAGER_ENVIRONMENT"] = "test-mig"

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

    def test_discovers_user_default_and_default_addons_while_skipping_backups_and_self(self):
        mig = _load_migrate()
        db_mod = importlib.import_module("bl_plugin_manager.db")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            lib = base / "library"
            lib.mkdir()
            db = db_mod.LibraryDB(str(lib), use_cache=False)
            db.prepare()

            # Default user scripts/addons: real addon + self + hidden backup
            user_addons = base / "user_addons"
            for folder, name in (("MyLegacy", "My Legacy"), ("bl_plugin_manager", "Self"),
                                 (".bl_plugin_manager-previous-1", "OldSelf")):
                d = user_addons / folder
                d.mkdir(parents=True)
                (d / "__init__.py").write_text(
                    f'bl_info = {{"name": "{name}", "version": (1, 0, 0), "blender": (4, 2, 0)}}\n',
                    encoding="utf-8",
                )

            # user_default extension repo: real blenderkit + updater backup temp
            user_def = base / "user_default"
            for folder, pid, name in (
                ("blenderkit", "blenderkit", "Blendkit Online Asset Library"),
                ("blenderkit_updater_backup_temp", "blenderkit", "Blendkit Online Asset Library"),
            ):
                d = user_def / folder
                d.mkdir(parents=True)
                (d / "blender_manifest.toml").write_text(
                    f'schema_version = "1.0.0"\nid = "{pid}"\nversion = "3.15.0"\n'
                    f'name = "{name}"\nmaintainer = "bk"\nlicense = ["SPDX:GPL-3.0-or-later"]\n'
                    'type = "add-on"\nblender_version_min = "4.2.0"\n',
                    encoding="utf-8",
                )
                (d / "__init__.py").write_text("def register(): pass\n", encoding="utf-8")

            # System repo: must be skipped
            sys_repo = base / "system_repo"
            (sys_repo / "sys_ext").mkdir(parents=True)
            (sys_repo / "sys_ext" / "blender_manifest.toml").write_text(
                'schema_version = "1.0.0"\nid = "sys_ext"\nversion = "1.0.0"\n'
                'name = "Sys"\nmaintainer = "b"\nlicense = ["SPDX:GPL-3.0-or-later"]\n'
                'type = "add-on"\nblender_version_min = "4.2.0"\n',
                encoding="utf-8",
            )

            orig_ur = mig.bpy.utils.user_resource
            orig_repos = mig.bpy.context.preferences.extensions.repos
            orig_scripts = mig.bpy.context.preferences.filepaths.script_directories
            orig_builtins = mig.bridge.builtin_module_names
            try:
                mig.bpy.utils.user_resource = lambda res, path="": str(user_addons) if path == "addons" else ""
                mig.bpy.context.preferences.filepaths.script_directories = []
                mig.bpy.context.preferences.extensions.repos = [
                    types.SimpleNamespace(module="user_default", name="User Default",
                                          source="USER", directory=str(user_def), custom_directory=""),
                    types.SimpleNamespace(module="system", name="System",
                                          source="SYSTEM", directory=str(sys_repo), custom_directory=""),
                ]
                mig.bridge.builtin_module_names = lambda: {"cycles", "node_wrangler"}

                cands = mig.collect_candidates(str(lib), db)
            finally:
                mig.bpy.utils.user_resource = orig_ur
                mig.bpy.context.preferences.extensions.repos = orig_repos
                mig.bpy.context.preferences.filepaths.script_directories = orig_scripts
                mig.bridge.builtin_module_names = orig_builtins

            folders = {c["folder"] for c in cands}
            self.assertEqual(folders, {"MyLegacy", "blenderkit"})


if __name__ == "__main__":
    unittest.main()
