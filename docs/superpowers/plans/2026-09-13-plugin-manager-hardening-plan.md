# Plugin Manager Hardening and Multi-Computer Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Blender plugin library manager data-safe, multi-computer aware, recoverable, and responsive while preserving one synchronized set of aliases, categories, notes, favorites, and startup choices.

**Architecture:** Deliver a focused 1.0.4 safety release first, then a 1.1.0 state-separation and maintainability release. Shared portable metadata stays in `<library>/.pm/library.json`; active paths, mount snapshots, environment-specific module state, and compatibility results move to `%LOCALAPPDATA%/BlenderPluginManager`. All filesystem mutations pass containment checks and all shared writes use conflict-aware transactions.

**Tech Stack:** Blender 4.2+/5.2 Python API, Python 3.11+, UTF-8 JSON, PowerShell, Blender background tests, ZIP packages.

## Global Constraints

- Never modify the real synchronized library until a timestamped metadata backup and mount snapshot exist.
- Never automatically restore historical categories; generate a diff and require user confirmation.
- Shared aliases, categories, notes, tags, favorites, and startup choices remain portable across computers.
- Machine paths, Blender repository paths, enabled state, errors, residue, and compatibility-test results never act as shared global truth.
- Every bug fix follows RED → GREEN with an isolated Blender test.
- Tests and fixtures required for repeatable verification must be tracked by Git.
- Every resolved record path must remain inside `<root>/addons` or `<root>/extensions` before any write, move, update, open, or delete.
- A corrupt or concurrently changed shared database is read-only until explicitly reloaded or restored.
- Version 1.0.4 contains P0 safety/multi-computer fixes; version 1.1.0 contains schema and module-boundary changes.

---

## Release 1.0.4 — data safety and multi-computer hotfix

### Task 1: Track the verification suite and snapshot the real environment

**Files:**
- Modify: `.gitignore`
- Create: `tests/blender/test_regressions.py`
- Create: `tests/blender/test_e2e.py`
- Create: `tests/blender/run_regressions.ps1`
- Create: `tests/blender/run_e2e.ps1`
- Create: `tests/blender/test_package.py`

**Interfaces:**
- Test runners accept `-BlenderExe` or `BLENDER_EXE` and always use temporary `BLENDER_USER_CONFIG`, `BLENDER_USER_SCRIPTS`, `TEMP`, and `TMP`.
- Test scripts exit non-zero when any check fails.

- [ ] Copy the reusable `_test` tests into `tests/blender`, update paths, and keep only disposable `_mcp_test`, `_migrate`, and generated output ignored.
- [ ] Add a deliberately failing check and run the runner to prove exit code is non-zero; then remove the deliberate failure.
- [ ] Run both unchanged suites and record the expected baseline: 16 regression checks and 161 E2E checks.
- [ ] Add a read-only environment snapshot helper that records Blender version, add-on version, owned script directories, repositories, existence flags, and shared database hash without exposing full plugin records.
- [ ] Commit with `test: track isolated Blender verification suite`.

### Task 2: Block record-path escape before any filesystem mutation

**Files:**
- Create: `bl_plugin_manager/paths.py`
- Modify: `bl_plugin_manager/library.py`
- Modify: `bl_plugin_manager/operators.py`
- Test: `tests/blender/test_regressions.py`

**Interfaces:**
- `resolve_record_path(root: str, rel: str, kind: str, *, must_exist: bool = False) -> str`
- Raises `UnsafeLibraryPathError` when the normalized real path is outside the expected container or the first relative component disagrees with `kind`.

- [ ] Add failing tests for `../outside`, absolute paths, drive-qualified paths, mixed separators, symlink/junction escape where supported, and valid Unicode paths.
- [ ] Reproduce the current controlled failure: `remove_plugin(..., to_trash=False)` deletes a temporary directory outside the temporary library.
- [ ] Implement `realpath/commonpath` containment and use it in remove, replace/update, open-folder, backup, and any record-derived path.
- [ ] Verify all escape cases fail before the filesystem changes and valid import/remove/update tests remain green.
- [ ] Commit with `fix: contain all plugin record paths`.

### Task 3: Make corrupt metadata fail closed

