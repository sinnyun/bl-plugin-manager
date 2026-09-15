# 迁移记录（把散落插件统一到插件库）

本次把本机 5.2 环境中散落各处的插件统一收敛到插件库
`D:\nastongbu\qitaziliao\blender_addons`。

## 迁移前的混乱来源

| 来源 | 说明 |
|------|------|
| 7 个脚本目录 | 偏好里注册的 `D:\...\blender\*_chajian`，其 `addons/` 子目录被当传统插件加载 |
| 10 个本地扩展仓库 | 其中 `chajian/addons` 被**同时**注册为脚本目录的 addons 和扩展仓库，导致同一插件被加载两次 |
| 用户配置 scripts/addons | `%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons` |
| 散装单文件插件 | `addon.py` 与 `blender_mcp.py` 内容完全相同，重复启用 |

## 迁移结果

- **44 个插件入库**：`addons/` 24 项（含 1 个配置文件 `Y`，属 Petik，被自动忽略）+
  `extensions/` 21 项。
- **合并重复**：三个 `Petik` 副本合一；`碰撞骨骼` 与 `飘动摆动骨骼` 为同一插件
  Swingy Bone Physics v1.8.0，保留较新的一份，另一份移入 `trash/`。
- **散装插件规范化**：`blender_mcp.py` → `addons/blender_mcp/__init__.py`；
  重复的 `addon.py` → `trash/`。
- **配置精简**：脚本目录只保留库根目录；扩展仓库只保留
  `blender_org`（官方商店）、`user_default`（BlenderKit）、`system`（系统）与
  新增的 `pmlib`（插件库）。
- **启用状态**：原 25 个启用的插件中 18 个成功还原；其余 7 个失败原因如下，
  均与迁移无关。

### 未能一键还原启用的插件（附原因）

| 插件 | 原因 | 是否迁移前就存在 |
|------|------|------------------|
| mmd_tools | manifest 声明 `blender_version_max = 5.0.0`，Blender 5.2 拒绝加载 | 是（版本上限） |
| ZenSets | 插件自身注册 operator 时 `bl_idname` 为中文字符，非法 | 是 |
| softwrap2 | 自带扩展模块是 `cp311` 编译，Blender 5.2 用 Python 3.13 | 是 |
| GroupPro | 后台模式无 GPU 上下文；图形界面下可正常启用 | 否（仅 headless 限制） |
| RealMotion Pro | 同上（注册预览图需要 GPU） | 否（仅 headless 限制） |

> 提示：后两个（GroupPro、RealMotion Pro）在正常图形界面下点启用即可；
> 前三个需要插件作者更新或换版本。

## 保留由 Blender 自行管理的部分

按你的选择「只统一自装插件」，以下**未**迁入库，仍由 Blender 管理：

- Blender 官方扩展商店的 17 个扩展（`blender_org`，保留一键更新）；
- BlenderKit（`user_default`，162MB，自带独立更新器）。

插件库仍可通过商店索引检测它们的更新，但不会搬动其文件。

## 旧目录处理结果

`D:\nastongbu\qitaziliao\blender\` 下的插件与安装包在完成核对后**已全部清理**，
仅保留 `ass/`（270G 资产库）。

清理前的内容（均已核对并收编，随后删除）：

| 目录 | 体积 | 内容 | 处理 |
|------|------|------|------|
| `ass/` | 270G | 资产库（素材，非插件） | **保留** |
| `chajian/addons/备份用` | 17G | 88 个插件备份 | 独有插件全部收编 |
| `chajian/*.zip` 等 | 约 4.6G | 插件原始安装包与解压副本 | 内含插件全部收编 |

完整文件清单存档于 `_migrate/old_listing_backup.txt`。

## 第二轮：备份目录与残留包的彻底收编

第一次迁移把名为 `备份用` 的目录整体当成备份跳过了，导致其中 88 个插件未入库。
第二轮做了完整核对与收编：

* 逐一识别 `D:\...\blender\`（除 `ass/`）下所有插件目录、zip 包内容、单文件插件；
* 按 **id / 文件夹名 / 显示名（归一化后）** 三重比对，与库、官方商店、BlenderKit 去重；
* 结果：**新增 90 个插件入库**（含 87 个目录/zip + Proxy Picker + 2 个单文件插件）；
* 期间合并的重复：同一插件的多个版本（如四边重拓扑、use全局翻译 2.0.0/2.0.1）只保留最高版本；
* Auto-Rig Pro 的安装包内部组件（`ARP相关`、`AutoRigPro`）整棵子树跳过，避免误收内部库（`torch`/`cv2`）；
* 单文件插件 `bbd-lite-1_0_6.py`、`render_button.py` 规范化为目录后入库；
  `proxy_picker.py`（v0.7）因已有新版 v1.1.10 而丢弃。

### 最终规模

| 项目 | 数量 |
|------|------|
| 库内插件总数 | **134**（传统插件 80 + 扩展插件 54） |
| 重复项 | 0 |
| 旧目录残留 | 仅 `ass/`（270G 资产库，用户保留） |

### 第三轮：清理

* 清理前把旧目录全部 6931 个文件的完整清单存档到 `_migrate/old_listing_backup.txt`（4.6GB）；
* 然后删除 `blender\` 下除 `ass/` 外的 9 个目录；
* 清理动作在确认「旧目录零遗漏、库内零重复、配置无旧路径引用」之后执行。

## 迁移脚本与可回滚

`_migrate/` 下保留本次使用的脚本：

- `inventory.py` / `plan.py` —— 只读盘点与预案；
- `do_migrate.py` —— 第一轮迁移（`--dry` / `--run`）；
- `restore_enabled.py` —— 新进程还原启用状态；
- `fixup.py` / `fixup2.py` —— 修正仓库目录、清理幽灵条目；
- `audit_old.py` / `audit_backup.py` / `audit_zips.py` —— 旧目录/备份/zip 审计；
- `import_leftovers.py` —— 第二轮收编（`--dry` / `--run`）；
- `final_crosscheck.py` / `check_dupes.py` —— 零遗漏与零重复核对；
- `cleanup_old.py` —— 归档清单并清理旧目录（`--dry` / `--run`）；
- `verify_state.py` / `final_verify.py` —— 状态验证。

**回滚**：
- `journal.json`（第一轮）与 `journal_import.json`（第二轮）记录每个插件的源路径与目标路径；
- `backups/<时间戳>/` 备份了迁移前的 `userpref.blend` 与配置快照；
- `old_listing_backup.txt` 是已删旧目录的完整文件清单。
