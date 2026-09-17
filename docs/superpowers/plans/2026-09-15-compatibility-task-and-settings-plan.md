# 兼容性测试任务与设置面板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「一键测试插件支持」成为不阻塞 Blender 界面、可见进度、可取消的分步任务；把低频维护入口从 3D 视图侧栏/顶部栏收进插件偏好设置，并删除顶部栏入口。

**Architecture:** 新增纯 Python 模块 `bl_plugin_manager/compat_task.py`，把目标筛选、任务状态机、单步执行（沿用现有真实加载/恢复/残留判定）与摘要文案提取为不依赖 `bpy` 的逻辑，通过注入的 `api` 调用 Blender；`operators.py` 用 `bpy.app.timers` 每 tick 精确处理一个目标，把进度镜像到偏好属性，由侧栏绘制进度条与取消按钮。界面入口搬迁只改注册与绘制位置，操作符与数据存储保持不变。

**Tech Stack:** Blender Python API（`bpy.app.timers`、`EnumProperty`、`UILayout.progress`）、`unittest`、隔离 Blender 回归（`_test/run_regressions.ps1`）、端到端（`_test/run_e2e.ps1`）、静态 UI 检查（`_test/check_ui.py`）。

## Global Constraints

- 不使用后台线程调用 Blender API；分步调度只允许主线程计时器。
- 不改变每个插件实际加载 / 恢复 / 残留的判定标准，只改调度方式。
- 不删除现有低频功能，不改变共享库、本机配置、设备配置的数据边界。
- 现有操作符 `bl_idname`、属性名、存储格式保持不变；搬迁只改入口位置。
- 取消不得中断正在运行的插件加载或卸载：只在当前目标处理结束后停止。
- 顶部栏模块删除后，`bl_plugin_manager` 不再有任何 `VIEW3D_HT_header` 注册。
- 每次任务结束（完成 / 取消 / 异常）必须：注销计时器、清空状态栏、保存已处理记录、刷新列表、生成可查看摘要。

---

## 设计要点（供实现者对齐）

**范围语义**

- `continue`（继续未完成项，默认）：仅挑选 `load_state` 不在 `{"ok", "failed"}` 的目标；已完成结果原样保留。
- `retest_all`（全部重新测试）：挑选全部可测目标；每个目标在被实测时由 `compat_task.step` 覆盖其旧 `load_state / load_error / restore_error / residue`，因此**取消不会销毁尚未重测的旧结果**。
- 目标筛选沿用现有规则：跳过无 `module`、`missing`、`pkg_type/type == "theme"`；已启用插件仅在勾选「也测试已启用的插件」时纳入。

**状态机与调度**

- `TaskState` 持有稳定目标列表、`index`、`ok`、`fail`、`fails`、`cancelled`，`len(targets)` 为总数。
- `compat_task.step(state, api)` 每次只处理一个目标并推进 `index`；返回 `None` 表示无待办（已完成或已取消）。
- 计时器回调每 tick 调一次 `step`，主线程在两次 tick 之间完成重绘与事件处理，界面不阻塞。

---

### Task 1: 建立不依赖 Blender 的任务逻辑与失败测试

**Files:**
- Create: `bl_plugin_manager/compat_task.py`
- Create: `tests/test_compat_task_v2.py`

**Interfaces:**
- Produces: `SCOPE_CONTINUE = "continue"`、`SCOPE_RETEST_ALL = "retest_all"`、`SCOPE_ITEMS`（供 `EnumProperty` 使用的三元组元组）。
- Produces: `Target(key, module, name, was_enabled)`、`Selection(targets, skipped_tested, skipped_enabled, skipped_theme, skipped_missing, skipped_unbound)`。
- Produces: `select_targets(records, enabled_modules, include_enabled=False, scope=SCOPE_CONTINUE) -> Selection`，其中 `records` 是 `(key, rec)` 可迭代对象。
- Produces: `TaskState(targets, include_enabled, scope)`，提供 `total`、`pending()`、`current_name`、`request_cancel()`。
- Produces: `StepOutcome(key, name, fields, ok, failed, fail, done)`。
- Produces: `step(state, api) -> StepOutcome | None`；`api` 需提供 `set_enabled(module, bool) -> (ok, err)`、`is_module_enabled(module) -> bool`、`module_residue(module) -> int`。
- Produces: `summary_text(state, cancelled=False) -> str`、`no_targets_text(selection, scope) -> str`。
- Consumes: 仅 Python 标准库（不 import `bpy`、不 import `.constants`）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_compat_task_v2.py`，沿用 `tests/test_tasks_v2.py` 的包桩写法：

```python
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
    def __init__(self, enable_ok=True, enable_err="", enabled_after=()):
        self.calls = []
        self.enable_ok = enable_ok
        self.enable_err = enable_err
        self.enabled_after = set(enabled_after)

    def set_enabled(self, module, enabled):
        self.calls.append((module, enabled))
        return (self.enable_ok, self.enable_err)

    def is_module_enabled(self, module):
        return module in self.enabled_after

    def module_residue(self, module):
        return 0
