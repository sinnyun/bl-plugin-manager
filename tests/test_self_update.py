import hashlib
import importlib
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


def updater():
    package = types.ModuleType("bl_plugin_manager")
    package.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault(package.__name__, package)
    return importlib.import_module("bl_plugin_manager.self_update")


def payload(version="2.4.0"):
    tag = "v" + version
    name = f"bl_plugin_manager-{version}.zip"
    return {"tag_name": tag, "assets": [{
        "name": name, "state": "uploaded", "size": 100,
        "digest": "sha256:" + "a" * 64,
        "browser_download_url": f"https://github.com/sinnyun/bl-plugin-manager/releases/download/{tag}/{name}",
    }]}


class SelfUpdateTests(unittest.TestCase):
    def test_release_requires_exact_named_checksummed_asset_and_a_newer_stable_version(self):
        mod = updater()
        self.assertEqual(mod.release_offer(payload(), (2, 3, 1)).version, (2, 4, 0))
        self.assertIsNone(mod.release_offer(payload(), (2, 4, 0)))
        self.assertIsNone(mod.release_offer(dict(payload(), prerelease=True), (2, 3, 1)))
        invalid = payload()
        invalid["assets"][0]["digest"] = ""
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            mod.release_offer(invalid, (2, 3, 1))
        invalid = payload()
        invalid["assets"][0]["browser_download_url"] = "https://example.com/update.zip"
        with self.assertRaises(ValueError):
            mod.release_offer(invalid, (2, 3, 1))

    def test_verified_package_waits_for_restart_and_keeps_old_installation(self):
        mod = updater()
        with tempfile.TemporaryDirectory() as workspace:
            parent = Path(workspace) / "addons"
            installed = parent / "bl_plugin_manager"
            installed.mkdir(parents=True)
            (installed / "__init__.py").write_text("old installation", encoding="utf-8")
            archive = Path(workspace) / "release.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("bl_plugin_manager/__init__.py",
                                 'bl_info = {"version": (2, 4, 0), "blender": (4, 2, 0)}\n')
                package.writestr("bl_plugin_manager/constants.py", 'ADDON_VERSION = (2, 4, 0)\n')
            release = payload()
            asset = release["assets"][0]
            asset.update(size=archive.stat().st_size,
                         digest="sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest())
            offer = mod.release_offer(release, (2, 3, 1))
            mod.stage_update(installed, archive, offer, (5, 2, 4))
            self.assertEqual((installed / "__init__.py").read_text(encoding="utf-8"), "old installation")
            real_rename = mod.os.replace

            def fail_swap(source, target):
                if Path(source) == mod.pending_path(installed):
                    raise PermissionError("in use")
                return real_rename(source, target)

            with patch.object(mod.os, "replace", side_effect=fail_swap):
                with self.assertRaises(PermissionError):
                    mod.activate_pending(installed, (2, 3, 1), (5, 2, 4))
            self.assertEqual((installed / "__init__.py").read_text(encoding="utf-8"), "old installation")
            old = mod.activate_pending(installed, (2, 3, 1), (5, 2, 4))
            self.assertEqual((old / "__init__.py").read_text(encoding="utf-8"), "old installation")
            self.assertIn("2, 4, 0", (installed / "__init__.py").read_text(encoding="utf-8"))
            self.assertIsNone(mod.activate_pending(installed, (2, 4, 0), (5, 2, 4)))

    def test_stage_update_recovers_from_windows_winerror_5_rename_lock(self):
        mod = updater()
        with tempfile.TemporaryDirectory() as workspace:
            parent = Path(workspace) / "addons"
            installed = parent / "bl_plugin_manager"
            installed.mkdir(parents=True)
            (installed / "__init__.py").write_text("old installation", encoding="utf-8")
            archive = Path(workspace) / "release.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("bl_plugin_manager/__init__.py",
                                 'bl_info = {"version": (2, 4, 0), "blender": (4, 2, 0)}\n')
                package.writestr("bl_plugin_manager/constants.py", 'ADDON_VERSION = (2, 4, 0)\n')
            release = payload()
            asset = release["assets"][0]
            asset.update(size=archive.stat().st_size,
                         digest="sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest())
            offer = mod.release_offer(release, (2, 3, 1))

            real_rename = mod.os.rename

            def locked_stage_rename(src, dst):
                if ".bl_plugin_manager-stage-" in str(src):
                    err = PermissionError("Access is denied")
                    err.winerror = 5
                    raise err
                return real_rename(src, dst)

            with patch.object(mod, "_WIN_RETRY_DELAYS", (0.0, 0.0)), \
                 patch.object(mod.os, "rename", side_effect=locked_stage_rename):
                mod.stage_update(installed, archive, offer, (5, 2, 4))

            pending = mod.pending_path(installed)
            self.assertTrue(pending.is_dir())
            self.assertIn("2, 4, 0", (pending / "__init__.py").read_text(encoding="utf-8"))