**Files:**
- Modify: `bl_plugin_manager/db.py`
- Modify: `bl_plugin_manager/operators.py`
- Modify: `bl_plugin_manager/ui.py`
- Test: `tests/blender/test_regressions.py`

**Interfaces:**
- `DatabaseStatus = OK | MISSING | CORRUPT | UNSUPPORTED`
- `LibraryDB.status`, `LibraryDB.error`, `LibraryDB.can_save`
- `CorruptDatabaseError` and `UnsupportedSchemaError`

- [ ] Add a failing test showing malformed JSON is backed up and then silently replaced by a scan.
- [ ] Add tests for wrong top-level types, missing `plugins`, unsupported future schema, and recovery from a valid backup.
- [ ] Make corrupt/unsupported databases read-only; `save()` and `sync_library()` must raise a user-facing error without replacing the source.
- [ ] Add UI states with “查看备份” and an explicit recovery operator; do not auto-select a backup.
- [ ] Verify a missing database can still initialize a genuinely new empty library, while a corrupt existing database cannot.
- [ ] Commit with `fix: protect corrupt shared metadata`.

### Task 4: Store the active library path per machine

**Files:**
- Create: `bl_plugin_manager/machine_config.py`
- Modify: `bl_plugin_manager/preferences.py`
- Modify: `bl_plugin_manager/__init__.py`
- Test: `tests/blender/test_regressions.py`

**Interfaces:**
- `config_path() -> str`
- `load_machine_config() -> dict`
- `save_machine_config(data: dict) -> None`
- `resolve_active_library(legacy_path: str) -> str`
- Environment override for tests: `BL_PLUGIN_MANAGER_MACHINE_CONFIG`

- [ ] Write failing tests for Unicode/UNC round-trip, local-over-legacy precedence, valid legacy migration, invalid legacy rejection, corrupt local JSON, and exact allowed keys.
- [ ] Implement atomic `%LOCALAPPDATA%/BlenderPluginManager/machine.json` storage with platform-local fallbacks.
- [ ] Mark `library_path` as `SKIP_SAVE`, use an empty default, and add a guarded runtime setter that does not recursively trigger activation.
- [ ] At registration, load local configuration before mounting; preserve a configured but temporarily unavailable path as `OFFLINE`, and never create it.
- [ ] Verify simulated computer A and B keep different absolute paths while reading copied shared metadata.
- [ ] Commit with `feat: isolate active library path per machine`.

### Task 5: Reconcile stale script directories and repositories

**Files:**
- Create: `bl_plugin_manager/mounts.py`
- Modify: `bl_plugin_manager/bridge.py`
- Modify: `bl_plugin_manager/__init__.py`
- Modify: `bl_plugin_manager/operators.py`
- Test: `tests/blender/test_regressions.py`

**Interfaces:**
- `MountReport(current, removed_stale, warnings, ready)`
- `reconcile_mounts(root: str, *, save: bool) -> MountReport`
- `inspect_mounts(root: str) -> MountReport`

- [ ] Add a failing fixture matching the live state: valid `D:` owned script path, invalid `X:` owned script path, official repo on D, `pmlib` on X.
- [ ] Assert current `library_state()` incorrectly reports the nonexistent X path ready.
- [ ] Implement ownership detection using the manager name prefix for script directories and exact `pmlib` module for the private repository.
- [ ] Retarget or remove only stale owned items, preserve official/system/user repositories, and require target directories to exist for `ready=True`.
- [ ] Move `official_repo_backup.json` into machine-local state keyed by Blender version; migrate a matching local snapshot only when safe.
- [ ] Make `save_prefs()` return `(ok, error)` and propagate failure instead of swallowing it.
- [ ] Verify reconcile is idempotent across three runs and produces exactly one current owned script entry/repository.
- [ ] Commit with `fix: reconcile machine-specific Blender mounts`.

### Task 6: Activate and switch libraries in one ordered transaction

**Files:**
- Create: `bl_plugin_manager/activation.py`
- Modify: `bl_plugin_manager/preferences.py`
- Modify: `bl_plugin_manager/operators.py`
- Modify: `bl_plugin_manager/__init__.py`
- Test: `tests/blender/test_e2e.py`

**Interfaces:**
- `activate_library(path: str, *, persist: bool, initialize_empty: bool = False) -> ActivationReport`
- Ordered stages: validate → uncached DB load → persist local path → reconcile mounts → refresh modules → scan once → rebuild UI.