```

断言用例：

- `select_targets` 的 `continue` 范围跳过 `load_state in ("ok","failed")`，`retest_all` 纳入它们；两者都跳过 theme / missing / 无 module，并且未勾选 `include_enabled` 时跳过已启用项（计入 `skipped_enabled`）。
- `step` 一次只推进一个目标：连续两次 `step` 后 `state.index == 2`。
- 已取消的 state，`step` 返回 `None`；在 `step` 完成后调用 `request_cancel()` 再 `step`，返回 `None` 且 `state.index` 不再增加。
- `api.set_enabled` 成功且原状态为未启用时，`fields["load_state"] == "ok"`、`"residue" == 0`、`state.ok == 1`。
- `api.set_enabled` 失败时，`fields["load_state"] == "failed"`、`fields["load_error"]` 非空、`state.fail == 1`、`state.fails[0]["error"]` 非空。
- `summary_text(state, cancelled=True)` 同时包含已完成数与「未完成」；`summary_text(state)` 完成后包含 `✓ 支持` 与 `✗ 不支持`。
- `no_targets_text` 在 `continue` 且只有已测项时提示可改用「全部重新测试」；在只跳过已启用项时提示勾选「也测试已启用的插件」。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest tests.test_compat_task_v2 -v`
Expected: FAIL，`ModuleNotFoundError: bl_plugin_manager.compat_task`。

- [ ] **Step 3: 实现 `compat_task.py`**

按上文 Interfaces 实现。`step` 的判断顺序与现有 `PM_OT_verify_compat.execute` 循环体逐一对应，勿改变判定：

```python
@dataclass(frozen=True)
class Target:
    key: str
    module: str
    name: str
    was_enabled: bool


@dataclass
class Selection:
    targets: list[Target]
    skipped_tested: int = 0
    skipped_enabled: int = 0
    skipped_theme: int = 0
    skipped_missing: int = 0
    skipped_unbound: int = 0


MEASURED = ("ok", "failed")


def select_targets(records, enabled_modules, include_enabled=False, scope=SCOPE_CONTINUE):
    sel = Selection(targets=[])
    for key, rec in records:
        module = (rec.get("module") or "").strip()
        if not module:
            sel.skipped_unbound += 1
            continue
        if rec.get("missing"):
            sel.skipped_missing += 1
            continue
        if (rec.get("pkg_type") or rec.get("type") or "").strip() == "theme":
            sel.skipped_theme += 1
            continue
        was_enabled = module in enabled_modules
        if was_enabled and not include_enabled:
            sel.skipped_enabled += 1
            continue
        if scope != SCOPE_RETEST_ALL and (rec.get("load_state") or "") in MEASURED:
            sel.skipped_tested += 1
            continue
        sel.targets.append(Target(key, module,
                                  rec.get("display_name") or rec.get("name") or key,
                                  was_enabled))
    return sel
```

`step` 的字段写入必须与旧实现等价：复测已启用项停用失败 → `load_error="无法开始复测：停用失败"`、`restore_error` 记录停用错误；启用成功但恢复失败 → `load_error="加载成功但无法恢复原启用状态"`；启用失败 → `residue=api.module_residue(module)`、`last_error=err`。成功分支额外写 `residue=0`（清除上轮残留标记，属显示一致性修复，不改变判定标准）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m unittest tests.test_compat_task_v2 -v`
Expected: 全部通过。

---

### Task 2: 把「一键测试」改为计时器分步任务（含取消）

**Files:**
- Modify: `bl_plugin_manager/operators.py`（`PM_OT_verify_compat`、新增 `PM_OT_cancel_compat`、`classes`、顶部 `from bpy.props import` 增加 `EnumProperty`）
- Modify: `bl_plugin_manager/preferences.py`（新增任务运行时属性，Task 3 会绘制）
- Modify: `bl_plugin_manager/__init__.py`（注册/注销时启动与关闭任务）

**Interfaces:**
- Consumes: Task 1 的 `compat_task` 全部导出。
- Produces: `PM_OT_verify_compat` 新增 `scope: EnumProperty(items=compat_task.SCOPE_ITEMS, default=compat_task.SCOPE_CONTINUE)`，`invoke` 弹窗同时展示范围与「也测试已启用的插件」。
- Produces: `PM_OT_cancel_compat`（`bl_idname="plugin_manager.cancel_compat"`）。
- Produces: `operators.shutdown_compat_task()` 供 `__init__.unregister()` 调用。
- Produces: `PMAddonPreferences` 运行时属性 `compat_running`、`compat_cancelling`、`compat_total`、`compat_done`、`compat_ok`、`compat_fail`、`compat_current`（均 `options={"SKIP_SAVE"}`）。

- [ ] **Step 1: 写失败回归检查**

在 `_test/test_regressions.py` 增加（该文件在隔离 Blender 中运行，用 `check()` 记录）：

```python
from bl_plugin_manager import compat_task
from bl_plugin_manager import operators as pm_ops

