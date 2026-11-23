import importlib
import sys
import threading
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

    def test_cancelled_worker_discards_staged_result(self):
        mod = _module()
        task = mod.TaskController()
        release = threading.Event()
        discarded = []

        def stage():
            release.wait(2)
            return "staged.zip"

        task.start(stage, discard=discarded.append)
        task.cancel()
        release.set()
        task.join(2)
        self.assertEqual(task.state, "cancelled")
        self.assertEqual(discarded, ["staged.zip"])
        self.assertIsNone(task.result)


if __name__ == "__main__":
    unittest.main()
