"""插件总资料库（catalog）：一份独立、可同步的插件元数据主数据库。

它是整个管理器的“总插件资料库”，位于 ``<插件库>/.pm/catalog.json``，随插件库
一起同步。与旧结构的根本区别是**记录以稳定的 plugin_id 为键**，而不是以库内
相对路径为键：

* 各台电脑安装的插件数量、目录名、所在盘符都可以不同；只要扫描到的插件能匹配
  到同一条记录，别名、分类、标签、备注、收藏、自启等用户数据就会自动复用。
* 匹配依靠“身份线索”（扩展 manifest id、插件声明名、目录名，以及历史上观察
  到的这些值的集合），因此目录改名或插件改名后仍能重新连回原记录。
* 本机数据不写入本文件：每台电脑实际把插件放在哪个相对目录，记录在该设备的
  ``.pm/devices/<device-id>.json`` 的 ``bindings`` 中；模块名、是否启用、加载
  结果等运行状态记录在 LOCALAPPDATA。
* 损坏时保持只读并拒绝覆盖；写入使用同目录临时文件 + fsync + 原子替换。

本模块不依赖 Blender，可在普通 Python 进程中完整测试。
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import json_cache


SCHEMA = 1
DB_NAME = "catalog.json"
LEGACY_DB_NAME = "library.json"
DEFAULT_CATEGORY = {"id": "uncategorized", "name": "未分类", "order": 0}

# 允许写入总资料库的字段。任何本机相关字段（绝对路径、模块名、是否启用、
# 运行结果）都会被拒绝，确保本机路径永远不会泄漏进可同步的数据。
PORTABLE_FIELDS = frozenset({
    # 稳定身份与插件自带元数据
    "plugin_id", "kind", "pkg_id", "id", "name", "version",
    "blender_min", "blender_max", "pkg_type", "author",
    "description", "auto_category", "auto_tags", "doc_url", "location",
    # 用户数据（跨电脑共享）
    "display_name", "category", "category_id", "tags", "note",
    "favorite",
    # 来源信息（插件官网/文档地址，不指向本机路径）
    "source_url",
    # 身份匹配线索：历史上观察到的名称与目录名
    "names", "folders",
    "created_at", "updated_at",
})

# 刻意**不**放进总资料库：机器行为与运行状态。各电脑安装的插件不同，
# 「自启」「是否启用」属于本机决策，随本机状态文件保存。
#   startup  —— 该插件是否随 Blender 启动自动加载
#   enabled  —— 当前是否已启用
#   module   —— 本机解析出的模块名
# 详见 bl_plugin_manager/storage/local_state.py 与 db.py 的字段归属表。


@dataclass(frozen=True)
class InitializationReport:
    status: str
    path: Path
    archived_legacy: Path | None = None


class InvalidCatalogFieldError(ValueError):
    pass


class CatalogConflictError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Catalog:
    """``<插件库>/.pm/catalog.json`` 的读写封装。"""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.directory = self.root / ".pm"
        self.path = self.directory / DB_NAME
        self.legacy_path = self.directory / LEGACY_DB_NAME
        self.data: dict = {}
        self.status = "MISSING"
        self.loaded_signature = None

    # -- 结构 --------------------------------------------------------------
    @staticmethod
    def _empty() -> dict:
        return {
            "schema": SCHEMA,
            "library_id": str(uuid.uuid4()),
            "revision": 0,
            "categories": [dict(DEFAULT_CATEGORY)],
            "plugins": {},
        }

    @staticmethod
    def _is_valid(value: object) -> bool:
        """结构校验：只判断文件是否为一个可解释的资料库。

        字段白名单不在这里强制。若把“出现未知字段”也判成损坏，一个多余字段就会
        让整份资料库变成只读；而本机路径泄漏的风险由 :meth:`_sanitize` 在**读取
        时丢弃**未知字段来消除（写入侧另有 :meth:`set_plugin` 直接拒绝）。
        """
        if not isinstance(value, dict) or value.get("schema") != SCHEMA:
            return False
        if not isinstance(value.get("library_id"), str) or not value["library_id"]:
            return False
        if not isinstance(value.get("revision"), int):
            return False
        if not isinstance(value.get("plugins"), dict):
            return False
        categories = value.get("categories")
        return isinstance(categories, list) and all(
            isinstance(item, dict) and set(item) <= {"id", "name", "order"}
            and isinstance(item.get("id"), str) and isinstance(item.get("name"), str)
            for item in categories
        )

    @staticmethod
    def _sanitize(value: dict) -> dict:
        """丢弃记录中不属于总资料库的字段。

        这样即便同步来的文件里混入了 ``rel`` / ``module`` / ``enabled`` 之类的
        本机字段，它们也不会被读进视图、更不会在下一次写入时被带回文件。
        """
        plugins = {}
        for plugin_id, record in (value.get("plugins") or {}).items():
            if not isinstance(plugin_id, str) or not plugin_id or not isinstance(record, dict):
                continue
            clean = {field: record[field] for field in PORTABLE_FIELDS if field in record}
            clean["plugin_id"] = plugin_id
            plugins[plugin_id] = clean
        value["plugins"] = plugins
        return value

    # -- 磁盘 IO -----------------------------------------------------------
    def _signature(self):
        return json_cache.signature(str(self.path))

    def load(self) -> str:
        """读取资料库；返回状态 OK / MISSING / CORRUPT。"""
        sig, value = json_cache.read(str(self.path))
        if sig is None:
            self.data = {}
            self.status = "MISSING"
            self.loaded_signature = None
            return self.status
        if value is None or not self._is_valid(value):
            self.data = {}
            self.status = "CORRUPT"
            self.loaded_signature = sig
            return self.status
        self.data = self._sanitize(value)
        self.data.setdefault("plugins", {})
        self.data.setdefault("categories", [dict(DEFAULT_CATEGORY)])
        self.status = "OK"
        self.loaded_signature = sig
        return self.status

    def initialize(self) -> InitializationReport:
        """创建/读取资料库；旧版 library.json 由调用方通过 :meth:`read_legacy` 迁移。

        旧文件损坏时返回 ``CORRUPT`` 而不创建资料库，调用方据此进入只读状态，
        绝不覆盖或丢弃旧文件。
        """
        if self.legacy_is_corrupt():
            return InitializationReport("CORRUPT", self.path)

        status = self.load()
        if status == "OK":
            return InitializationReport("READY", self.path)
        if status == "CORRUPT":
            return InitializationReport("CORRUPT", self.path)

        self.data = self._empty()
        self._write()
        self.status = "OK"
        return InitializationReport("CREATED", self.path)

    def legacy_is_corrupt(self) -> bool:
        """旧库存在但不可解析时为 True（此时不得创建/覆盖任何数据）。"""
        if not self.legacy_path.exists():
            return False
        try:
            with open(self.legacy_path, "r", encoding="utf-8") as fh:
                json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return True
        return False

    def read_legacy(self) -> dict | None:
        """读取旧版 schema 2 的 library.json；不存在或非 schema 2 时返回 None。"""
        if not self.legacy_path.exists():
            return None
        with open(self.legacy_path, "r", encoding="utf-8") as fh:
            value = json.load(fh)
        if not isinstance(value, dict) or value.get("schema") != 2:
            return None
        return value

    def archive_legacy(self) -> Path | None:
        """把旧版 library.json 移到 .pm/archive 并设为只读。"""
        if not self.legacy_path.exists():
            return None
        archive_dir = self.directory / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = archive_dir / f"library.pre-catalog.{stamp}.json"
        index = 1
        while target.exists():
            target = archive_dir / f"library.pre-catalog.{stamp}.{index}.json"
            index += 1
        os.replace(self.legacy_path, target)
        try:
            target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        except OSError:
            pass
        return target

    def _write(self) -> None:
        if self.status == "CORRUPT":
            raise CatalogConflictError(f"拒绝覆盖损坏数据库: {self.path}")
        current = self._signature()
        if self.loaded_signature is not None and current is not None and current != self.loaded_signature:
            raise CatalogConflictError(f"数据库已被外部更新: {self.path}")
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + f".{uuid.uuid4().hex}.tmp")
        payload = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            json_cache.store(str(self.path), self.data)
            self.loaded_signature = self._signature()
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def save(self) -> None:
        self.data["revision"] = int(self.data.get("revision", 0)) + 1
        self._write()

    def refresh_if_stale(self) -> bool:
        """文件已被其它实例/进程更新时重新读取；返回是否发生了刷新。

        同一进程里常有多个 ``LibraryDB`` 句柄（面板、操作符、任务各持一份），
        它们会先后保存同一个文件。这属于正常的并发写入，不应报冲突；但同步
        软件在外部替换文件时，又必须先把对方的新内容读进来，避免这次写入把
        对方新增的记录整体覆盖掉。两种情况都用“重新读取后再合并”处理：
        调用方把自己的记录覆盖回最新数据之上，其余记录原样保留。
        """
        if self.status == "CORRUPT":
            return False
        current = self._signature()
        if current is None:
            # 文件被删除（例如同步工具正在替换）：按空库重新读取。
            if self.loaded_signature is not None:
                self.load()
                return True
            return False
        if current == self.loaded_signature:
            return False
        self.load()
        return True

    # -- 记录访问 ----------------------------------------------------------
    @property
    def plugins(self) -> dict:
        return self.data.setdefault("plugins", {})

    @property
    def categories(self) -> list:
        return self.data.setdefault("categories", [dict(DEFAULT_CATEGORY)])

    def get(self, plugin_id: str) -> dict | None:
        return self.plugins.get(plugin_id)

    def set_plugin(self, plugin_id: str, record: dict) -> dict:
        unknown = set(record) - PORTABLE_FIELDS
        if unknown:
            raise InvalidCatalogFieldError(
                "本机字段不能写入总资料库: " + ", ".join(sorted(unknown))
            )
        clean = {field: record[field] for field in PORTABLE_FIELDS if field in record}
        clean["plugin_id"] = plugin_id
        self.plugins[plugin_id] = clean
        return clean

    def replace_plugins(self, plugins: dict) -> None:
        for plugin_id, record in plugins.items():
            self.set_plugin(plugin_id, record)