check("cancel operator registered",
      hasattr(bpy.ops.plugin_manager, "cancel_compat"))
check("verify operator exposes scope",
      "scope" in pm_ops.PM_OT_verify_compat.bl_rna.properties)

# 用 Blender 兼容的模拟上下文验证模态调度：一次 tick 只处理一项
records = [("addons/a", {"module": "a", "name": "A", "load_state": ""}),
           ("addons/b", {"module": "b", "name": "B", "load_state": ""})]
sel = compat_task.select_targets(records, enabled_modules=set(),
                                 include_enabled=False, scope=compat_task.SCOPE_RETEST_ALL)
state = compat_task.TaskState(sel.targets, False, compat_task.SCOPE_RETEST_ALL)
api = _FakeCompatApi()  # 测试内定义：set_enabled 恒返回 (True, "")
compat_task.step(state, api)
check("one tick processes exactly one target", state.index == 1)
state.request_cancel()
check("cancel stops after current item",
      compat_task.step(state, api) is None and state.index == 1)
```

（`_FakeCompatApi` 在测试文件内以 `class` 定义，提供 `set_enabled/is_module_enabled/module_residue`。）

- [ ] **Step 2: 运行确认失败**

Run: `powershell -ExecutionPolicy Bypass -File _test\run_regressions.ps1`
Expected: FAIL（`cancel_compat` 未注册、`scope` 不存在）。

- [ ] **Step 3: 新增运行时属性**

在 `PMAddonPreferences` 的界面状态区加入：

```python
    # 兼容性测试任务运行时状态（不跨会话保存）
    compat_running: BoolProperty(default=False, options={"SKIP_SAVE"})
    compat_cancelling: BoolProperty(default=False, options={"SKIP_SAVE"})
    compat_total: IntProperty(default=0, options={"SKIP_SAVE"})
    compat_done: IntProperty(default=0, options={"SKIP_SAVE"})
    compat_ok: IntProperty(default=0, options={"SKIP_SAVE"})
    compat_fail: IntProperty(default=0, options={"SKIP_SAVE"})
    compat_current: StringProperty(default="", options={"SKIP_SAVE"})
```

- [ ] **Step 4: 用分步任务替换 `PM_OT_verify_compat.execute`**

删除原一次性循环与前置 `load_state` 清零块，改为：

```python
class PM_OT_verify_compat(_PMBase):
    bl_idname = "plugin_manager.verify_compat"
    bl_label = "一键测试插件支持"
    bl_description = "逐个真实加载插件测出可用性；分步执行，不阻塞界面，可随时取消"

    scope: EnumProperty(name="测试范围", items=compat_task.SCOPE_ITEMS,
                        default=compat_task.SCOPE_CONTINUE)
    include_enabled: BoolProperty(name="也测试已启用的插件", default=False)

    def invoke(self, context, event):
        prefs = _prefs(context)
        if prefs and prefs.compat_running:
            return _report(self, False, "已有测试任务在运行，请等待完成或先取消")
        return context.window_manager.invoke_props_dialog(self, width=460)

    def execute(self, context):
        prefs, db = _prefs(context), _db(context)
        if not prefs or not db:
            return _report(self, False, "插件库未就绪")
        if prefs.compat_running:
            return _report(self, False, "已有测试任务在运行")
        selection = compat_task.select_targets(
            list(db.plugins.items()), bridge.enabled_modules(),
            include_enabled=self.include_enabled, scope=self.scope)
        if not selection.targets:
            return _report(self, True, compat_task.no_targets_text(selection, self.scope))
        _compat_start(prefs, db, selection, self.include_enabled, self.scope)
        return _report(self, True,
                       f"已开始测试 {len(selection.targets)} 个插件；可在侧栏查看进度或取消")
