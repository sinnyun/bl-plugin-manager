# Blender 插件库管理器 2.0.0 全面重构实施计划

> 实施时必须遵循测试驱动开发：每项功能先提交能复现问题的失败测试，再写最小实现使其通过，最后重构。不得在真实插件库上运行破坏性故障注入。

**目标：** 一次性建立干净的 2.0.0 架构，解决多电脑路径互相覆盖、切库无法识别统一配置、共享/本机状态混杂、同步覆盖、越界文件操作、阻塞和生命周期不可靠等问题。

**已确认决策：** 不兼容、不解析、不迁移 1.x 数据。旧 `.pm/library.json` 只改名归档为只读文件；随后创建全新 schema 2 数据库。

**技术环境：** Blender 5.2.1 LTS、Python 3.13、Blender Python API、UTF-8 JSON、PowerShell 测试入口。

## 全局约束

- 所有自动测试使用临时 Blender 配置、脚本目录和临时插件库。
- 在用户明确进入实机安装步骤前，不修改 `D:\nastongbu\qitaziliao\blender_addons` 或 Blender 真实偏好。
- 共享数据库只允许白名单字段；本机状态永不写入插件库。
- 任意记录路径必须先通过容器边界验证。
- 数据库损坏、schema 不支持、同步冲突、归档失败时一律停止写入。
- 每个任务结束运行相关测试和 `git diff --check`，保持小提交。
- 2.0.0 不包含 migration 模块或兼容 facade；旧数据归档代码不得读取记录内容。

## Task 1：把测试基线纳入版本控制

**文件：**

- 修改：`.gitignore`
- 新建：`tests/blender/run_regressions.ps1`
- 新建：`tests/blender/run_e2e.ps1`
- 新建：`tests/blender/test_regressions.py`
- 新建：`tests/blender/test_e2e.py`
- 新建：`tests/blender/test_package.py`
- 新建：`tests/fixtures/`

**步骤：**

1. 把 `_test` 中可重复的 16 项回归和 161 项端到端测试迁入 `tests/blender`，继续忽略一次性 `_mcp_test`、`_migrate` 和生成物。
2. 让测试入口接受 `-BlenderExe` 或 `BLENDER_EXE`，并固定使用临时 `BLENDER_USER_CONFIG`、`BLENDER_USER_SCRIPTS`、`TEMP`、`TMP`。
3. 临时加入一个故意失败断言，验证进程退出码非零后删除该断言。
4. 运行原始基线，确认 16/16、161/161；保存一个不含插件记录内容的实机只读环境快照测试。
5. 提交：`test: track isolated Blender verification suite`。

## Task 2：定义 schema 2 和三层存储契约

**文件：**

- 新建：`bl_plugin_manager/storage/__init__.py`
- 新建：`bl_plugin_manager/storage/models.py`
- 新建：`bl_plugin_manager/storage/shared_db.py`
- 新建：`bl_plugin_manager/storage/machine_config.py`
- 新建：`bl_plugin_manager/storage/local_state.py`
- 测试：`tests/blender/test_storage.py`

**接口：**

- `SharedLibrary(schema, library_id, revision, categories, plugins)`
- `MachineConfig(schema, device_id, library_path)`
- `RuntimeState(library_id, environment, revision, plugins, repository_backup)`
- `environment_key() -> str`

**步骤：**

1. 先写失败测试，固定共享字段白名单和本机字段白名单。
2. 验证共享数据库拒绝绝对路径、设备信息、`module/enabled/missing/load_error/compat` 等环境字段。
3. 实现严格类型校验、默认分类、UUID `library_id` 和递增 revision。
4. 实现 `%LOCALAPPDATA%/BlenderPluginManager` 位置及测试环境变量覆盖。
5. 验证电脑 A/B 使用不同路径，共享配置相同但运行状态独立。
6. 提交：`feat: establish clean schema 2 storage boundaries`。

## Task 3：实现无迁移的全新数据库初始化事务

**文件：**

- 修改：`bl_plugin_manager/storage/shared_db.py`
- 新建：`bl_plugin_manager/services/initialization.py`
- 测试：`tests/blender/test_initialization.py`

**接口：**

- `inspect_database(path) -> MISSING | SCHEMA_2 | LEGACY | CORRUPT | FUTURE`
- `initialize_library(root, *, archive_existing: bool) -> InitializationReport`
- `archive_name(utc_now) -> library.pre-2.0.<timestamp>.json`

**步骤：**

