"""总资料库的跨电脑匹配：把本机扫描结果连接到稳定 plugin_id。

这是“自动匹配电脑中安装了哪些插件，然后链接插件的信息”的核心逻辑。它不依赖
Blender，也不读写文件，因此可以在普通 Python 进程中完整单元测试。

匹配优先级（强 → 弱）
--------------------
1. 扩展 manifest id：扩展插件声明的 ``id`` 全局唯一，是最可靠的身份。
2. 插件声明名：``bl_info['name']`` / manifest ``name``。
3. 目录名：插件的安装位置（对传统插件而言也是模块名）。

每条记录会累积“身份线索”（names / folders），所以插件改名或目录改名后，只要
命中任一历史线索，仍会连回同一条记录。声明名与目录名都写入线索，因此“插件在
原目录改名”这类正常升级会连回原记录并刷新名称，而不会新建条目。

匹配严格区分插件类型（addon / extension），避免同名在两种类型间误连。

“目录名不同”有两种含义，靠**原目录是否仍在本次扫描中**区分：

* 原目录已消失 → 插件改名或搬家，按声明名连回原记录（跨电脑目录名不同的场景）；
* 原目录仍在   → 本机存在同一插件的第二个安装，另立条目，不抢占原记录的别名。

``LinkResult.assignment`` 提供 ``rel -> plugin_id``，供导入流程把落盘后的目录
映射回合并结果。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field


_SLUG_RE = re.compile(r"[^\w]+", re.UNICODE)


def slug(value: object) -> str:
    """把名称归一成稳定的比较键：小写、空白折叠、非字母数字折成连字符。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    return _SLUG_RE.sub("-", text).strip("-")


def plugin_id_for(kind: str, pkg_id: str = "", name: str = "", folder: str = "") -> str:
    """为一个新插件生成稳定、可读的 plugin_id。

    只在“确实没有匹配到既有记录”时用于创建新条目；已存在条目一律沿用原 id，
    因此这里对同一插件是否稳定并不影响既有数据的连续性。
    """
    if kind == "extension":
        base = pkg_id or name or folder
        prefix = "ext"
    else:
        base = name or folder
        prefix = "addon"
    return f"{prefix}:{slug(base) or 'unnamed'}"


@dataclass
class LinkResult:
    plugins: dict
    bindings: dict
    present: set = field(default_factory=set)
    assignment: dict = field(default_factory=dict)  # rel -> plugin_id
    added: int = 0
    matched: int = 0
    renamed: int = 0
    total: int = 0


def _record_hints(record: dict) -> list[tuple]:
    kind = record.get("kind") or ""
    hints: list[tuple] = []
    pkg_id = record.get("pkg_id") or record.get("id")
    if pkg_id:
        hints.append(("pkg", kind, str(pkg_id).lower()))
    for name in list(record.get("names") or []) + [record.get("name")]:
        key = slug(name)
        if key:
            hints.append(("name", kind, key))
    for folder in list(record.get("folders") or []) + [record.get("folder_name")]:
        key = slug(folder)
        if key:
            hints.append(("folder", kind, key))
    return hints


def _entry_hints(entry: dict) -> tuple[list[tuple], list[tuple]]:
    """返回 (强线索, 弱线索)。强线索=id/声明名；弱线索=目录名（安装位置）。"""
    kind = entry.get("kind") or ""
    strong: list[tuple] = []
    weak: list[tuple] = []
    pkg_id = entry.get("pkg_id") or entry.get("id")
    if pkg_id:
        strong.append(("pkg", kind, str(pkg_id).lower()))
    name = slug(entry.get("name"))
    if name:
        strong.append(("name", kind, name))
    folder = slug(entry.get("folder_name") or entry.get("folder"))
    if folder:
        weak.append(("folder", kind, folder))
    return strong, weak


def build_index(plugins: dict) -> dict:
    """线索 → plugin_id 索引。同一线索出现多次时以先出现者为准。"""
    index: dict[tuple, str] = {}
    for plugin_id, record in plugins.items():
        for hint in _record_hints(record):
            index.setdefault(hint, plugin_id)
    return index