```

- [ ] **Step 5: 加入任务驱动与取消操作符**

在 `operators.py` 状态辅助函数旁新增模块级运行时与回调：

```python
_COMPAT: dict = {"state": None, "db": None, "prefs": None}


class _CompatBridgeAPI:
    def set_enabled(self, module, enabled):
        return bridge.set_enabled(module, enabled)

    def is_module_enabled(self, module):
        return bridge.is_module_enabled(module)

    def module_residue(self, module):
        return bridge.module_residue(module)


def _compat_start(prefs, db, selection, include_enabled, scope):
    state = compat_task.TaskState(selection.targets, include_enabled, scope)
    _COMPAT.update(state=state, db=db, prefs=prefs)
    prefs.compat_running = True
    prefs.compat_cancelling = False
    prefs.compat_total = state.total
    prefs.compat_done = prefs.compat_ok = prefs.compat_fail = 0
    prefs.compat_current = state.current_name
    _status(f"插件库：正在测试 0/{state.total} …")
    _compat_register_timer()


def _compat_register_timer():
    if not bpy.app.timers.is_registered(_compat_tick):
        bpy.app.timers.register(_compat_tick, first_interval=0.0)


def _compat_stop_timer():
    if bpy.app.timers.is_registered(_compat_tick):
        bpy.app.timers.unregister(_compat_tick)


def _compat_tick():
    state, prefs, db = _COMPAT.get("state"), _COMPAT.get("prefs"), _COMPAT.get("db")
    if state is None or prefs is None or db is None:
        _compat_stop_timer()
        return None
    try:
        if state.cancelled or not state.pending():
            _compat_finish(state, db, prefs, cancelled=state.cancelled)
            return None
        outcome = compat_task.step(state, _CompatBridgeAPI())
        if outcome is not None:
            record = db.get(outcome.key) or {}
            record.update(outcome.fields)
            db.upsert(outcome.key, record)
            db.save()                      # 每步落盘，取消/异常不丢已处理记录
            prefs.compat_done = state.index
            prefs.compat_ok = state.ok
            prefs.compat_fail = state.fail
            prefs.compat_current = state.current_name
        _status(f"插件库：正在测试 {state.index}/{state.total} — {state.current_name}")
        _redraw()
        if not state.pending():
            _compat_finish(state, db, prefs, cancelled=False)
            return None
        return 0.02
    except Exception as exc:              # noqa: BLE001 — 任务必须清理并留痕
        print("[插件库] 兼容性测试任务异常:", exc)
        _compat_finish(state, db, prefs, cancelled=False, error=str(exc))
        return None


def _compat_finish(state, db, prefs, cancelled, error=""):
    _compat_stop_timer()
    _clear_status()
    try:
        db.save()
    except Exception as exc:
        print("[插件库] 保存测试结果失败:", exc)
    prefs.report_items.clear()
    for item in state.fails:
        entry = prefs.report_items.add()
        entry.name = item.get("name") or ""
        entry.path = item.get("key") or ""
        entry.kind = "兼容性"
        entry.status = "failed"
        entry.detail = item.get("error") or ""
    prefs.report_summary = compat_task.summary_text(state, cancelled=cancelled)
    log_entries = [{"name": i.get("name"), "path": i.get("key"), "kind": "兼容性",
                    "status": "failed", "detail": i.get("error")} for i in state.fails]
    if error:
        prefs.report_summary += f"；任务异常：{error}"
        log_entries.append({"name": "任务异常", "path": "", "kind": "兼容性",
                            "status": "failed", "detail": error})
    _write_report_log(prefs, {"entries": log_entries})

    from . import items
    items.rebuild_items(prefs)
    prefs.compat_running = False
    prefs.compat_cancelling = False
    prefs.compat_current = ""
    try:
        bridge.save_prefs()
    except Exception:
        pass
    _redraw()
    if state.fails and not cancelled:
        try:
            bpy.ops.plugin_manager.show_report()
        except Exception:
            pass
    _COMPAT.update(state=None, db=None, prefs=None)


def shutdown_compat_task():
    state, prefs, db = _COMPAT.get("state"), _COMPAT.get("prefs"), _COMPAT.get("db")
    if state is not None and db is not None and prefs is not None:
        state.request_cancel()
        _compat_finish(state, db, prefs, cancelled=True)
    _compat_stop_timer()