1. 写失败测试覆盖：无文件、合法 schema 2、schema 1、任意旧 JSON、损坏 JSON、未来 schema、同名归档、只读失败、原子创建失败。
2. 对非 schema 2 文件只进行字节级移动，不解析或复制字段。
3. 归档到 `.pm/archive` 并设置只读；用唯一 UTC 时间戳和安全重名后缀。
4. 只有归档成功后才原子创建最小 schema 2；失败时把入口恢复到初始化前状态。
5. 合法 schema 2 必须幂等连接，不能重新生成 `library_id`。
6. 提交：`feat: initialize clean databases with read-only legacy archive`。

## Task 4：阻止数据库记录路径逃逸

**文件：**

- 新建：`bl_plugin_manager/security/__init__.py`
- 新建：`bl_plugin_manager/security/paths.py`
- 修改：`bl_plugin_manager/library.py`
- 修改：`bl_plugin_manager/operators.py`
- 测试：`tests/blender/test_path_security.py`

**接口：**

- `resolve_record_path(root, rel, kind, *, must_exist=False) -> Path`
- `UnsafeLibraryPathError`

**步骤：**

1. 固化当前 `../outside_plugin` 可被永久删除的失败复现。
2. 测试绝对路径、盘符、UNC、`..`、混合分隔符、大小写、符号链接/目录联接逃逸、Windows 保留名和合法 Unicode 路径。
3. 使用 `realpath + commonpath` 验证路径位于对应 `addons` 或 `extensions` 容器。
4. 删除、更新、替换、备份、打开文件夹和记录派生读取全部改用该接口。
5. 验证所有恶意路径在任何文件变化前失败。
6. 提交：`fix: contain every plugin record path`。

## Task 5：实现可靠、冲突感知的共享数据库

**文件：**

- 修改：`bl_plugin_manager/storage/shared_db.py`
- 测试：`tests/blender/test_shared_db.py`

**接口：**

- `DatabaseStatus = OK | MISSING | CORRUPT | UNSUPPORTED | CONFLICT`
- `read_snapshot(use_cache=True)`
- `update_plugin_fields(key, changes, expected_revision)`
- `update_categories(mutator, expected_revision)`
- `DatabaseConflictError`

**步骤：**

1. 写失败测试复现同大小/同修改时间替换命中旧缓存，以及 A 读取、B 写入、A 覆盖 B 的丢失更新。
2. 缓存签名加入文件身份、创建/修改时间、大小和内容摘要；显式激活绕过缓存。
3. 保存前比较 revision 和磁盘签名，只应用请求字段；字段不在白名单则拒绝。
4. 使用同目录临时文件、flush、`fsync`、原子替换和 `library.json.bak.1..3`。
5. 损坏和不支持的 schema 进入只读状态，不允许扫描流程重建覆盖。
6. 提交：`fix: make shared metadata conflict safe`。

## Task 6：隔离本机路径与运行状态

**文件：**

- 修改：`bl_plugin_manager/storage/machine_config.py`
- 修改：`bl_plugin_manager/storage/local_state.py`
- 修改：`bl_plugin_manager/preferences.py`
- 修改：`bl_plugin_manager/items.py`
- 测试：`tests/blender/test_local_storage.py`

**步骤：**

1. 测试 Unicode、UNC、离线路径、本机配置损坏、原子写入失败和允许键集合。
2. `library_path` 改为 `SKIP_SAVE`、空默认值；启动只从 `machine.json` 恢复，不读取旧 Blender 偏好作为迁移来源。
3. 路径暂时不可用时保留值并报告 `OFFLINE`，不得创建目录。
4. 模块、启用、缺失、扫描指纹、错误、残留、兼容性和仓库备份全部写入环境级本机状态。
5. 扫描共享库时只更新便携身份；运行探测只更新本机状态。
6. 提交：`feat: isolate machine path and runtime state`。

## Task 7：收敛挂载并建立单一激活流程

**文件：**

- 新建：`bl_plugin_manager/services/mounts.py`
- 新建：`bl_plugin_manager/services/activation.py`
- 修改：`bl_plugin_manager/bridge.py`
- 修改：`bl_plugin_manager/preferences.py`
- 修改：`bl_plugin_manager/operators.py`
- 修改：`bl_plugin_manager/__init__.py`
- 测试：`tests/blender/test_activation.py`

**接口：**

- `MountManager.inspect(root) -> MountReport`
- `MountManager.reconcile(root, save=True) -> MountReport`
- `LibraryService.activate(path, initialize=False, persist=True) -> ActivationReport`

**步骤：**

