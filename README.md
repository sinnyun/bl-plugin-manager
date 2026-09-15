# Blender 插件库管理器 2.0

面向 Blender 4.2+ 的便携插件库。它统一管理传统插件、扩展插件、分类、别名、备注、启用状态、扩展仓库和自定义脚本目录，同时把“共享数据”与“每台电脑的路径和启用清单”严格分开。

## 关键行为

- 插件文件集中存放在一个可同步或可迁移的库目录。
- 别名、分类、备注、标签、收藏等共享配置跟随库迁移。
- 每台电脑有稳定设备 ID、可编辑设备名称和独立插件启用清单。
- 每台电脑可使用不同盘符或网络路径，不会把 A 电脑的绝对路径覆盖到 B 电脑。
- 管理开启时，Blender 原生“获取扩展 / 插件 / 文件路径”中的相关配置由本插件接管并双向同步。
- 管理停止时，只恢复扩展仓库、自定义脚本目录和库内插件启用状态；不会重置主题、快捷键、语言、渲染、资产库或其它偏好。
- 插件不会调用 Blender 的全局偏好保存或工厂重置操作，也不会自动修改“允许联网访问”。

## 数据布局

```text
BlenderPluginLibrary/
├── addons/                         传统插件
├── extensions/                     Blender 扩展
├── inbox/                          待导入文件夹或 ZIP
├── trash/                          可恢复移除区
└── .pm/
    ├── library.json                schema 2 共享数据库
    ├── devices/
    │   ├── <device-id-a>.json      设备 A 的启用意图、仓库与脚本路径
    │   └── <device-id-b>.json      设备 B 的独立配置
    ├── backups/
    └── archive/
```

本机文件（不放进同步库）：

```text
%LOCALAPPDATA%/BlenderPluginManager/
├── machine.json                    稳定设备 ID、名称、本机库路径、接管状态
└── state/<library-id>/<环境>.json  模块名、加载结果、错误等运行状态
```

`library.json` 只保存可跨电脑解释的数据。绝对路径、当前是否加载、兼容性测试结果等不会写回共享主数据库。设备配置按 ID 分文件，避免多台电脑同时改写同一份大配置。

## 安装

从 ZIP 安装：

```powershell
python build_zip.py
```

在 Blender 中打开“编辑 → 偏好设置 → 插件 → 从磁盘安装”，选择 `dist/bl_plugin_manager-2.0.0.zip`。

本机开发安装前先关闭所有 Blender 窗口，再运行：

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

安装脚本使用临时目录和上一版本回滚目录进行交换；若复制或编译失败，会尝试恢复旧版本。

## 首次使用

1. 在“编辑 → 偏好设置 → 插件”中启用“插件库管理器”。仅启用管理器不会自动接管 Blender 配置。
2. 在 3D 视图按 `N`，打开“插件库”。填写设备名称并选择插件库目录。
3. 点击“启用/修复插件库”。管理器会创建全新 schema 2 数据库、扫描插件并创建当前设备配置。
4. 首次启用会把当前原生扩展仓库和自定义脚本目录收编进当前设备配置，同时保证库的 `addons/` 与 `extensions/` 可被 Blender 发现。

选择目录时，空目录会初始化成新库；已有库会直接识别；散放的插件可收编。插件本身或与插件无关的非空目录会被拒绝，避免误改文件。

## 多电脑同步

每台电脑第一次运行都会生成不同的稳定设备 ID。设备名称可在插件偏好或主面板中修改，改名不会改变 ID。

- A 电脑可使用 `D:\BlenderLibrary`，B 电脑可使用 `X:\同步\BlenderLibrary`。
- 两台电脑共享 `library.json` 中的别名、分类、备注和插件信息。
- 两台电脑分别读取 `.pm/devices/<各自ID>.json`，自动恢复各自的启用插件列表和本地地址。
- 某插件暂时不存在或加载失败时，其“启用意图”仍保留；文件恢复后可再次应用。
- 设备数不硬限制为三台。

同步软件应同步整个插件库，包括隐藏目录 `.pm/`。不要同步 `%LOCALAPPDATA%/BlenderPluginManager/machine.json`，否则设备 ID 会被复制。

## 接管与停止

“启用/修复插件库”进入接管状态：

- 原生面板新增或修改的扩展仓库、脚本目录会写入当前设备文件；
- 在原生插件面板或本插件中改变库内插件启用状态，会进入当前设备启用清单；
- 管理器每两秒做一次字段比较，仅在实际变化时写文件；
- 库根脚本目录与库扩展目录是运行必需绑定，被原生面板删除后会自动补回。

“卸载挂载”或停用本插件会先保存当前设备配置，然后：

- 停用插件库内的插件；
- 删除自定义脚本目录；
- 删除自定义扩展仓库，并把官方仓库恢复到 Blender 默认 URL 与默认目录行为；
- 保留 Blender 内置仓库；
- 不修改联网权限及任何其它偏好。

停止状态保存在本机。再次加载管理器仍保持停止；必须点击“启用/修复插件库”才会重新接管并恢复当前设备清单。

## 插件管理

- 将插件文件夹或 ZIP 放进 `<库>/inbox/`，点击“扫描投放区”。
- 传统插件进入 `addons/`；带 `blender_manifest.toml` 的扩展进入 `extensions/`。
- 扩展 ID 会转换为合法 Python 标识符，manifest 会在导入前校验。
- 支持搜索、收藏、扁平分类、别名、备注、批量启停、更新检查和回收站移除。
- 兼容性实测会真实运行第三方插件代码，建议只在可信插件和已保存工程中使用。
- 在线安装需要用户自行在 Blender 系统设置中允许联网；管理器不会代为开启。

## 安全边界

- 不调用 `save_userpref`、`read_factory_settings`、`read_homefile` 或 `read_userpref`。
- 不复制或覆盖真实 `userpref.blend`。
- 不按名称猜测并清理未知来源。
- 所有数据库写入采用临时文件后原子替换；损坏的数据库保持只读，不会静默覆盖。
- 插件记录路径必须落在库的 `addons/` 或 `extensions/` 容器内。
- 测试运行器会验证全部 Blender 与插件状态路径位于一次性隔离目录，并检查真实偏好文件哈希不变。

## 验证

```powershell
python -m unittest discover -s tests -p "test*.py" -q
powershell -ExecutionPolicy Bypass -File _test/run_regressions.ps1
powershell -ExecutionPolicy Bypass -File _test/run_e2e.ps1
```

详细设计与审计见 `docs/PLUGIN_MANAGER_ARCHITECTURE_AUDIT.md`、`docs/superpowers/specs/2026-09-14-scoped-management-design.md` 和 `docs/superpowers/plans/2026-09-14-scoped-management.md`。

## 2.0.0 交付状态（2026-09-15）

- Python 单元测试：40 项通过，1 项按运行条件跳过。
- Blender 隔离回归：16/16 通过。
- Blender 隔离端到端：166/166 通过。
- 测试前后真实 Blender 5.2 `userpref.blend` SHA-256 均为 `6015931B9E7BF51E647A18CECD921C6A79FE33063EA224BF814EB751E631B5ED`。
- 已事务式安装至本机 Blender 4.5 和 5.2；两处安装的 29 个发行文件与源码逐文件一致。
- 安装包：`dist/bl_plugin_manager-2.0.0.zip`，SHA-256 为 `B12ACCD17A6CFC7FEA878A379B5764BD2874F7C58DA4E79C45C00C82904071FF`。