class PM_OT_cancel_compat(_PMBase):
    bl_idname = "plugin_manager.cancel_compat"
    bl_label = "取消兼容性测试"
    bl_description = "当前插件处理结束后停止后续测试；已完成结果会保留"

    def execute(self, context):
        state = _COMPAT.get("state")
        prefs = _prefs(context)
        if state is None or prefs is None:
            return _report(self, False, "没有正在运行的测试")
        state.request_cancel()
        prefs.compat_cancelling = True
        _status("插件库：正在取消，等待当前插件处理结束…")
        _redraw()
        return _report(self, True, "已请求取消：当前插件处理结束后停止，已完成结果会保留")
```

把 `PM_OT_cancel_compat` 加入 `operators.py` 末尾的 `classes` 元组，并在顶部 `from . import (...) " 增加 `compat_task`。

- [ ] **Step 6: 注册生命周期接线**

在 `bl_plugin_manager/__init__.py`：

```python
from . import (
    bridge, compat_task, db, items, library, migrate, operators,
    preferences, scoped_management, scan, store, ui, updates, watcher,
)   # 移除 header
```

`unregister()` 中在注销类之前调用：

```python
    try:
        operators.shutdown_compat_task()
    except Exception as exc:
        print("[插件库] 关闭兼容性测试任务失败:", exc)
```

`register()` 的回滚分支同样先调用 `operators.shutdown_compat_task()`。

- [ ] **Step 7: 运行确认通过**

Run: `powershell -ExecutionPolicy Bypass -File _test\run_regressions.ps1`
Expected: 新增检查通过，既有 18 项保持通过。

---

### Task 3: 侧栏进度与取消 UI

**Files:**
- Modify: `bl_plugin_manager/ui.py`（`PM_PT_main.draw`、新增 `_progress_bar` 辅助）

**Interfaces:**
- Consumes: `prefs.compat_running / compat_total / compat_done / compat_ok / compat_fail / compat_current / compat_cancelling`（Task 2）。
- Produces: `ui._progress_bar(layout, factor, text)`；`PM_PT_main` 在任务运行时显示 `已完成 / 总数`、当前插件名、进度条与「取消」按钮。

- [ ] **Step 1: 写 UI 静态断言**

在 `_test/check_ui.py` 增加（Task 7 统一整理）：`ui.py` 必须出现 `"plugin_manager.cancel_compat"` 与 `compat_running`。

- [ ] **Step 2: 实现进度区**

在 `PM_PT_main.draw` 的「一键测试」行处替换：

```python
        row = layout.row(align=True)
        if prefs.compat_running:
            row.operator("plugin_manager.cancel_compat", icon="X", text="取消测试")
        else:
            row.operator("plugin_manager.verify_compat", icon="CHECKMARK",
                         text="一键测试插件支持")

        if prefs.compat_running:
            box = layout.box()
            box.label(text=f"测试进度 {prefs.compat_done} / {prefs.compat_total}", icon="TIME")
            if prefs.compat_current:
                box.label(text=f"当前: {prefs.compat_current}")
            _progress_bar(box, prefs.compat_done / max(1, prefs.compat_total),
                          f"{prefs.compat_done}/{prefs.compat_total}")
            sub = box.row(align=True)
            sub.label(text=f"✓ {prefs.compat_ok}　✗ {prefs.compat_fail}")
            sub.operator("plugin_manager.cancel_compat", icon="X", text="取消")
            if prefs.compat_cancelling:
                box.label(text="正在取消，等待当前插件处理结束…", icon="INFO")
```

并新增辅助：

```python
def _progress_bar(layout, factor, text):
    """画进度条；不支持 progress 的布局回退为文本，保证进度始终可读。"""
    try:
        layout.progress(factor=max(0.0, min(1.0, float(factor))), text=text)
    except Exception:
        layout.label(text=text)
```

- [ ] **Step 3: 运行 UI 检查**

Run: `python _test/check_ui.py`
Expected: `===UI_CHECK_OK===`。

---

### Task 4: 设置面板收纳低频配置与维护

**Files:**
- Modify: `bl_plugin_manager/preferences.py`（`PMAddonPreferences.draw`）
- Modify: `bl_plugin_manager/__init__.py`（让「启动同步策略」真正生效）

**Interfaces:**
- Consumes: 现有操作符 `plugin_manager.setup_library / unmount_library / refresh / scan_inbox / show_warnings / clear_updates / cleanup_residue / scan_candidates / import_candidates / apply_startup / unify_store`。
- Produces: 偏好设置面板分区：设备与库、挂载、诊断与维护、启动同步、迁移收编、官方商店目录关联。

