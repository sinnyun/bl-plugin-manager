# 总插件数据库（Master Catalog）设计

## 1. 背景与目标

2.1 及以前的共享数据库 `<库>/.pm/library.json` 以**库内相对路径**为记录的键：

```json
{"schema": 2, "plugins": {"addons/MeshTools_v2.1": {...}}}
```

这在单机上工作正常，但多电脑场景下有两个结构性缺陷：

1. **目录名不同则连不上**：同一插件在 A 电脑是 `addons/MeshTools_v2.1`、在 B 电脑是
   `addons/网格工具`，扫描后是两条互不相关的记录，B 电脑看不到 A 电脑设置的别名与分类。
2. **同一路径换插件则错误继承**：卸载 A 后在同一目录装入 B，B 会继承 A 的别名、分类、
   备注与启用意图。

2.2 引入独立的**总插件数据库**，以稳定身份为键，把“插件资料”和“本机安装情况”解耦。

目标：

- 一份可同步的总插件资料库，记录插件身份与用户资料（别名、分类、标签、备注、收藏）。
- 每台电脑安装的插件数量、目录名、盘符都可以不同，扫描时自动匹配并连线。
- 插件改名或目录改名后仍能连回原记录，不丢别名与分类。
- 本机路径、模块名、启用状态、自启等绝不进入可同步的数据，也不被别的电脑覆盖。

非目标：

- 不实现同步服务；仍由用户自己的同步软件同步整个库目录。
- 不自动合并同步软件产生的冲突副本；冲突时拒绝覆盖并提示刷新。

## 2. 数据布局

```text
<插件库>/.pm/catalog.json            总插件数据库（跨电脑同步）
<插件库>/.pm/devices/<设备ID>.json   本机启用意图 / 仓库 / 脚本目录（随库同步，每设备一份）
%LOCALAPPDATA%/BlenderPluginManager/
├── machine.json                    设备 ID、设备名、本机库路径、接管状态
└── state/<library_id>/<环境>.json  本机绑定与运行状态（不随库同步）
```

### 2.1 总插件数据库结构

```json
{
  "schema": 1,
  "library_id": "UUID",
  "revision": 3,
  "categories": [{"id": "uncategorized", "name": "未分类", "order": 0}],
  "plugins": {
    "addon:mesh-tools": {
      "plugin_id": "addon:mesh-tools",
      "kind": "addon",
      "name": "Mesh Tools",
      "version": "2.1",
      "display_name": "网格工具",
      "category": "建模",
      "tags": ["mesh"],
      "note": "常用",
      "favorite": true,
      "names": ["mesh-tools"],
      "folders": ["MeshTools_v2.1", "网格工具"]
    }
  }
}
```

允许字段（`PORTABLE_FIELDS`，见 `bl_plugin_manager/storage/catalog.py`）：

- 身份与插件自带元数据：`plugin_id`、`kind`、`pkg_id`、`id`、`name`、`version`、
  `blender_min`、`blender_max`、`pkg_type`、`author`、`description`、
  `auto_category`、`auto_tags`、`doc_url`、`location`；
- 用户资料：`display_name`、`category`、`category_id`、`tags`、`note`、`favorite`、
  `source_url`；
- 匹配线索：`names`、`folders`；
- 时间戳：`created_at`、`updated_at`。

**禁止**写入：绝对或相对路径、模块名、是否启用、是否缺失、自启标记、文件时间与指纹、
加载结果、异常信息、兼容性结论、Blender/Python 版本、仓库或挂载状态。

写入侧由 `Catalog.set_plugin()` 直接拒绝未知/本机字段；读取侧由 `Catalog._sanitize()`
丢弃它们（而不是把整份文件判为损坏）——这样一个多余的同步字段不会让整库变成只读。

### 2.2 本机状态结构

```json
{
  "schema": 2,
  "bindings": {"addon:mesh-tools": "addons/网格工具"},
  "plugins": {
    "addon:mesh-tools": {
      "module": "网格工具", "enabled": true, "startup": true,
      "missing": false, "load_state": "ok", "update_available": false,
      "origin_path": "D:/inbox/mesh.zip"
    }
  }
}
```