1. 建立与实机一致的 D: 有效挂载、X: 失效重复挂载、官方仓库和失效 `pmlib` 测试夹具。
2. 只识别并修改管理器拥有的名称前缀和固定 `pmlib`；保留全部官方/用户条目。
3. `ready` 同时验证条目、目标目录和仓库模块；三次协调后状态完全相同。
4. 激活顺序固定为验证 → 无缓存开库/初始化 → 保存本机路径 → 挂载 → 刷新模块 → 扫描一次 → 发布一次 UI 事件。
5. 删除属性回调中的扫描/保存，测试一次切库只有一次扫描与一次数据库事务。
6. 测试库复制到不同盘符后别名/分类保持一致，模块在 `pmlib` 建立后才解析。
7. 提交：`fix: make mounting and activation deterministic`。

## Task 8：让插件注册和注销具备事务性

**文件：**

- 修改：`bl_plugin_manager/__init__.py`
- 修改：`bl_plugin_manager/header.py`
- 测试：`tests/blender/test_lifecycle.py`

**步骤：**

1. 测试首次导入不热重载、启用→禁用→启用、类注册中途失败、菜单回调失败和计时器残留。
2. 修正热重载哨兵，记录本次成功注册的类、回调和计时器。
3. 失败时逆序回滚且保留原异常；注销操作可重复。
4. 移除按宽泛模块前缀注销未知类型的行为。
5. 提交：`fix: make addon lifecycle transactional`。

## Task 9：把兼容性检测移到隔离环境

**文件：**

- 新建：`bl_plugin_manager/compatibility.py`
- 修改：`bl_plugin_manager/bridge.py`
- 修改：兼容性相关操作符和 UI
- 测试：`tests/blender/test_compatibility.py`

**步骤：**

1. 测试 Blender 4/5、Python 3.11/3.13 和不同仓库模块的结果互相隔离。
2. 默认启动独立后台 Blender 子进程测试第三方插件，限制超时并收集结构化结果。
3. 当前会话测试仅作为带明确副作用警告的高级选项。
4. 精确比较测试前后类型、处理器、模块和偏好快照；无法确认的残留提示重启，不猜测注销。
5. 文件、偏好、网络和第三方执行类操作符移除误导性的 `UNDO`。
6. 提交：`feat: isolate compatibility testing by environment`。

## Task 10：加固导入、下载、更新和删除事务

**文件：**

- 新建：`bl_plugin_manager/security/archive.py`
- 新建：`bl_plugin_manager/services/plugin_files.py`
- 修改：`bl_plugin_manager/library.py`
- 修改：`bl_plugin_manager/store.py`
- 测试：`tests/blender/test_plugin_files.py`

**接口：**

- `inspect_archive(path, limits) -> ArchivePlan`
- `PluginFileTransaction.stage/validate/swap/rollback`

**步骤：**

1. 测试成员数、单文件、总展开大小、压缩比、路径长度、保留名、嵌套根、损坏 ZIP 和中断交换。
2. 删除 `tempfile.mktemp`，使用安全创建的临时文件和隔离暂存目录。
3. 下载时流式限制字节数，只允许 HTTPS；目录提供哈希时强制校验。
4. 导入、更新和删除统一为暂存→验证→备份→交换→恢复启用状态；任一步失败可回滚。
5. 清理失败目录不参与扫描，并向用户报告确切位置。
6. 提交：`fix: secure plugin file transactions`。

## Task 11：长任务非阻塞化

**文件：**

- 新建：`bl_plugin_manager/tasks.py`
- 修改：扫描、商店、更新、兼容性操作符
- 修改：`bl_plugin_manager/ui.py`
- 测试：`tests/blender/test_tasks.py`

**接口：**

- `TaskController.start(io_job, main_thread_steps)`
- 状态：`idle/running/cancelling/succeeded/failed`

**步骤：**

1. 用延迟下载和大目录扫描测试 Blender 计时器仍响应，取消只在安全边界生效。
2. 网络、哈希和纯文件 IO 进入工作线程；工作线程禁止调用 `bpy`。
3. Blender 操作通过主线程 modal/timer 小步执行，界面显示进度、当前项和错误。
4. 任务运行时禁用冲突按钮，退出/注销时安全取消并清理。
5. 提交：`feat: keep long operations responsive`。

## Task 12：用 revision 驱动 UI，并拆分模块

**文件：**

