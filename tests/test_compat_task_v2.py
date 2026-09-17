import importlib
import sys
import types
import unittest


def _module():
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module("bl_plugin_manager.compat_task")


def _rec(module="addons/a", load_state="", missing=False, pkg_type="addon", name="A"):
    return {"module": module, "load_state": load_state, "missing": missing,
            "pkg_type": pkg_type, "name": name}


class FakeApi:
    """Stand-in for the Blender bridge used by one task step.

    Tracks enabled state so the disable/re-enable round trip is exercised
    faithfully. ``stubborn`` modules report success on disable but stay
    enabled, which models a plugin that leaves residue behind.
    """

    def __init__(self, enable_ok=True, enable_err="", enabled_after=(),
                 residue_after=0, stubborn=()):
        self.calls = []
        self.enable_ok = enable_ok
        self.enable_err = enable_err
        self.enabled = set(enabled_after)
        self.residue_after = residue_after
        self.stubborn = set(stubborn)

    def set_enabled(self, module, enabled):
        self.calls.append((module, enabled))
        if self.enable_ok:
            if enabled:
                self.enabled.add(module)
            elif module not in self.stubborn:
                self.enabled.discard(module)
        return (self.enable_ok, self.enable_err)

    def is_module_enabled(self, module):
        return module in self.enabled

    def module_residue(self, module):
        return self.residue_after


def _select(mod, records, enabled=(), include_enabled=False, scope=None):
    scope = mod.SCOPE_CONTINUE if scope is None else scope
    return mod.select_targets(records, set(enabled), include_enabled=include_enabled,
                              scope=scope)


class SelectionTests(unittest.TestCase):
    def test_continue_scope_skips_already_measured(self):
        mod = _module()
        records = [
            ("addons/ok", _rec("addons/ok", load_state="ok", name="Ok")),
            ("addons/bad", _rec("addons/bad", load_state="failed", name="Bad")),
            ("addons/new", _rec("addons/new", load_state="", name="New")),
        ]
        sel = _select(mod, records)
        self.assertEqual([t.key for t in sel.targets], ["addons/new"])
        self.assertEqual(sel.skipped_tested, 2)

    def test_retest_all_includes_measured(self):
        mod = _module()
        records = [
            ("addons/ok", _rec("addons/ok", load_state="ok")),
            ("addons/bad", _rec("addons/bad", load_state="failed")),
        ]
        sel = _select(mod, records, scope=mod.SCOPE_RETEST_ALL)
        self.assertEqual([t.key for t in sel.targets], ["addons/ok", "addons/bad"])
        self.assertEqual(sel.skipped_tested, 0)

    def test_skips_theme_missing_and_unbound(self):
        mod = _module()
        records = [
            ("addons/theme", _rec("addons/theme", pkg_type="theme")),
            ("addons/gone", _rec("addons/gone", missing=True)),
            ("addons/none", _rec("", )),
            ("addons/real", _rec("addons/real")),
        ]
        sel = _select(mod, records)
        self.assertEqual([t.key for t in sel.targets], ["addons/real"])
        self.assertEqual(sel.skipped_theme, 1)
        self.assertEqual(sel.skipped_missing, 1)
        self.assertEqual(sel.skipped_unbound, 1)

    def test_enabled_plugins_excluded_unless_requested(self):
        mod = _module()
        records = [("addons/on", _rec("addons/on")), ("addons/off", _rec("addons/off"))]
        sel = _select(mod, records, enabled={"addons/on"})
        self.assertEqual([t.key for t in sel.targets], ["addons/off"])
        self.assertEqual(sel.skipped_enabled, 1)
        self.assertFalse(sel.targets[0].was_enabled)

        sel2 = _select(mod, records, enabled={"addons/on"}, include_enabled=True)
        self.assertEqual({t.key for t in sel2.targets}, {"addons/on", "addons/off"})
        enabled_t = [t for t in sel2.targets if t.key == "addons/on"][0]
        self.assertTrue(enabled_t.was_enabled)

    def test_name_prefers_display_name(self):
        mod = _module()
        rec = _rec("addons/a", name="Actual")
        rec["display_name"] = "Alias"
        sel = _select(mod, [("addons/a", rec)])
        self.assertEqual(sel.targets[0].name, "Alias")