`bindings` 是 `plugin_id → 库内相对路径`，即“这台电脑把该插件装在哪”。
`startup` 与 `enabled` 同属本机行为，因此**不随总插件数据库同步**。

## 3. 身份与匹配

`plugin_id` 是稳定、可读的字符串：

- 传统插件：`addon:<slug>`（slug 取自声明名，其次目录名）；
- 扩展插件：`ext:<slug>`（slug 优先取 manifest `id`）。

新条目只在确实没有匹配到既有记录时创建；既有条目一律沿用原 id。

匹配顺序（`services/catalog_sync.py`）：

1. **强线索**：扩展 manifest `id` → 插件声明名；
2. **弱线索**：目录名。

区分插件类型（addon / extension），两者之间绝不互相匹配。

防误连：目录名是弱线索。若资料库记录与本机插件**都**声明了名称且两者不同，则拒绝
用目录名连线，转而新建条目。这覆盖了“卸载 A、同一目录装入 B”的情况；旧条目保留在
总资料库中，重新装回 A 时会自动连回。

每条记录累积 `names` / `folders` 历史线索，因此改名后仍能命中。

## 4. 读写路径

`LibraryDB`（`bl_plugin_manager/db.py`）是三份数据的视图：以 `plugin_id` 为键，合并

- 总插件数据库的共享字段；
- 本机状态里的绑定（补出 `rel`、`folder_name`）；
- 本机运行状态（`module`、`enabled`、`startup`、加载结果等）。

写回 `save()` 时按字段归属拆分：共享字段进 `catalog.json`，绑定与运行状态进本机状态
文件。因此本机数据永远不会泄漏进可同步的数据。

本机只列出“有绑定”的条目：总资料库里其它电脑安装的插件不会被显示成本机插件，但会在
插件出现时被自动匹配。

## 5. 迁移

首次连接旧库时：

1. 若 `library.json` 存在且不可解析 → 进入 `CORRUPT` 只读状态，不创建 `catalog.json`，
   绝不覆盖旧文件。
2. 否则创建/读取 `catalog.json`，读取旧 `library.json`，把每条旧记录当作“上一次扫描
   结果”走同一套身份匹配并入总资料库（`LibraryDB.absorb_legacy`）：
   - 别名、分类、标签、备注、收藏保留到总资料库；
   - **自启**转入本机状态；
   - 旧路径记录为本机绑定；
   - 匹配到既有条目就合并，不会产生重复条目。
3. 旧文件原样移动到 `.pm/archive/library.pre-catalog.<UTC>.json` 并设为只读。

## 6. 一致性与故障处理

- 总插件数据库与旧库分别校验：结构合法才可写；损坏 → 只读，`save()` 抛
  `CorruptDatabaseError`，不替换源文件。
- 写入使用同目录临时文件、`fsync`、原子替换；写入前比较加载签名，被外部更新时抛
  `CatalogConflictError`，由视图转换为可读错误。
- 读取缓存（`storage/json_cache.py`）以 `st_dev/st_ino/st_ctime_ns/st_mtime_ns/st_size`
  为签名，原子替换后不会命中旧内容；返回深拷贝，调用方的未保存修改不会污染缓存。

## 7. 验收标准

- 两台电脑目录名不同时，扫描后连到同一条记录，别名与分类一致。
- 插件改名 / 目录改名后仍连回原记录，不产生重复条目。
- 同一目录换插件时新插件不继承旧别名；旧条目保留，重装后自动连回。
- 总插件数据库中不出现任何本机字段（路径、模块名、启用状态、自启）。
- 各电脑的启用状态与自启标记互不覆盖。
- 旧 `library.json` 完整迁移且被只读归档；损坏的旧库或资料库都不会被覆盖。
- 移除插件只影响本机，其它电脑与该插件资料不受影响。

## 8. 模块边界

- `storage/catalog.py`：总插件数据库的 schema、字段归属、原子写入与冲突检测。
- `storage/json_cache.py`：按文件签名的共享解析缓存。
- `storage/local_state.py`：本机绑定与运行状态。
- `services/catalog_sync.py`：纯函数式身份匹配与旧数据迁移（不依赖 Blender）。
- `db.py`：三份数据的读写视图与字段拆分。
- `library.py`：导入、同步、移除、更新等文件操作。
