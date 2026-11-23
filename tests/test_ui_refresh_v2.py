import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def _module():
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module("bl_plugin_manager.items")


class Prefs:
    library_path = ""
    active_category = "全部"
    only_favorites = False
    only_updates = False
    only_enabled = False
    only_incompatible = False
    search = ""


class UIRefreshTests(unittest.TestCase):
    def test_unchanged_signature_does_not_rebuild_repeatedly(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            prefs = Prefs()
            prefs.library_path = root
            (Path(root) / ".pm").mkdir()
            (Path(root) / ".pm" / "catalog.json").write_text("{}", encoding="utf-8")
            mod._LAST = {"t": 0.0, "sig": None}
            with patch.object(mod, "rebuild_items") as rebuild, patch.object(mod.time, "time", side_effect=[1.0, 2.0]):
                mod.maybe_rebuild(prefs, force=True)
                mod.maybe_rebuild(prefs)
                self.assertEqual(rebuild.call_count, 1)

    def test_native_names_ttl_cache_avoids_repeated_network_stats(self):
        native = importlib.import_module("bl_plugin_manager.native_names")
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / ".pm").mkdir()
            (Path(root) / ".pm" / "catalog.json").write_text("{}", encoding="utf-8")
            native.invalidate_cache()
            native._CACHE["sig"] = (root, (1, 2), "", None)
            native._CACHE["checked_at"] = 100.0
            native._CACHE["by_module"] = {"demo": "示例"}
            native._CACHE["by_pkg_id"] = {}
            with patch.object(native.time, "monotonic", return_value=101.0), \
                 patch.object(native.os.path, "isdir", side_effect=AssertionError("should not stat within TTL")):
                by_mod, _ = native._load_alias_maps(root)
            self.assertEqual(by_mod, {"demo": "示例"})
            native.invalidate_cache()


if __name__ == "__main__":
    unittest.main()