- [ ] Add a failing cross-path test that copies a complete library with alias/category/note/favorite/startup values, primes a stale cache, switches paths, and asserts all values remain.
- [ ] Instrument `sync_library` and assert explicit activation scans/saves once.
- [ ] Add a failing startup test proving extensions receive `bl_ext.pmlib.*` only after the pmlib repository exists.
- [ ] Implement the activation service and make both setup and path-picker operators call it.
- [ ] Reduce the property callback to local persistence/direct-edit dispatch; suppress it during controlled activation.
- [ ] Verify unavailable paths return `OFFLINE`, corrupt DB returns `METADATA_CORRUPT`, partial mounts return `MOUNT_DEGRADED`, and only complete activation returns `READY`.
- [ ] Commit with `fix: make library activation ordered and single-pass`.

### Task 7: Detect synchronized replacements and concurrent saves

**Files:**
- Modify: `bl_plugin_manager/db.py`
- Test: `tests/blender/test_regressions.py`

**Interfaces:**
- `_file_signature(path) -> tuple[dev, ino, mtime_ns, ctime_ns, size] | None`
- `DatabaseConflictError`
- `LibraryDB.loaded_signature`
- `LibraryDB.save(expected_signature=...)`

- [ ] Add a failing same-size/same-mtime atomic-replacement cache test.
- [ ] Add a failing lost-update test: load A, externally save B, then save A; current code overwrites B.
- [ ] Strengthen cache identity and make explicit activation bypass cache.
- [ ] Compare the loaded signature immediately before save; on mismatch, refuse to overwrite and tell the operator to reload/retry.
- [ ] Keep same-directory temporary write, flush, `os.fsync`, atomic replace, and rolling `library.json.bak.1..3` backups.
- [ ] Verify sequential edits pass and concurrent edits fail closed without losing either source file.
- [ ] Commit with `fix: detect synced metadata write conflicts`.

### Task 8: Make add-on registration transactional

**Files:**
- Modify: `bl_plugin_manager/__init__.py`
- Modify: `bl_plugin_manager/header.py`
- Test: `tests/blender/test_regressions.py`

**Interfaces:**
- Internal `_register_classes_transactionally(modules) -> None`
- Correct pre-import hot-reload sentinel.

- [ ] Add tests for enable → disable → enable, injected class-registration failure, and header registration failure.
- [ ] Prove the current injected failure leaves registered classes behind.
- [ ] Fix the reload sentinel so first import does not reload every submodule.
- [ ] On registration failure, unregister only classes and callbacks registered in that attempt, in reverse order.
- [ ] Make unregister idempotent and clear timers/callbacks even after partial registration.
- [ ] Commit with `fix: make manager registration recoverable`.

### Task 9: Release and install 1.0.4 safely

**Files:**
- Modify: `bl_plugin_manager/constants.py`
- Modify: `bl_plugin_manager/__init__.py`
- Modify: `README.md`
- Modify: `install.ps1`
- Modify: `build_zip.py`
- Test: `tests/blender/test_package.py`

- [ ] Add failing version consistency, package-content, and installer rollback tests.
- [ ] Bump all version sources to 1.0.4 and document per-machine path/shared metadata behavior and offline states.
- [ ] Change installer to stage, compile-check, swap, retain `.previous`, and report Blender restart/re-enable requirement.
- [ ] Build `dist/bl_plugin_manager-1.0.4.zip` and verify it excludes caches/tests/local state.
- [ ] Back up the current real DB, current `userpref.blend`, and mount snapshot before installation.
- [ ] Install with Blender add-on disabled or Blender closed, then start/re-enable and run live checks.
- [ ] Confirm the inferred valid path `D:\nastongbu\qitaziliao\blender_addons` with the user before writing it to machine configuration.
- [ ] Run compile, regression, E2E, package, three-cycle reload, and live mount verification.
- [ ] Commit with `release: harden multi-computer library safety`.

---

## Release 1.1.0 — state separation, responsiveness, and maintainability

### Task 10: Introduce shared schema 2 and local runtime state