- [ ] **Step 1: 扩展 `PMAddonPreferences.draw`**

在现有设备名/路径/挂载状态之后，按分区补齐（保持 `self.layout` 与 `box` 用法一致）：

```python
        row = col.row(align=True)
        row.operator("plugin_manager.setup_library", icon="LINKED")
        row.operator("plugin_manager.unmount_library", icon="UNLINKED")
        row = col.row(align=True)
        row.operator("plugin_manager.refresh", icon="FILE_REFRESH")
        row.operator("plugin_manager.scan_inbox", icon="FILE_REFRESH", text="扫描投放区")
        col.operator("plugin_manager.show_warnings", icon="INFO", text="诊断信息")

        box = layout.box()
        box.label(text="启动同步策略", icon="PLAY")
        box.prop(self, "sync_startup_on_launch")
        box.prop(self, "startup_disable_unmarked")
        box.operator("plugin_manager.apply_startup", icon="PLAY", text="立即同步自启状态")

        box = layout.box()
        box.label(text="迁移收编", icon="IMPORT")
        box.operator("plugin_manager.scan_candidates", icon="VIEWZOOM", text="扫描可收编插件")
        if self.candidate_count:
            box.label(text=f"可收编 {self.candidate_count} 个")
        box.operator("plugin_manager.import_candidates", icon="IMPORT", text="收编全部")
        box.operator("plugin_manager.unify_store", icon="LINKED",
                     text="让官方商店使用本插件库")

        box = layout.box()
        box.label(text="维护", icon="TOOL_SETTINGS")
        box.operator("plugin_manager.cleanup_residue", icon="TRASH", text="清理失败插件残留")
        box.operator("plugin_manager.clear_updates", icon="X", text="清除更新标记")
```

- [ ] **Step 2: 让启动同步策略生效**

`__init__.py` 的 2 秒配置镜像计时器（`_sync_manager_state`）之外，新增一次启动同步：

```python
def _apply_launch_sync():
    """仅当偏好开启时，按库内「自启」标记同步一次插件启用状态。"""
    prefs = bridge.get_prefs()
    if not prefs or not prefs.sync_startup_on_launch or not prefs.library_path:
        return
    try:
        from .db import LibraryDB
        library.apply_startup(prefs.library_path, LibraryDB(prefs.library_path),
                              enable_marked=True,
                              disable_unmarked=prefs.startup_disable_unmarked)
        from . import items
        items.rebuild_items(prefs)
    except Exception as exc:
        print("[插件库] 启动同步失败:", exc)
```

在 `register()` 中与 `_sync_manager_state` 并列注册 `first_interval=2.5`（`persistent=False`），并在 `unregister()` 中 `bpy.app.timers.unregister(_apply_launch_sync)`（用 try/except 包裹）。

- [ ] **Step 3: 静态检查确认入口可触达**

Run: `python _test/check_ui.py; python _test/audit_features.py`（audit 需 Task 7 先去掉 header 读取）
Expected: 无「关键功能入口缺失」。

---

### Task 5: 移除侧栏「迁移与启动控制」与库菜单低频项

**Files:**
- Modify: `bl_plugin_manager/ui.py`（删除 `PM_PT_tools`、精简 `PM_MT_library`、`classes`）

**Interfaces:**
- Consumes: Task 4 已把低频入口放入偏好设置。
- Produces: 侧栏不再有 `PM_PT_tools`；`PM_MT_library` 仅保留库路径显示、启用/修复挂载、刷新列表。

- [ ] **Step 1: 删除 `PM_PT_tools`**

删除 `ui.py` 中 `class PM_PT_tools(Panel):` 整段（原 535–574 行）及 `classes` 元组中的 `PM_PT_tools,`。

- [ ] **Step 2: 精简库菜单**

`PM_MT_library.draw` 保留：

```python
        if prefs.library_path:
            sub = layout.column()
            sub.enabled = False
            sub.label(text=prefs.library_path)
        layout.separator()
        layout.operator("plugin_manager.setup_library", icon="LINKED", text="启用/修复挂载")
        layout.operator("plugin_manager.refresh", icon="FILE_REFRESH", text="刷新列表")
```

移除 `show_warnings`、`clear_updates`、`unmount_library` 三个条目（已迁入偏好设置）。

- [ ] **Step 3: 静态检查确认**

Run: `python _test/check_ui.py`
Expected: `===UI_CHECK_OK===`，且源码中不再出现 `PM_PT_tools`。

---

