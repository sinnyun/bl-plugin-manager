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
    return importlib.import_module("bl_plugin_manager.services.activation")


class ActivationTests(unittest.TestCase):
    def test_activation_runs_once_in_order(self):
        mod = _module()
        with tempfile.TemporaryDirectory() as root:
            calls = []
            service = mod.LibraryService(
                load_database=lambda p: calls.append("load") or object(),
                persist_path=lambda p: calls.append("persist"),
                reconcile_mounts=lambda p: calls.append("mount"),
                refresh_modules=lambda: calls.append("modules"),
                scan=lambda p, db: calls.append("scan"),
                publish=lambda: calls.append("publish"),
            )
            report = service.activate(Path(root))
            self.assertEqual(report.status, "READY")
            self.assertEqual(calls, ["load", "persist", "mount", "modules", "scan", "publish"])


if __name__ == "__main__":
    unittest.main()
