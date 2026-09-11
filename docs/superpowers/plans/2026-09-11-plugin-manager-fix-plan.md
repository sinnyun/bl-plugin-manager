# Plugin Library Manager Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复插件库管理器已确认的数据安全、官方仓库、同步缓存、兼容性测试和测试运行器问题，并在真实 Blender 中验证全部入口。

**Architecture:** 保持现有模块边界。`library.py` 负责文件事务，`bridge.py` 负责 Blender 挂载与仓库快照，`db.py` 负责隔离缓存，`operators.py` 负责用户可见状态恢复；测试层新增隔离的 Blender 回归脚本，最后通过 Blender MCP 逐项调用操作符。

**Tech Stack:** Python 3.11+、Blender 4.2+ API、Blender MCP、PowerShell、ZIP/TOML/JSON 标准库。

## Global Constraints

- 不删除用户插件；移除失败必须保留源目录和数据库记录。
- 普通挂载不得隐式改写官方商店仓库；统一商店必须是显式、可逆操作。
- 保留用户分类、备注、别名、收藏、自启和启用状态。
- 兼容 Blender 4.2+，实机验证使用 Blender 5.2.1 LTS。
- 每个行为修复先有失败回归测试，再修改生产代码。
- 不执行影响真实插件库的批量删除、覆盖或永久删除操作。

---

### Task 1: 建立回归测试夹具和可靠测试退出

**Files:**
- Create: `_test/test_regressions.py`
- Modify: `_test/test_e2e.py`
- Modify: `_test/run_e2e.sh`
- Create: `_test/run_regressions.ps1`

**Interfaces:**
- Tests import `bl_plugin_manager.db`, `library`, `bridge`, `operators` inside Blender.
- Regression script exits 0 only when every case passes.

- [ ] **Step 1: Write failing tests**
  - Cover failed trash move preserves source/record.
  - Cover same-version metadata refresh.
  - Cover cache isolation.
  - Cover mount-state signature changes.
  - Cover repository target selection.
  - Cover test runner failure propagation.

- [ ] **Step 2: Run tests to verify they fail**
  - Run isolated Blender regression script.
  - Expected failures: trash fallback deletes source, same-version name remains old, cache leaks unsaved value, state cache remains stale.

- [ ] **Step 3: Add explicit exit status**
  - Make `_test/test_e2e.py` call `sys.exit(1)` when summary contains failures or when top-level setup fails.
  - Make shell runner require summary and zero failures in addition to Blender exit code.

- [ ] **Step 4: Verify runner failure behavior**
  - Inject a deliberate failing assertion in a temporary copy and confirm non-zero exit.
  - Restore the test file and run the clean baseline.

---

### Task 2: Make removal and update file operations recoverable

**Files:**
- Modify: `bl_plugin_manager/library.py:98-139, 142-158, 515-538, 594-677`
- Modify: `bl_plugin_manager/operators.py:1029-1051`
- Test: `_test/test_regressions.py`

**Interfaces:**
- `remove_plugin(...) -> dict` returns `ok`, `moved_to`, `error`, and `record_removed`.
- Existing operator reports failures as `CANCELLED` and successes as `FINISHED`.

- [ ] **Step 1: Assert failed move preserves data**
- [ ] **Step 2: Implement safe removal**
  - Never call `rmtree` as a fallback after failed trash move.
  - Delete the DB record only after a verified successful move.
  - Keep a clear failure result.
- [ ] **Step 3: Add transactional update staging**
  - Extract and validate a new package in a sibling temporary directory.
  - Backup first; abort if backup creation fails.
  - Swap directories only after the new content is complete.
  - Restore the old directory if swap fails.
- [ ] **Step 4: Verify removal and update failure cases**

---

### Task 3: Separate ordinary library mounting from official-store unification

**Files:**
- Modify: `bl_plugin_manager/bridge.py:64-142, 187-255`
- Modify: `bl_plugin_manager/operators.py:61-171`
- Modify: `bl_plugin_manager/db.py`
- Test: `_test/test_regressions.py`