# 扫描结果里可刷新到资料库的“插件自带/版本”字段。用户数据（别名、分类、标签、
# 备注、收藏、自启）永不在这里被覆盖。
_REFRESH_FIELDS = (
    "kind", "pkg_id", "id", "name", "version",
    "blender_min", "blender_max", "pkg_type", "author",
    "description", "auto_category", "auto_tags", "doc_url", "location",
)


def _merge_hints(record: dict, entry: dict) -> None:
    names = {slug(n) for n in (record.get("names") or []) if slug(n)}
    name = slug(entry.get("name"))
    if name:
        names.add(name)
    if names:
        record["names"] = sorted(names)
    # folders 保留原始写法（大小写有意义，便于人工排查），按 slug 去重
    folders = {slug(f): f for f in (record.get("folders") or []) if slug(f)}
    folder = entry.get("folder_name") or entry.get("folder")
    if folder and slug(folder):
        folders.setdefault(slug(folder), str(folder))
    if folders:
        record["folders"] = [folders[key] for key in sorted(folders)]

    pkg_id = entry.get("pkg_id") or entry.get("id")
    if pkg_id:
        record["pkg_id"] = str(pkg_id)
        record["id"] = str(pkg_id)


def link_scan(existing_plugins: dict, existing_bindings: dict,
              entries: list[dict], occupied_folders: set | None = None) -> LinkResult:
    """把本机扫描到的插件连接到既有资料库记录。

    ``bindings`` 是**累积**的：本机曾经安装但这次没扫描到的插件会保留绑定，
    从而保留其启用意图；只有显式移除插件时才删除绑定。

    ``occupied_folders`` 是“本机当前确实存在、且已被某条记录占用的目录名”
    （slug 形式）。用来区分两种“目录名与绑定不同”的情形：

    * 候选记录的目录仍存在于本机 → 这是同一插件的**第二个副本**，另立条目；
    * 候选记录的目录已不存在   → 插件改名/搬家，按声明名连回原记录。

    缺省为空集，适用于“本机尚无既有安装”的场景（如首次迁移）。
    """
    plugins = {pid: dict(rec) for pid, rec in existing_plugins.items()}
    bindings = {pid: rel for pid, rel in existing_bindings.items() if pid in plugins}
    index = build_index(plugins)

    result = LinkResult(plugins=plugins, bindings=bindings)
    claimed: set[str] = set()
    occupied = set(occupied_folders or ())

    def _bound_folder(plugin_id: str) -> str:
        rel = bindings.get(plugin_id) or ""
        return slug(rel.rsplit("/", 1)[-1]) if rel else ""

    for entry in entries:
        rel = entry.get("rel") or ""
        if not rel:
            continue
        result.total += 1
        match_id = None
        entry_folder = slug(entry.get("folder_name") or entry.get("folder"))
        strong, weak = _entry_hints(entry)

        # 强线索（manifest id / 声明名）只在原安装位置已不占用时才生效，
        # 否则同一台电脑上同名插件的第二个副本会错误抢占原记录的别名。
        for hint in strong:
            candidate = index.get(hint)
            if not candidate or candidate in claimed:
                continue
            bound = _bound_folder(candidate)
            if bound == entry_folder or bound not in occupied:
                match_id = candidate
                break

        # 目录名 = 安装位置（传统插件也是模块名）：同一目录即同一安装，
        # 覆盖“插件在原目录改名”这种正常升级。
        if match_id is None:
            for hint in weak:
                candidate = index.get(hint)
                if candidate and candidate not in claimed:
                    match_id = candidate
                    break

        if match_id is None:
            match_id = _allocate_id(plugin_id_for(
                entry.get("kind") or "", entry.get("pkg_id") or entry.get("id") or "",
                entry.get("name") or "", entry.get("folder_name") or entry.get("folder") or "",
            ), plugins)
            plugins[match_id] = {"plugin_id": match_id}
            result.added += 1
            previous_folder = ""
        else:
            result.matched += 1
            record = plugins[match_id]
            prior = record.get("folders") or (
                [record["folder_name"]] if record.get("folder_name") else [])
            previous_folder = slug(prior[0]) if prior else ""

        record = plugins[match_id]
        previous = record.get("name") or ""
        _merge_hints(record, entry)
        for field_name in _REFRESH_FIELDS:
            if field_name in entry and entry[field_name] not in (None, ""):
                record[field_name] = entry[field_name]
        # name 允许为空时不清空既有值（扫描读不到声明名时保留旧名）
        if not entry.get("name") and previous:
            record["name"] = previous
        record["plugin_id"] = match_id

        claimed.add(match_id)
        bindings[match_id] = rel
        result.present.add(match_id)
        result.assignment[rel] = match_id

        new_folder = slug(entry.get("folder_name") or "")
        if previous_folder and new_folder and previous_folder != new_folder:
            result.renamed += 1

        # 线索可能新增（例如首次拿到 manifest id），同步进索引供后续条目使用
        for hint in _record_hints(record):
            index.setdefault(hint, match_id)

    return result