### Task 6: 删除顶部栏模块

**Files:**
- Delete: `bl_plugin_manager/header.py`
- Modify: `bl_plugin_manager/__init__.py`（移除 import、`header.register()`、`header.unregister()`）
- Modify: `_test/check_ui.py`、`_test/audit_features.py`、`_test/test_ui_smoke.py`、`_test/test_e2e.py`、`_test/test_regressions.py`
- Modify: `_mcp_test/` 内有 header 引用的开发脚本

**Interfaces:**
- Produces: `bl_plugin_manager` 不再注册任何 `VIEW3D_HT_header` 回调，也不再有 `plugin_manager.header_popup` 操作符。

- [ ] **Step 1: 删除模块与调用**

删除 `bl_plugin_manager/header.py`；从 `__init__.py` 移除 `header` import、`header.register()`、`header.unregister()` 及回滚分支中的 `header.unregister()`。

- [ ] **Step 2: 更新静态与冒烟检查**

- `_test/check_ui.py`：删除 `header_src = read("header.py")` 及其两处操作符收集/引用检查；新增断言：仓库不存在 `bl_plugin_manager/header.py`、`ui.py` 不定义 `PM_PT_tools`、`ui.py` 引用 `plugin_manager.cancel_compat` 且该 id 已在 `operators.py` 定义、`preferences.py` 引用迁移与维护操作符。
- `_test/audit_features.py`：删除 `hd_src`/`hd_refs` 读取；`must_have` 去掉 `"header_popup"`；其余入口因迁入 `preferences.py` 仍应可触达。
- `_test/test_ui_smoke.py`：把 `PM_PT_tools.draw` 调用替换为「断言 `not hasattr(pm_ui, "PM_PT_tools")`」；偏好设置 draw 用真实 prefs 代理 shim（`__getattr__` 转发到真实 prefs），并断言 draw 过程中出现 `scan_candidates / import_candidates / cleanup_residue / clear_updates / unmount_library` 引用；主面板在 `compat_running=True` 时 draw 通过。
- `_test/test_e2e.py`：新增 `check("header module removed", not hasattr(PM, "header"))`、`check("tools panel removed", not hasattr(PM.ui, "PM_PT_tools"))`、`check("cancel operator exists", hasattr(bpy.ops.plugin_manager, "cancel_compat"))`。
- `_test/test_regressions.py`：新增 `check("no header draw hooked", ...)`，遍历 `bpy.types.VIEW3D_HT_header._dyn_ui_initialize()` 确认不存在 `_draw_header`。

- [ ] **Step 3: 清理开发脚本引用**

从 `_mcp_test/do_unify.py`、`perf_wall.py`、`run_verify.py`、`verify_newui.py`、`verify_maxver.py`、`verify_store.py`、`test_cats.py`、`test_cleanup_cats.py` 的模块重载列表中删除 `"header"` 并移除 `PM.header.register()/unregister()` 调用；删除 `_mcp_test/verify_header.py`、`verify_header2.py`；`_mcp_test/full_button_audit.py` 删除 header 绘制与 `header_popup` 断言，改为对偏好设置 draw 做冒烟并断言 `PM_PT_tools` 不存在。`_mcp_test/diag_draw2.py`、`draw_with_data.py` 中对 `ui.PM_PT_tools` 的包裹改为包裹 `PM_PT_main`。

- [ ] **Step 4: 运行确认**

Run: `python _test/check_ui.py; python _test/audit_features.py`
Expected: 两项均通过，无缺失入口。

---

### Task 7: 全量验证、版本与打包

**Files:**
- Modify: `bl_plugin_manager/constants.py`（`ADDON_VERSION=(2,1,0)`、`ADDON_VERSION_STR="2.1.0"`）
- Modify: `bl_plugin_manager/__init__.py`（`bl_info["version"]=(2,1,0)`）
- Modify: `README.md`（入口结构、取消语义、2.1.0 验证状态）

**Interfaces:**
- Consumes: Task 1–6 全部实现。
- Produces: `dist/bl_plugin_manager-2.1.0.zip`。

- [ ] **Step 1: 单元测试**

Run: `python -m unittest discover -s tests -p "test*.py" -q`
Expected: 既有 40 项（含 1 项按条件跳过）+ `test_compat_task_v2` 新增项全部通过。

- [ ] **Step 2: 隔离 Blender 回归**

Run: `powershell -ExecutionPolicy Bypass -File _test\run_regressions.ps1`
Expected: 全项通过，含模态调度、取消后置生效、顶部栏未注册检查。

- [ ] **Step 3: 端到端**