class StepTests(unittest.TestCase):
    def _state(self, mod, n=2, scope=None, include_enabled=False):
        records = [(f"addons/p{i}", _rec(f"addons/p{i}", name=f"P{i}")) for i in range(n)]
        sel = _select(mod, records, scope=scope)
        return mod.TaskState(sel.targets, include_enabled,
                             mod.SCOPE_CONTINUE if scope is None else scope)

    def test_one_step_processes_exactly_one_target(self):
        mod = _module()
        state = self._state(mod, n=2)
        api = FakeApi()
        first = mod.step(state, api)
        self.assertIsNotNone(first)
        self.assertEqual(state.index, 1)
        self.assertFalse(first.done)
        second = mod.step(state, api)
        self.assertEqual(state.index, 2)
        self.assertTrue(second.done)

    def test_cancel_requested_before_step_does_nothing(self):
        mod = _module()
        state = self._state(mod, n=2)
        state.request_cancel()
        self.assertIsNone(mod.step(state, FakeApi()))
        self.assertEqual(state.index, 0)

    def test_cancel_after_current_item_takes_effect_on_next_call(self):
        mod = _module()
        state = self._state(mod, n=2)
        api = FakeApi()
        mod.step(state, api)
        state.request_cancel()
        self.assertIsNone(mod.step(state, api))
        self.assertEqual(state.index, 1)

    def test_success_records_ok_and_clears_residue(self):
        mod = _module()
        state = self._state(mod, n=1)
        api = FakeApi(enable_ok=True)
        outcome = mod.step(state, api)
        self.assertTrue(outcome.ok)
        self.assertFalse(outcome.failed)
        self.assertEqual(outcome.fields["load_state"], "ok")
        self.assertEqual(outcome.fields["residue"], 0)
        self.assertEqual(outcome.fields["restore_error"], "")
        self.assertEqual(state.ok, 1)
        self.assertEqual(state.fail, 0)

    def test_failure_records_error_and_residue(self):
        mod = _module()
        state = self._state(mod, n=1)
        api = FakeApi(enable_ok=False, enable_err="boom", residue_after=3)
        outcome = mod.step(state, api)
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.failed)
        self.assertEqual(outcome.fields["load_state"], "failed")
        self.assertEqual(outcome.fields["load_error"], "boom")
        self.assertEqual(outcome.fields["last_error"], "boom")
        self.assertEqual(outcome.fields["residue"], 3)
        self.assertEqual(state.fail, 1)
        self.assertEqual(state.fails[0]["error"], "boom")

    def test_restore_failure_is_reported(self):
        mod = _module()
        state = self._state(mod, n=1)
        # enable succeeds, but the disable call does not actually unload it
        api = FakeApi(enable_ok=True, stubborn={"addons/p0"})
        outcome = mod.step(state, api)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.fields["load_state"], "failed")
        self.assertEqual(outcome.fields["load_error"], "加载成功但无法恢复原启用状态")

    def test_enabled_retest_blocks_when_disable_fails(self):
        mod = _module()
        records = [("addons/p0", _rec("addons/p0", name="P0"))]
        sel = mod.select_targets(records, {"addons/p0"}, include_enabled=True,
                                 scope=mod.SCOPE_CONTINUE)
        state = mod.TaskState(sel.targets, True, mod.SCOPE_CONTINUE)
        api = FakeApi(enable_ok=False, enabled_after={"addons/p0"})
        outcome = mod.step(state, api)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.fields["load_error"], "无法开始复测：停用失败")
        # the blocked target must not even attempt to enable
        self.assertEqual([c for c in api.calls if c[1] is True], [])

    def test_enabled_retest_success_keeps_enabled(self):
        mod = _module()
        records = [("addons/p0", _rec("addons/p0", name="P0"))]
        sel = mod.select_targets(records, {"addons/p0"}, include_enabled=True,
                                 scope=mod.SCOPE_CONTINUE)
        state = mod.TaskState(sel.targets, True, mod.SCOPE_CONTINUE)
        # disable sticks, then the re-enable succeeds: original state preserved
        api = FakeApi(enable_ok=True, enabled_after={"addons/p0"})
        outcome = mod.step(state, api)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.fields["load_state"], "ok")
        self.assertTrue(outcome.fields["enabled"])
        # disable then re-enable must both have been requested
        self.assertEqual(api.calls, [("addons/p0", False), ("addons/p0", True)])
        self.assertEqual(state.ok, 1)


class TextTests(unittest.TestCase):
    def test_cancelled_summary_reports_remaining(self):
        mod = _module()
        records = [(f"addons/p{i}", _rec(f"addons/p{i}")) for i in range(4)]
        sel = _select(mod, records)
        state = mod.TaskState(sel.targets, False, mod.SCOPE_CONTINUE)
        mod.step(state, FakeApi())
        text = mod.summary_text(state, cancelled=True)
        self.assertIn("已完成 1/4", text)
        self.assertIn("未完成 3", text)

    def test_completed_summary_reports_counts(self):
        mod = _module()
        records = [("addons/a", _rec("addons/a")), ("addons/b", _rec("addons/b"))]
        state = mod.TaskState(_select(mod, records).targets, False, mod.SCOPE_CONTINUE)
        mod.step(state, FakeApi(enable_ok=True))
        mod.step(state, FakeApi(enable_ok=False, enable_err="x"))
        text = mod.summary_text(state)
        self.assertIn("✓ 支持 1", text)
        self.assertIn("✗ 不支持 1", text)

    def test_no_targets_text_suggests_retest_all(self):
        mod = _module()
        records = [("addons/a", _rec("addons/a", load_state="ok"))]
        sel = _select(mod, records)
        self.assertEqual(sel.targets, [])
        self.assertIn("全部重新测试", mod.no_targets_text(sel, mod.SCOPE_CONTINUE))

    def test_no_targets_text_suggests_include_enabled(self):
        mod = _module()
        records = [("addons/a", _rec("addons/a"))]
        sel = _select(mod, records, enabled={"addons/a"})
        self.assertEqual(sel.targets, [])
        self.assertIn("也测试已启用的插件", mod.no_targets_text(sel, mod.SCOPE_CONTINUE))


if __name__ == "__main__":
    unittest.main()