def _allocate_id(base: str, plugins: dict) -> str:
    if base not in plugins:
        return base
    suffix = uuid.uuid4().hex[:8]
    candidate = f"{base}~{suffix}"
    while candidate in plugins:
        suffix = uuid.uuid4().hex[:8]
        candidate = f"{base}~{suffix}"
    return candidate


# 旧版 library.json 中可继承到总资料库的便携字段。旧文件以库内相对路径为键，
# 这里把它转换成 plugin_id 键并记下 rel 作为本机绑定与匹配线索。
_LEGACY_USER_FIELDS = (
    "display_name", "category", "category_id", "tags", "note",
    "favorite", "startup", "created_at", "updated_at",
)
_LEGACY_META_FIELDS = (
    "kind", "pkg_id", "id", "name", "version", "blender_min", "blender_max",
    "pkg_type", "author", "description", "auto_category", "auto_tags",
    "doc_url", "location",
)


def migrate_legacy(legacy_plugins: dict, categories: list | None = None) -> tuple[dict, dict]:
    """把旧版“按路径为键”的记录转成“按 plugin_id 为键”的总资料库记录。

    返回 ``(plugins, bindings)``：``bindings`` 为 ``plugin_id -> 旧相对路径``，
    让本机在首次扫描前仍能沿用自己的安装位置。旧记录里的别名、分类、备注、
    标签、收藏、自启都会完整保留。
    """
    plugins: dict[str, dict] = {}
    bindings: dict[str, str] = {}
    for rel, legacy in (legacy_plugins or {}).items():
        if not isinstance(legacy, dict):
            continue
        rel = str(legacy.get("rel") or rel or "").replace("\\", "/")
        kind = legacy.get("kind") or ("extension" if rel.startswith("extensions/") else "addon")
        folder = legacy.get("folder_name") or (rel.split("/")[-1] if rel else "")
        pkg_id = legacy.get("pkg_id") or legacy.get("id") or ""
        name = legacy.get("name") or folder
        plugin_id = _allocate_id(
            plugin_id_for(kind, str(pkg_id or ""), str(name or ""), str(folder or "")), plugins)

        record: dict = {"plugin_id": plugin_id, "kind": kind}
        for field_name in _LEGACY_META_FIELDS:
            value = legacy.get(field_name)
            if value not in (None, ""):
                record[field_name] = value
        for field_name in _LEGACY_USER_FIELDS:
            value = legacy.get(field_name)
            if value not in (None, "", []):
                record[field_name] = value

        names = {n for n in (legacy.get("names") or []) if n}
        if name:
            names.add(name)
        if names:
            record["names"] = sorted(names)
        folders = {f for f in (legacy.get("folders") or []) if f}
        if folder:
            folders.add(folder)
        if folders:
            record["folders"] = sorted(folders)

        plugins[plugin_id] = record
        if rel:
            bindings[plugin_id] = rel
    return plugins, bindings