Run: `powershell -ExecutionPolicy Bypass -File _test\run_e2e.ps1`
Expected: 全项通过（含新增 header/tools/取消操作符检查）。

- [ ] **Step 4: 人工交互核验（Blender MCP 或手动）**

在一份临时库上点击「一键测试插件支持」，确认：侧栏出现 `n / N` 进度、当前插件名与进度条；界面在测试期间可旋转视图、可点「取消」；取消后摘要显示已完成与未完成数；完成/取消/异常三种情况下状态栏被清空、列表已刷新、扫描报告中可见失败项与日志。

- [ ] **Step 5: 版本与打包**

更新版本号后运行：`python build_zip.py`
Expected: 生成 `dist/bl_plugin_manager-2.1.0.zip`，包内 29 个发行文件（新增 `compat_task.py`、删除 `header.py`，与 2.0.1 总数持平），不含 `.pyc`/`__pycache__`/`.blender_ext`。

- [ ] **Step 6: 更新文档**

在 `README.md` 记录：顶部栏入口已移除；侧栏保留兼容性测试与运行时进度；低频维护入口位于「编辑 → 偏好设置 → 插件 → 插件库管理器」；2.1.0 的测试计数与安装包 SHA-256。

---

## 交付门槛

- `python -m unittest discover -s tests -p "test*.py" -q` 全绿（实测 57 项，1 项按条件跳过）。
- `_test/run_regressions.ps1` 全绿（实测 30/30）、`_test/run_e2e.ps1` 全绿（实测 186/186，Blender 5.2.1 LTS）。
- `python _test/check_ui.py`、`python _test/audit_features.py` 通过。
- 隔离回归前后真实 Blender `userpref.blend` 哈希不变（实测一致）。
- `dist/bl_plugin_manager-2.1.0.zip` 生成且不含缓存产物（实测 29 个文件）。

## 实施结果（2026-09-17）

全部 7 个任务已按计划完成，并在本机 Blender 5.2.1 LTS 上隔离验证：

- Python 单元测试 57 项通过（1 项按运行条件跳过），其中 `tests/test_compat_task_v2.py` 17 项覆盖范围筛选、单步推进、取消后置生效、启用态复测的停用/恢复成功与失败、摘要与空目标文案。
- Blender 隔离回归 30/30 通过，新增模态调度、`retest_all` 保留未重测旧结果、顶部栏未注册、工具面板移除等检查。
- Blender 隔离端到端 186/186 通过，新增「完整任务」检查：真实 `_compat_start` → `_compat_tick` → 收尾，8 个目标逐项处理、计时器注销、`load_state` 逐条落盘、目标恢复原未启用状态。
- UI 冒烟无错误；`_test/check_ui.py` 与 `_test/audit_features.py` 通过，无未引用操作符、无缺失入口。
- 回归与端到端前后真实 `userpref.blend` SHA-256 不变（`6889F40F…C5BB0`）。
- 安装包 `dist/bl_plugin_manager-2.1.0.zip`：29 个发行文件，SHA-256 `5324A6B4…B7260`。

实施中的两点修正：

- **范围默认值落地为「继续未完成项」**：原实现的 `retest_all` 会先把本轮全部目标 `load_state` 清零再逐个测试；新实现改为在每个目标被实测时才覆盖其字段，因此中途取消不会销毁尚未重测的旧结果，这与「继续未完成项保留已完成结果」的语义一致。
- **`get_rna_type()` 探测差异**：在自动注册（scripts/addons）环境中 `bpy.ops.*.get_rna_type()` 不可靠，回归测试改用类 `__annotations__` 断言 `scope` 属性；端到端在显式 `addon_utils.enable` 后使用 `get_rna_type()`。

## 风险与取舍

- **计时器间隔**：`0.02s` 在数百插件时会把总时长拉长到数秒量级，但换来可交互与可取消；单插件加载本身仍是同步阻塞，取消无法中断它（符合非目标）。
- **`UILayout.progress`**：若目标 Blender 版本的 `progress` 在面板中不渲染，`_progress_bar` 回退为文本，`n / N` 仍可读；冒烟测试不受影响。
- **启动同步策略**：`sync_startup_on_launch / startup_disable_unmarked` 此前只声明、无入口，代码注释却称「仅当偏好开启时生效」；本计划把入口放入设置面板并补齐一次启动同步，属既有意图的修复，不改变数据边界。
- **取消后残留**：成功分支显式写 `residue=0`，避免上轮失败残留标记在新结果下误报；不改动失败判定标准。
