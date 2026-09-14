import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1] / "bl_plugin_manager"


class SafetyContractTests(unittest.TestCase):
    def test_source_never_invokes_global_preference_or_factory_reset_operators(self):
        source = "\n".join(path.read_text(encoding="utf-8")
                           for path in ROOT.rglob("*.py"))
        forbidden_calls = (
            r"bpy\.ops\.wm\.save_userpref\s*\(",
            r"bpy\.ops\.wm\.read_factory_settings\s*\(",
            r"bpy\.ops\.wm\.read_homefile\s*\(",
            r"bpy\.ops\.wm\.read_userpref\s*\(",
        )
        for pattern in forbidden_calls:
            self.assertIsNone(re.search(pattern, source), pattern)

    def test_online_access_is_never_assigned(self):
        source = "\n".join(path.read_text(encoding="utf-8")
                           for path in ROOT.rglob("*.py"))
        self.assertIsNone(re.search(r"use_online_access\s*=", source))

    def test_startup_does_not_use_heuristic_orphan_cleanup(self):
        source = (ROOT / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("cleanup_orphaned_mounts", source)


if __name__ == "__main__":
    unittest.main()
