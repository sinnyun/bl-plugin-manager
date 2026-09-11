# 插件列表兼容性信息 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在插件列表每一行隐藏插件自身版本号，并显示当前 Blender 兼容性与插件最高支持版本。

**Architecture:** 复用现有 `items.rebuild_items()` 计算出的 `compat`、`compat_detail` 和 `max_version_text`，只调整 `PM_UL_plugins.draw_item()` 的右侧布局；通过一个小的纯函数生成显示文本，保证 UI 渲染和测试都能覆盖边界状态。

**Tech Stack:** Blender Python API、`bpy.types.UIList`、现有 `scan.py` 版本兼容判断、PowerShell/Python 测试脚本、Blender MCP。

## Global Constraints

- 不新增持久化字段，不改变数据库格式。
- 不改变插件自身版本、更新检测、过滤器和详情面板的现有语义。
- 兼容性判断必须继续实时基于当前 `bpy.app.version`。
- 未声明最高版本显示“最高 不限”；声明上限显示“最高 ≤ x.y.z”。
- 兼容性状态使用现有 `yes/no/unknown` 与 `ok/too_old/too_new/unknown` 结论。

---

### Task 1: 建立列表状态文本的失败测试

**Files:**
- Modify: `bl_plugin_manager/ui.py`
- Test: `_test/test_regressions.py`

**Interfaces:**
- Produces: `ui._compat_display(item) -> tuple[str, str, bool]`，返回 `(compat_text, max_text, alert)`。

- [ ] **Step 1: 写失败测试**

在 `_test/test_regressions.py` 增加纯数据测试，构造最小对象并断言：兼容、超上限、未知、加载失败四种状态分别返回预期文字和警示标记。

```python
def test_list_compatibility_display_states():
    from types import SimpleNamespace
    from bl_plugin_manager import ui

    ok = SimpleNamespace(compat="ok", supported="yes", compat_detail="支持 4.2.0 ~ 5.2.0", max_version_text="≤ 5.2.0")
    assert ui._compat_display(ok) == ("✓ 当前 Blender 兼容", "最高 ≤ 5.2.0", False)

    too_new = SimpleNamespace(compat="too_new", supported="no", compat_detail="最高仅支持 5.0.0", max_version_text="≤ 5.0.0")
    assert ui._compat_display(too_new) == ("✗ 当前 Blender 不兼容", "最高 ≤ 5.0.0", True)

    unknown = SimpleNamespace(compat="unknown", supported="unknown", compat_detail="未声明支持的 Blender 版本", max_version_text="不限")
    assert ui._compat_display(unknown) == ("? 兼容性未知", "最高 不限", False)

    failed = SimpleNamespace(compat="ok", supported="no", compat_detail="支持 4.2.0 ~ 5.2.0", max_version_text="≤ 5.2.0")
    assert ui._compat_display(failed) == ("✗ 当前 Blender 不兼容", "最高 ≤ 5.2.0", True)
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run: `python _test/test_regressions.py`

Expected: FAIL，原因是 `bl_plugin_manager.ui` 尚未提供 `_compat_display`。

- [ ] **Step 3: 实现最小纯函数**

在 `ui.py` 列表区前加入：

```python
def _compat_display(item):
    alert = item.supported == "no" or item.compat in ("too_old", "too_new")
    if alert:
        label = "✗ 当前 Blender 不兼容"
    elif item.supported == "yes" or item.compat == "ok":
        label = "✓ 当前 Blender 兼容"
    else:
        label = "? 兼容性未知"
    maximum = item.max_version_text or "不限"
    if maximum == "不限":
        maximum = "最高 不限"
    elif not maximum.startswith("最高"):
        maximum = f"最高 {maximum}"
    return label, maximum, alert
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python _test/test_regressions.py`

Expected: 新增四个断言通过，既有回归保持通过。

### Task 2: 调整 UIList 右侧布局

**Files:**
- Modify: `bl_plugin_manager/ui.py:PM_UL_plugins.draw_item`
- Test: `_test/check_ui.py`

**Interfaces:**
- Consumes: Task 1 的 `_compat_display(item)`。
- Produces: 列表行右侧纵向兼容性列，不再显示插件自身版本号。

- [ ] **Step 1: 写 UI 静态断言**

在 `_test/check_ui.py` 增加源码检查：`draw_item` 包含 `当前 Blender 兼容`、`最高`，且不在列表行区域使用 `item.version`。

- [ ] **Step 2: 运行静态检查确认失败**

Run: `python _test/check_ui.py`

Expected: FAIL，当前源码尚未包含新的兼容性文案。

- [ ] **Step 3: 实现列表布局**

保留左侧启用/选择/状态图标和名称，替换原有右侧 `✓/✗/?` 单符号区：

```python
        compat_label, max_label, compat_alert = _compat_display(item)
        sub = row.column(align=True)
        sub.alignment = "RIGHT"
        sub.alert = compat_alert
        sub.label(text=compat_label)
        max_row = sub.row(align=True)
        max_row.alignment = "RIGHT"
        max_row.enabled = False
        max_row.label(text=max_label)
```

- [ ] **Step 4: 运行 UI 检查确认通过**

Run: `python _test/check_ui.py`

Expected: `UI_CHECK_OK`，操作符引用和入口数量无回归。

### Task 3: MCP 实际绘制与完整回归

**Files:**
- Modify: `_mcp_test/full_button_audit.py` only if its mock layout needs to support the new `column()`/`label()` calls.

**Interfaces:**
- Consumes: Task 2 的 UIList 绘制实现。
- Produces: Blender MCP 中真实重建列表并绘制兼容、超上限、未知三种状态的证据。

- [ ] **Step 1: 扩展 MCP UI smoke 数据**

在临时库中加入声明 `blender_max=5.0.0` 和无版本声明的夹具，调用 `items.rebuild_items()` 后分别执行 `PM_UL_plugins.draw_item()`；Mock Layout 记录文本。

- [ ] **Step 2: 运行 MCP 审计确认状态文本**

Run: 通过 `mcp__blender__execute_blender_code` 执行 `_mcp_test/full_button_audit.py`。

Expected: 兼容性列出现“当前 Blender 兼容/不兼容/兼容性未知”和“最高 ≤ …/最高 不限”，入口失败数为 0。

- [ ] **Step 3: 运行完整本地验证**

Run: `python -m compileall -q bl_plugin_manager; python _test/check_ui.py; powershell -ExecutionPolicy Bypass -File _test\run_regressions.ps1; powershell -ExecutionPolicy Bypass -File _test\run_e2e.ps1`

Expected: 编译通过、UI 检查通过、回归 11/11、E2E 161/161。

- [ ] **Step 4: 重新打包插件**

Run: `python build_zip.py`

Expected: 更新 `dist/bl_plugin_manager-1.0.1.zip`，包内无 `.pyc` 与 `.blender_ext`。