- 新建：`bl_plugin_manager/operators/` 下分组模块
- 新建：`bl_plugin_manager/services/` 下业务模块
- 修改：`bl_plugin_manager/items.py`
- 修改：`bl_plugin_manager/ui.py`
- 精简：`bl_plugin_manager/operators.py`、`library.py`、`bridge.py`
- 测试：`tests/blender/test_ui_performance.py` 及全套测试

**步骤：**

1. 用特征测试冻结公开操作符 ID、注册顺序和关键函数行为。
2. UI 缓存键改为 `(library_id, shared_revision, runtime_revision, filter_signature)`；无状态变化的连续绘制不重建列表。
3. 以激活、元数据、运行、文件、商店、诊断为单位逐步移动操作符，每次移动后运行启停循环。
4. 共享数据库、本机状态、文件事务和 Blender bridge 之间只通过服务接口连接。
5. 对 152 条记录夹具比较重建次数和耗时，保存性能基线。
6. 提交：`refactor: split services and make UI revision driven`。

## Task 13：错误传播、诊断与用户状态

**文件：**

- 新建：`bl_plugin_manager/diagnostics.py`
- 修改：`bl_plugin_manager/ui.py`
- 修改：所有仍存在宽泛异常吞噬的模块
- 测试：`tests/blender/test_diagnostics.py`

**步骤：**

1. 搜索并逐一分类 `except Exception`；禁止无日志 `pass`。
2. 统一错误对象、日志上下文和用户可执行建议，敏感信息与完整插件记录不进入诊断包。
3. UI 明确显示 `UNCONFIGURED/OFFLINE/METADATA_CORRUPT/UNSUPPORTED/CONFLICT/MOUNT_DEGRADED/READY`。
4. 保存偏好、保存本机配置、挂载和数据库写入失败不得提示完整成功。
5. 提交：`fix: surface actionable failures and truthful state`。

## Task 14：安全打包、安装和回滚

**文件：**

- 修改：`bl_plugin_manager/constants.py`
- 修改：`bl_plugin_manager/__init__.py`
- 修改：`README.md`
- 修改：`install.ps1`
- 修改：`build_zip.py`
- 新建：`docs/MULTI_COMPUTER_TEST_MATRIX.md`
- 测试：`tests/blender/test_package.py`

**步骤：**

1. 写版本一致性、包内容、暂存编译失败、交换失败和 `.previous` 回滚测试。
2. 全部版本源升级到 2.0.0，README 明确全新数据库、每机路径、共享配置和本机状态。
3. 安装器先复制到同级暂存目录并编译检查，再交换目标目录；保留上一版本，不在 Blender 运行时盲目删除已加载代码。
4. 构建 `dist/bl_plugin_manager-2.0.0.zip`，确认不含测试、缓存、机器配置、真实数据库和审计临时文件。
5. 完成两盘符、两环境、离线、同步替换、并发编辑、损坏库和旧库归档测试矩阵。
6. 提交：`release: prepare plugin manager 2.0.0`。

## Task 15：实机备份、安装与 Blender 验证

**前置条件：** 所有隔离测试通过，用户确认进入实机安装步骤，Blender 插件已禁用或 Blender 已关闭。

**步骤：**

1. 备份当前安装目录、`userpref.blend`、脚本目录/仓库快照；计算并记录真实旧数据库哈希。
2. 安装 2.0.0，但不自动采用同步偏好中的 X: 路径。
3. 使用本机有效路径 `D:\nastongbu\qitaziliao\blender_addons` 进行显式连接。
4. 将旧入口数据库归档为只读文件并创建 schema 2；核对归档字节哈希与原文件一致。
5. 通过 Blender MCP 验证插件版本、单一有效脚本路径、单一 `pmlib` 仓库、真实 READY/OFFLINE 状态、重启后本机路径恢复。
6. 在复制的第二根目录模拟另一电脑，验证同一 `library_id` 和统一配置可读取，本机运行状态不互相污染。
7. 若任一步失败，回滚插件安装和 Blender 偏好；保留新库及旧库归档现场，不做自动反向数据转换。
8. 最终提交：`release: verify plugin manager 2.0.0 in Blender`。

## 最终验证命令

```powershell
python -m compileall -q .\bl_plugin_manager
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\blender\run_regressions.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\blender\run_e2e.ps1
python .\tests\blender\test_package.py
python .\build_zip.py
git diff --check
git status --short
```

完成标准还包括：所有测试实际退出码为 0；没有未说明的工作区改动；旧数据库归档哈希一致且只读；新共享数据库不含本机字段；两台模拟电脑共享配置一致；Blender 5.2.1 连续启用/禁用/重启三轮无残留。
