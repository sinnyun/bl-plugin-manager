"""兼容性测试任务的纯逻辑：目标筛选、单步执行、摘要文案。

本模块刻意不 import bpy 与 constants，所有 Blender 副作用都通过注入的
``api`` 调用完成，因此可以在普通 Python 进程里完整测试。调度（计时器、
取消、界面进度）由 operators.py 负责，这里只回答两个问题：
* 这次要测哪些插件；
* 测其中一个插件时发生了什么。
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCOPE_CONTINUE = "continue"
SCOPE_RETEST_ALL = "retest_all"

# 供 bpy.props.EnumProperty 直接使用的 (标识, 名称, 说明) 三元组
SCOPE_ITEMS = (
    (SCOPE_CONTINUE, "继续未完成项",
     "只测试还没有本轮实测结果的插件；已完成的结果保留"),
    (SCOPE_RETEST_ALL, "全部重新测试",
     "重新测试全部可测插件，覆盖旧的本轮实测结果"),
)

# 有这些 load_state 的目标视为「本轮已测」
MEASURED = ("ok", "failed")


@dataclass(frozen=True)
class Target:
    key: str
    module: str
    name: str
    was_enabled: bool


@dataclass
class Selection:
    targets: list[Target] = field(default_factory=list)
    skipped_tested: int = 0
    skipped_enabled: int = 0
    skipped_theme: int = 0
    skipped_missing: int = 0
    skipped_unbound: int = 0

    @property
    def skipped_total(self) -> int:
        return (self.skipped_tested + self.skipped_enabled + self.skipped_theme
                + self.skipped_missing + self.skipped_unbound)


def select_targets(records, enabled_modules, include_enabled: bool = False,
                   scope: str = SCOPE_CONTINUE) -> Selection:
    """建立稳定的测试目标列表。

    ``records`` 是 ``(key, rec)`` 的可迭代对象。筛选规则与旧的一次性实现
    逐一对应：跳过无模块名、已缺失、主题；已启用插件仅在显式要求时纳入；
    ``continue`` 范围跳过本轮已有结果的项目。
    """
    sel = Selection()
    retest_all = scope == SCOPE_RETEST_ALL
    for key, rec in records:
        module = (rec.get("module") or "").strip()
        if not module:
            sel.skipped_unbound += 1
            continue
        if rec.get("missing"):
            sel.skipped_missing += 1
            continue
        # 主题(theme)不是插件，无法用 addon_enable 启用，跳过
        if (rec.get("pkg_type") or rec.get("type") or "").strip() == "theme":
            sel.skipped_theme += 1
            continue
        was_enabled = module in enabled_modules
        if was_enabled and not include_enabled:
            sel.skipped_enabled += 1
            continue
        if not retest_all and (rec.get("load_state") or "") in MEASURED:
            sel.skipped_tested += 1
            continue
        name = (rec.get("display_name") or rec.get("name") or key or module)
        sel.targets.append(Target(key=key, module=module, name=name,
                                  was_enabled=was_enabled))
    return sel


@dataclass
class TaskState:
    """一次测试任务的进度与结果。``index`` 表示已处理的目标数。"""

    targets: list[Target]
    include_enabled: bool = False
    scope: str = SCOPE_CONTINUE
    index: int = 0
    ok: int = 0
    fail: int = 0
    fails: list[dict] = field(default_factory=list)
    cancelled: bool = False

    @property
    def total(self) -> int:
        return len(self.targets)

    def pending(self) -> bool:
        return self.index < self.total

    @property
    def current_name(self) -> str:
        if 0 <= self.index < self.total:
            return self.targets[self.index].name
        return ""

    def request_cancel(self) -> None:
        """请求取消：当前目标处理完后停止，不中断正在进行的加载/卸载。"""
        self.cancelled = True


@dataclass
class StepOutcome:
    """单个目标的处理结果。``fields`` 需合并回该插件的记录。"""

    key: str
    name: str
    fields: dict
    ok: bool = False
    failed: bool = False
    fail: dict | None = None
    done: bool = False


def step(state: TaskState, api) -> StepOutcome | None:
    """处理一个目标并推进进度；无待办（已完成或已取消）时返回 None。

    ``api`` 需提供 ``set_enabled(module, enabled) -> (ok, err)``、
    ``is_module_enabled(module) -> bool``、``module_residue(module) -> int``。
    判定标准沿用原有真实加载/恢复/残留逻辑，只改变调用时机。
    """
    if state.cancelled or not state.pending():
        return None

    target = state.targets[state.index]
    fields: dict = {"restore_error": ""}
    passed = False
    fail: dict | None = None
    blocked = False

    # 已启用插件只有在用户明确选择复测时才做真实停用。
    if target.was_enabled and state.include_enabled:
        off_ok, off_err = api.set_enabled(target.module, False)
        if not off_ok or api.is_module_enabled(target.module):
            fields["load_state"] = "failed"
            fields["load_error"] = "无法开始复测：停用失败"
            fields["restore_error"] = off_err or fields["load_error"]
            fields["enabled"] = api.is_module_enabled(target.module)
            fail = {"name": target.name, "key": target.key,
                    "error": fields["load_error"], "residue": 0}
            blocked = True

    if not blocked:
        success, err = api.set_enabled(target.module, True)
        if success:
            restore_ok, restore_err = True, ""
            if not target.was_enabled:
                restore_ok, restore_err = api.set_enabled(target.module, False)
                restore_ok = restore_ok and not api.is_module_enabled(target.module)
            elif state.include_enabled:
                # 已启用插件已在上面停用，现在需确认重新启用成功。
                restore_ok = api.is_module_enabled(target.module)
            if restore_ok:
                fields["load_state"] = "ok"
                fields["load_error"] = ""
                fields["enabled"] = target.was_enabled
                # 清除上一轮失败留下的残留标记，避免新结果下误报。
                fields["residue"] = 0
                passed = True
            else:
                fields["load_state"] = "failed"
                fields["load_error"] = "加载成功但无法恢复原启用状态"
                fields["restore_error"] = restore_err or fields["load_error"]
                fields["enabled"] = api.is_module_enabled(target.module)
                fail = {"name": target.name, "key": target.key,
                        "error": fields["restore_error"], "residue": 0}
        else:
            left = api.module_residue(target.module)
            fields["residue"] = left
            fields["load_state"] = "failed"
            fields["load_error"] = err or "未知错误"
            if err:
                # 仅在拿到新错误时覆盖，等价于旧的 "err or 保留原值"。
                fields["last_error"] = err
            fields["enabled"] = api.is_module_enabled(target.module)
            fail = {"name": target.name, "key": target.key, "error": err,
                    "residue": left}

    state.index += 1
    if passed:
        state.ok += 1
    else:
        state.fail += 1
        if fail is not None:
            state.fails.append(fail)
    return StepOutcome(key=target.key, name=target.name, fields=fields,
                       ok=passed, failed=not passed, fail=fail,
                       done=not state.pending())


def summary_text(state: TaskState, cancelled: bool = False) -> str:
    """任务结束后的摘要；取消明确区分已完成与未完成，不报告为错误。"""
    if cancelled:
        remaining = max(0, state.total - state.index)
        return (f"兼容性测试已取消：✓ 支持 {state.ok} 个，✗ 不支持 {state.fail} 个；"
                f"已完成 {state.index}/{state.total}，未完成 {remaining} 个")
    return (f"一键测试完成：✓ 支持 {state.ok} 个，"
            f"✗ 不支持 {state.fail} 个（共 {state.total} 个）")


def no_targets_text(selection: Selection, scope: str = SCOPE_CONTINUE) -> str:
    """没有任何可测目标时的说明，尽量指出下一步该改哪个选项。"""
    if selection.skipped_tested:
        return (f"没有需要测试的项目：{selection.skipped_tested} 个已有本轮实测结果，"
                "如需重测请选择「全部重新测试」")
    if selection.skipped_enabled and not (selection.skipped_theme or selection.skipped_missing):
        return ("没有需要测试的项目：其余插件都已启用，"
                "如需复测请勾选「也测试已启用的插件」")
    if selection.skipped_total:
        return (f"没有可测试的插件（已跳过 {selection.skipped_total} 项："
                "已测 / 已启用 / 主题 / 缺失记录）")
    return "没有可测试的插件"