**Files:**
- Create: `bl_plugin_manager/storage/shared_db.py`
- Create: `bl_plugin_manager/storage/local_state.py`
- Create: `bl_plugin_manager/storage/migrations.py`
- Modify: `bl_plugin_manager/db.py` as a compatibility facade
- Modify: `bl_plugin_manager/items.py`
- Modify: `bl_plugin_manager/library.py`
- Test: `tests/blender/test_storage.py`

**Interfaces:**
- Shared root: `{schema: 2, library_id, revision, categories, plugins}`
- Local runtime key: `<library_id>/<platform>/<blender-version>/<python-version>`
- `SharedPluginRecord` contains portable identity plus user metadata.
- `RuntimePluginRecord` contains module, enabled, missing, load/error/residue and environment fingerprint.

- [ ] Add migration tests using copies of the current 263355-byte schema-1 database and historical backups.
- [ ] Define exact allowlists for shared and runtime fields; assert no drive-qualified value enters shared runtime fields.
- [ ] Generate a stable `library_id` once and preserve it when the library moves.
- [ ] Move environment fields into local state while keeping aliases/categories/notes/favorites/startup unchanged.
- [ ] Keep a pre-migration backup and make migration idempotent.
- [ ] Verify computers A/B share user metadata but retain independent enabled/errors/compatibility results.
- [ ] Commit with `feat: separate shared metadata from local runtime state`.

### Task 11: Replace mutable snapshots with field-level transactions

**Files:**
- Modify: `bl_plugin_manager/storage/shared_db.py`
- Modify: metadata operators in `bl_plugin_manager/operators.py`
- Test: `tests/blender/test_storage.py`

**Interfaces:**
- `update_plugin_fields(key: str, changes: dict, expected_revision: int) -> int`
- `update_categories(mutator, expected_revision: int) -> int`
- `read_snapshot() -> immutable/deep-copied snapshot`

- [ ] Add conflict tests for simultaneous alias/category edits and sync replacement between read/save.
- [ ] Reject fields outside the shared allowlist.
- [ ] Reload, apply only requested fields, increment revision, and atomically save after signature comparison.
- [ ] Convert every metadata operator to the transaction API; no operator may save a long-lived full mutable snapshot.
- [ ] Display a clear “数据库已被另一台电脑更新，请刷新后重试” message on conflict.
- [ ] Commit with `refactor: use conflict-aware metadata transactions`.

### Task 12: Make compatibility testing environment-scoped and safer

**Files:**
- Create: `bl_plugin_manager/compatibility.py`
- Modify: `bl_plugin_manager/bridge.py`
- Modify: compatibility operators in `bl_plugin_manager/operators.py`
- Modify: `bl_plugin_manager/ui.py`
- Test: `tests/blender/test_compatibility.py`

**Interfaces:**
- `environment_fingerprint() -> {os, blender, python, repository_module}`
- `CompatibilityResult` stored only in local runtime state.
- Type cleanup uses before/after snapshots, not broad module-prefix deletion.

- [ ] Add tests proving Blender 4.x/5.x and Python 3.11/3.13 results remain separate.
- [ ] Capture exact Blender types, handlers, enabled modules, and preference state before each test.
- [ ] Default to an isolated background Blender process; retain in-session testing only behind an explicit warning.
- [ ] Remove `UNDO` from operators whose effects include files, preferences, network, or third-party registration code.
- [ ] On uncertain residue, report restart required instead of unregistering unrelated classes.
- [ ] Commit with `feat: isolate compatibility results by environment`.

### Task 13: Harden archive, download, import, update, and removal workflows

**Files:**
- Create: `bl_plugin_manager/security/archive.py`
- Create: `bl_plugin_manager/services/plugin_files.py`
- Modify: `bl_plugin_manager/library.py`
- Modify: `bl_plugin_manager/store.py`
- Test: `tests/blender/test_plugin_files.py`

**Interfaces:**
- `inspect_archive(path, limits) -> ArchivePlan`
- Limits cover member count, total expanded bytes, per-file bytes, compression ratio, path length, and allowed plugin root.
- `PluginFileTransaction` stages, validates, swaps, rolls back, and reports cleanup leftovers.

