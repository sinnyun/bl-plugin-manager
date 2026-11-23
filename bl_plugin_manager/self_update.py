"""GitHub Release updates for the manager's own legacy add-on package."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


PACKAGE = "bl_plugin_manager"
REPOSITORY = "sinnyun/bl-plugin-manager"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
MAX_ZIP_SIZE = 16 * 1024 * 1024
_TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


@dataclass(frozen=True)
class Release:
    version: tuple[int, int, int]
    tag: str
    url: str
    digest: str
    size: int


def release_offer(data: dict, current: tuple) -> Release | None:
    if not isinstance(data, dict):
        raise ValueError("GitHub 返回的发布信息格式不正确")
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    match = _TAG.fullmatch(tag)
    if not match:
        raise ValueError("GitHub 正式版本标签应采用 v主版本.次版本.修订号")
    version = tuple(map(int, match.groups()))
    if version <= current:
        return None
    name = f"{PACKAGE}-{tag[1:]}.zip"
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise ValueError("GitHub Release 缺少 ZIP 安装包")
    expected_url = f"{RELEASES_URL}/download/{tag}/{name}"
    for asset in assets:
        if (not isinstance(asset, dict) or asset.get("name") != name
                or asset.get("state") != "uploaded"):
            continue
        if asset.get("browser_download_url") != expected_url:
            raise ValueError("安装包地址与项目的 GitHub Release 不一致")
        digest = str(asset.get("digest") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("安装包缺少 GitHub 提供的 SHA-256 校验值")
        size = asset.get("size")
        if type(size) is not int or not (0 < size <= MAX_ZIP_SIZE):
            raise ValueError("安装包大小不在允许范围内")
        return Release(version, tag, expected_url, digest, size)
    raise ValueError(f"GitHub Release 缺少安装包 {name}")


def latest_release(current: tuple) -> Release | None:
    request = Request(
        f"https://api.github.com/repos/{REPOSITORY}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": PACKAGE,
                 "X-GitHub-Api-Version": "2026-03-10"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read(1024 * 1024 + 1)
    except HTTPError as error:
        if error.code == 404:
            return None
        if error.code in {403, 429}:
            raise RuntimeError("GitHub 暂时限制访问，请稍后重试") from error
        raise
    if len(raw) > 1024 * 1024:
        raise ValueError("GitHub 发布信息超过大小限制")
    return release_offer(json.loads(raw), current)


def pending_path(installation: Path) -> Path:
    return installation.with_name(f".{PACKAGE}-pending")


def installed_path(module_file: str, user_scripts: str) -> Path:
    installation = Path(module_file).parent.resolve()
    if not user_scripts or installation != (Path(user_scripts) / "addons").resolve() / PACKAGE:
        raise ValueError("请先把插件安装到 Blender 用户插件目录，再使用 GitHub 更新")
    return installation


def _package_version(package: Path, blender_version: tuple) -> tuple:
    def literal(path, name):
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in statement.targets
            ):
                return ast.literal_eval(statement.value)
        raise ValueError(f"安装包缺少 {name}")

    def valid_version(value):
        return (isinstance(value, tuple) and len(value) == 3
                and all(type(part) is int and part >= 0 for part in value))

    info = literal(package / "__init__.py", "bl_info")
    version = literal(package / "constants.py", "ADDON_VERSION")
    if not valid_version(version) or not isinstance(info, dict) or info.get("version") != version:
        raise ValueError("安装包的版本信息不一致")
    minimum = info.get("blender")
    if not valid_version(minimum) or minimum > blender_version:
        raise ValueError("安装包不支持当前 Blender 版本")
    return version


def stage_update(installation: Path, archive: Path, release: Release, blender_version: tuple,
                 cancelled=None) -> None:
    from .security.archive import extract_archive

    if archive.stat().st_size != release.size:
        raise ValueError("GitHub 安装包大小校验失败")
    if "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest() != release.digest:
        raise ValueError("GitHub 安装包 SHA-256 校验失败")
    pending = pending_path(installation)
    if pending.exists():
        raise ValueError("已有更新等待安装，请先重启 Blender")
    with tempfile.TemporaryDirectory(prefix=f".{PACKAGE}-stage-", dir=installation.parent) as stage:
        root = Path(stage)
        extract_archive(archive, root, max_total_bytes=MAX_ZIP_SIZE * 2)
        package = root / PACKAGE
        if list(root.iterdir()) != [package] or not package.is_dir():
            raise ValueError("ZIP 必须只包含插件安装目录")
        if _package_version(package, blender_version) != release.version:
            raise ValueError("安装包版本与 GitHub 标签不一致")
        for file in package.rglob("*.py"):
            compile(file.read_bytes(), str(file), "exec")
        if not (cancelled and cancelled()):
            os.rename(package, pending)


def activate_pending(installation: Path, current: tuple, blender_version: tuple) -> Path | None:
    pending = pending_path(installation)
    if not pending.is_dir() or pending.is_symlink():
        return None
    if _package_version(pending, blender_version) <= current:
        return None
    backup = installation.with_name(f".{PACKAGE}-previous-{'-'.join(map(str, current))}-{uuid.uuid4().hex[:8]}")
    os.replace(installation, backup)
    try:
        os.replace(pending, installation)
    except OSError:
        os.replace(backup, installation)
        raise
    return backup


def restore_installation(installation: Path, backup: Path) -> None:
    failed = installation.with_name(f".{PACKAGE}-failed-{uuid.uuid4().hex[:8]}")
    os.replace(installation, failed)
    try:
        os.replace(backup, installation)
    except OSError:
        os.replace(failed, installation)
        raise


def load_replacement(module, installation: Path, backup: Path) -> None:
    """Reload only during the initial import, before Blender registers the add-on."""
    import importlib
    import sys

    def reload_package():
        prefix = module.__name__ + "."
        for name in tuple(sys.modules):
            if name.startswith(prefix):
                sys.modules.pop(name, None)
                module.__dict__.pop(name.removeprefix(prefix).split(".")[0], None)
        module.__dict__.pop("bpy", None)
        importlib.invalidate_caches()
        importlib.reload(module)

    try:
        reload_package()
        if not callable(getattr(module, "register", None)):
            raise ValueError("安装包缺少 Blender 注册入口")
    except Exception as error:
        restore_installation(installation, backup)
        reload_package()
        module._update_boot_message = f"新版加载失败，已恢复旧版：{error}"
