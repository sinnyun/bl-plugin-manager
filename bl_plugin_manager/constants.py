"""插件库管理器 —— 常量与共享定义。"""

from __future__ import annotations

import os

# --- 身份标识 -------------------------------------------------------------
ADDON_ID = "bl_plugin_manager"
ADDON_NAME = "插件库管理器"
ADDON_VERSION = (1, 0, 3)
ADDON_VERSION_STR = "1.0.3"

# Blender 侧扩展仓库标识（必须是合法的 Python 标识符）
REPO_MODULE = "pmlib"
REPO_NAME = "插件库 (Plugin Library)"

# --- 库目录约定 -----------------------------------------------------------
DIR_ADDONS = "addons"          # 传统插件：<库>/addons/<名称>
DIR_EXTENSIONS = "extensions"  # 扩展插件：<库>/extensions/<id>
DIR_INBOX = "inbox"            # 投放区：文件夹或 zip
DIR_TRASH = "trash"            # 移除区
DIR_META = ".pm"               # 内部数据（元数据库、备份、日志）
DIR_BACKUPS = os.path.join(DIR_META, "backups")

DB_FILENAME = "library.json"
DB_SCHEMA = 1
PROCESSED_LOG = os.path.join(DIR_META, "processed.json")

DEFAULT_CATEGORY = "未分类"

# --- 插件类型 -------------------------------------------------------------
KIND_ADDON = "addon"          # 传统插件
KIND_EXTENSION = "extension"  # 扩展插件
KIND_LABELS = {KIND_ADDON: "传统插件", KIND_EXTENSION: "扩展插件"}

MANIFEST_NAME = "blender_manifest.toml"
LEGACY_INIT = "__init__.py"

# 无法作为插件识别的目录/文件名（扫描时忽略）
IGNORED_NAMES = {
    "__pycache__", ".git", ".svn", ".idea", ".vscode",
    ".blender_ext", "node_modules", ".pm",
}

# 扩展仓库中属于 Blender 自带/官方、迁移时不建议搬动的模块
BUILTIN_REPO_MODULES = {"blender_org", "user_default", "system", "addons"}
BUILTIN_REPO_HOSTS = ("extensions.blender.org", "blenderkit.com")