- [ ] Add malicious/oversized archive tests, Windows reserved-name tests, interrupted swap tests, backup failure tests, and re-enable failure tests.
- [ ] Replace `tempfile.mktemp` with securely opened temporary files.
- [ ] Extract only validated members into an isolated staging directory.
- [ ] Unify import/update/removal around one transaction abstraction with explicit pre/post state.
- [ ] Enforce download byte limits while streaming, require HTTPS for remote catalogs, and require/verify hashes when supplied.
- [ ] Ensure failed cleanup directories are hidden from scans and reported for later cleanup.
- [ ] Commit with `fix: harden plugin file transactions and archives`.

### Task 14: Move long work out of blocking operator execution

**Files:**
- Create: `bl_plugin_manager/tasks.py`
- Modify: store/update/scan operators
- Modify: `bl_plugin_manager/preferences.py`
- Modify: `bl_plugin_manager/ui.py`
- Test: `tests/blender/test_tasks.py`

**Interfaces:**
- `TaskController.start(io_job, main_thread_steps)`
- Observable state: `idle/running/cancelling/succeeded/failed`, progress, current item, error.

- [ ] Add tests with a delayed fake download showing Blender timers continue and cancel reaches a safe boundary.
- [ ] Run network and pure file IO off the Blender main thread; never call `bpy` from worker threads.
- [ ] Use modal/timer callbacks for progress and main-thread Blender operations.
- [ ] Disable conflicting buttons while a transaction is active and provide cancel where safe.
- [ ] Commit with `feat: make long plugin operations responsive`.

### Task 15: Make UI refresh revision-driven

**Files:**
- Modify: `bl_plugin_manager/items.py`
- Modify: `bl_plugin_manager/ui.py`
- Test: `tests/blender/test_ui_performance.py`

**Interfaces:**
- UI cache key: `(library_id, shared_revision, runtime_revision, filter_signature)`.
- One immutable view-model snapshot per draw/update cycle.

- [ ] Add an instrumentation test that draws unchanged panels repeatedly and expects zero full list rebuilds after the first.
- [ ] Replace the 0.3-second unconditional rebuild policy with revision/event invalidation.
- [ ] Reuse category counts, startup counts, selected record, and filtered rows from one view model.
- [ ] Measure a 152-record fixture and record rebuild count and elapsed time before/after.
- [ ] Commit with `perf: make plugin list refresh event-driven`.

### Task 16: Split oversized modules behind stable facades

**Files:**
- Create grouped operator modules under `bl_plugin_manager/operators/`
- Create service modules under `bl_plugin_manager/services/`
- Keep compatibility exports in existing `operators.py`, `library.py`, and `bridge.py` during migration
- Test: all Blender suites

- [ ] Freeze public function/operator IDs with characterization tests.
- [ ] Move one responsibility per commit: activation, metadata, runtime, import/update, store, migration, diagnostics.
- [ ] Keep registration order explicit in `operators/__init__.py` and verify enable/disable cycles after each move.
- [ ] Remove compatibility facades only after all internal callers migrate.
- [ ] Commit each bounded move separately; do not perform a single-file rewrite.

### Task 17: Release 1.1.0 and verify multi-computer behavior

**Files:**
- Modify version declarations and README
- Create: `docs/MULTI_COMPUTER_TEST_MATRIX.md`
- Generate: `dist/bl_plugin_manager-1.1.0.zip`

- [ ] Build a two-root/two-environment test matrix representing different drive letters, Blender versions, Python versions, offline/sync replacement, corrupt DB, and concurrent edit.
- [ ] Verify shared aliases/categories/notes/favorites/startup converge while local path/runtime results remain independent.
- [ ] Verify data migration and rollback from a copy of the real schema-1 database.
- [ ] Run all compile, unit, Blender E2E, security, performance, package, installer, reload, and live MCP checks.
- [ ] Produce a read-only category recovery diff from the historical backup; restore only after separate user confirmation.
- [ ] Publish 1.1.0 with rollback instructions and retain the last working installed version.

## Final verification commands

```powershell
python -m compileall -q .\bl_plugin_manager
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\blender\run_regressions.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\blender\run_e2e.ps1
python .\tests\blender\test_package.py
python .\build_zip.py
git diff --check
git status --short
```

Completion additionally requires Blender MCP verification of the installed version, one valid owned script path, one valid repository target, truthful offline/ready status, local path restoration after restart, and unchanged shared alias/category hashes after switching between different absolute roots.
