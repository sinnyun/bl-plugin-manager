import importlib
import sys
import types
import unittest


def _module():
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module("bl_plugin_manager.tasks")


class TaskTests(unittest.TestCase):
    def test_task_reports_success_and_result(self):
        mod = _module()
        task = mod.TaskController()
        task.start(lambda: 42)
        task.join()
        self.assertEqual(task.state, "succeeded")
        self.assertEqual(task.result, 42)

    def test_task_reports_failure_without_raising_from_worker(self):
        mod = _module()
        task = mod.TaskController()
        task.start(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        task.join()
        self.assertEqual(task.state, "failed")
        self.assertIn("boom", task.error)


if __name__ == "__main__":
    unittest.main()