**Interfaces:**
- Add repository snapshot helpers: `capture_official_repo_state()` and `restore_official_repo_state(state)`.
- `register_library()` only manages the plugin-owned repository.
- `unify_store_with_library()` explicitly changes official store state and persists a snapshot.
- `unregister_library()` restores official state or removes only plugin-owned repository.

- [ ] **Step 1: Assert ordinary setup leaves official repo unchanged**
- [ ] **Step 2: Assert unmount never deletes user/official repo objects**
- [ ] **Step 3: Implement repository ownership and snapshot model**
- [ ] **Step 4: Update UI messages and operator reports**
- [ ] **Step 5: Verify mount/unmount/unify round-trip three times**

---

### Task 4: Fix synchronization and cache invalidation

**Files:**
- Modify: `bl_plugin_manager/library.py:430-509`
- Modify: `bl_plugin_manager/bridge.py:145-180`
- Modify: `bl_plugin_manager/db.py:35-60, 107-123`
- Test: `_test/test_regressions.py`

**Interfaces:**
- Add `metadata_fingerprint(plugin_dir, kind) -> tuple` in `library.py` or `scan.py`.
- DB cache stores deep snapshots, never live mutable references.
- Mount-state cache signature includes normalized paths, modules and enabled flags.

- [ ] **Step 1: Assert same-version metadata refresh**
- [ ] **Step 2: Assert DB cache does not expose unsaved mutations**
- [ ] **Step 3: Assert path-only mount changes invalidate state**
- [ ] **Step 4: Implement fingerprints, deep-copy cache and full state signature**
- [ ] **Step 5: Verify unchanged fast path and changed-file refresh**

---

### Task 5: Make compatibility verification truthful and state-safe

**Files:**
- Modify: `bl_plugin_manager/operators.py:353-456`
- Modify: `bl_plugin_manager/bridge.py:517-552`
- Test: `_test/test_regressions.py`

**Interfaces:**
- Compatibility test records `load_state`, `load_error`, `restore_state`, and `restore_error`.
- Successful enabled-plugin retest performs an actual disable/enable cycle only when requested.

- [ ] **Step 1: Add tests for enabled retest and disabled restoration**
- [ ] **Step 2: Implement explicit retest mode and result checks**
- [ ] **Step 3: Preserve prior results for plugins not tested this round**
- [ ] **Step 4: Verify real Blender state equals DB state after each case**

---

### Task 6: Correct diagnostics, documentation and packaging

**Files:**
- Modify: `_mcp_test/final_check.py`
- Modify: `README.md`
- Modify: `build_zip.py`
- Test: `_test/check_ui.py`, `_test/audit_features.py`

- [ ] **Step 1: Add failing check for `.blender_ext` false positive**
- [ ] **Step 2: Fix diagnostic filtering**
- [ ] **Step 3: Make README describe manual inbox scanning accurately**
- [ ] **Step 4: Bump addon version and build installable ZIP**
- [ ] **Step 5: Verify ZIP contains only the plugin package and updated metadata**

---

### Task 7: Blender MCP full button and feature verification

**Files:**
- Create: `_mcp_test/full_button_audit.py`
- Modify: `_mcp_test/smoke_operators.py` if needed for safe, reversible coverage

- [ ] **Step 1: Add a reversible test library and fixture plugins**
- [ ] **Step 2: Invoke every registered operator at least once**
  - Use safe values for dialogs and restore all preference/database state afterward.
  - Test import, scan, report, categories, metadata, enable/disable, batch, startup, compatibility, store cache, update and mount actions.
- [ ] **Step 3: Draw every panel/menu/header popup through Blender UI**
- [ ] **Step 4: Run the audit in live Blender via MCP**
- [ ] **Step 5: Verify no real library files, enabled plugins or user preferences were changed**

---

### Task 8: Final verification and delivery

**Files:**
- Verify all modified files and generated ZIP.

- [ ] **Step 1: Run Python compile and static UI checks**
- [ ] **Step 2: Run isolated regression and end-to-end suites**
- [ ] **Step 3: Run Blender MCP full audit and capture results**
- [ ] **Step 4: Inspect package contents and version**
- [ ] **Step 5: Report exact test counts, remaining limitations and output paths**

