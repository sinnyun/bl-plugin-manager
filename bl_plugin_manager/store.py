"""在线扩展商店：浏览/搜索 extensions.blender.org，下载并安装或更新到插件库。

要点：
* 目录来自各扩展仓库缓存的 ``.blender_ext/index.json``（Blender 自带，
  通过 ``repo_sync_all`` 联网刷新），因此不额外抓取网页；
* 下载用官方 ``archive_url`` 直链（需带 User-Agent，否则 403）；
* 安装/更新都走插件库自己的导入流程，保证与库内其它插件一致管理。
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

import bpy

from . import constants as C, library, scan
from .db import LibraryDB

# 官方站点对缺 User-Agent 的请求返回 403
USER_AGENT = "Blender/" + ".".join(str(v) for v in bpy.app.version)
TIMEOUT = 40
CHUNK = 262144


# ---------------------------------------------------------------------------
# 目录
# ---------------------------------------------------------------------------
def _repo_index_files() -> list[tuple[str, str, str, str]]:
    out = []
    try:
        repos = bpy.context.preferences.extensions.repos
    except Exception:
        return out
    for repo in repos:
        if not getattr(repo, "enabled", True):
            continue
        directory = getattr(repo, "custom_directory", "") or getattr(repo, "directory", "")
        if not directory:
            continue
        path = os.path.join(directory, ".blender_ext", "index.json")
        if os.path.isfile(path):
            module = getattr(repo, "module", "")
            remote = getattr(repo, "remote_url", "") if getattr(repo, "use_remote_url", False) else ""
            out.append((remote or module, getattr(repo, "name", "") or module, remote, path))
    return out


def read_catalog(*, include_unavailable: bool = False) -> list[dict]:
    """Preserve offers from different repositories, even for the same extension ID."""
    best = {}
    for source, repo_name, remote, path in _repo_index_files():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            continue
        for item in data["data"]:
            if not isinstance(item, dict):
                continue
            pid = item.get("id")
            if not isinstance(pid, str) or not pid:
                continue
            archive = item.get("archive_url") or ""
            if not isinstance(archive, str):
                continue
            archive = urljoin(remote, archive) if remote and archive else archive
            try:
                if not isinstance(archive, str) or urlsplit(archive).scheme not in {"https", "http"}:
                    archive = ""
                size = int(item.get("archive_size") or 0)
            except (TypeError, ValueError):
                continue
            if not archive and not include_unavailable:
                continue
            pkg = {
                "id": pid,
                "name": str(item.get("name") or pid),
                "version": scan.version_str(item.get("version")),
                "tagline": str(item.get("tagline") or ""),
                "tags": [str(t) for t in item["tags"]] if isinstance(item.get("tags"), list) else [],
                "type": item.get("type", ""),
                "archive_url": archive,
                "archive_hash": str(item.get("archive_hash") or ""),
                "archive_size": size,
                "blender_version_min": scan.version_str(item.get("blender_version_min")),
                "blender_version_max": scan.version_str(item.get("blender_version_max")),
                "website": item.get("website", ""),
                "maintainer": str(item.get("maintainer") or ""),
                "license": item.get("license") or [],
                "repo": repo_name,
                "source": source,
            }
            key = (pid.casefold(), source)
            cur = best.get(key)
            if cur is None or scan.compare_versions(pkg["version"], cur["version"]) > 0:
                best[key] = pkg
    return sorted(best.values(), key=lambda p: (p["name"].casefold(), p["repo"].casefold()))


def is_compatible(pkg: dict, blender_version: tuple | None = None) -> bool:
    return scan.blender_compat(pkg.get("blender_version_min"), pkg.get("blender_version_max"),
                               blender_version)[0] not in {"too_old", "too_new"}


def installed_map(db: LibraryDB) -> dict:
    """id / 文件夹名 → 库内记录，用于判断"已安装"。"""
    m = {}
    for rec in db.plugins.values():
        package_id = rec.get("pkg_id") or rec.get("id")
        if rec.get("kind") == C.KIND_EXTENSION and not rec.get("missing") and package_id:
            m[str(package_id).casefold()] = rec
    return m


def search(catalog: list[dict], db: LibraryDB, query: str = "",
           only_compatible: bool = False, only_addons: bool = True,
           only_new: bool = False) -> list[dict]:
    """按关键字过滤目录，并标注 installed / installed_version。"""
    inst = installed_map(db)
    needle = (query or "").strip().lower()
    out = []
    for pkg in catalog:
        if only_addons and pkg.get("type") != "add-on":
            continue
        if only_compatible and not is_compatible(pkg):
            continue
        rec = inst.get(str(pkg["id"]).lower())
        if only_new and rec:
            continue
        if needle:
            hay = " ".join([
                pkg.get("name", ""), pkg.get("id", ""), pkg.get("tagline", ""),
                pkg.get("maintainer", ""), " ".join(pkg.get("tags", []) or []),
            ]).lower()
            if needle not in hay:
                continue
        out.append({**pkg,
                    "installed": bool(rec),
                    "installed_version": (rec or {}).get("version", ""),
                    "installed_source": (rec or {}).get("archive_source", "")})
    return out


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------
def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: str, expected_hash: str = "", expected_size: int = 0,
             progress=None, cancelled=None) -> tuple[bool, str]:
    """下载 archive_url 到 dest，校验大小与 sha256。返回 (成功, 错误)。"""
    if not url:
        return False, "缺少下载地址"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            total = int(resp.headers.get("Content-Length") or expected_size or 0)
            got = 0
            with open(dest, "wb") as f:
                while True:
                    if cancelled and cancelled():
                        return False, "下载已取消"
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    got += len(chunk)
                    if expected_size and got > expected_size:
                        return False, "下载大小超过仓库记录"
                    f.write(chunk)
                    if progress and total:
                        progress(min(99, int(got * 100 / total)))
    except urllib.error.HTTPError as exc:
        return False, f"服务器返回 {exc.code}（{exc.reason}）"
    except urllib.error.URLError as exc:
        return False, f"网络不可达: {exc.reason}"
    except Exception as exc:
        return False, f"下载失败: {type(exc).__name__}: {exc}"

    # 校验
    if expected_size and os.path.getsize(dest) != expected_size:
        return False, f"文件大小不符（期望 {expected_size}，实际 {os.path.getsize(dest)}）"
    if expected_hash.startswith("sha256:"):
        want = expected_hash.split(":", 1)[1].lower()
        real = _sha256_file(dest)
        if real != want:
            return False, f"sha256 校验失败（期望 {want[:12]}…，实际 {real[:12]}…）"
    if progress:
        progress(100)
    return True, ""


# ---------------------------------------------------------------------------
# 安装 / 更新
# ---------------------------------------------------------------------------
def download_package(pkg: dict, progress=None, cancelled=None) -> str:
    """Stage a verified download. The caller owns the returned temporary file."""
    with tempfile.NamedTemporaryFile(prefix="pm_store_", suffix=".zip", delete=False) as file:
        path = file.name
    try:
        ok, error = download(pkg.get("archive_url", ""), path,
                             pkg.get("archive_hash", ""), int(pkg.get("archive_size") or 0),
                             progress, cancelled)
        if not ok or (cancelled and cancelled()):
            raise RuntimeError(error or "下载已取消")
        return path
    except Exception:
        os.remove(path)
        raise


def apply_package(pkg: dict, path: str, root: str, db: LibraryDB,
                  record: dict | None = None, *, enable: bool = False) -> dict:
    if record is not None:
        return library.replace_from_zip(record, path, root, db, version=pkg.get("version", ""),
                                        archive_source=pkg.get("source", ""))
    rec = library.import_zip(path, root, db, enable=enable, origin="在线商店",
                             origin_path=pkg.get("website", ""), expected_id=pkg.get("id", ""),
                             expected_version=pkg.get("version", ""))
    rec["source_url"] = pkg.get("website", "") or rec.get("source_url", "")
    rec["archive_source"] = pkg.get("source", "")
    db.save()
    return rec


def install_package(pkg: dict, root: str, db: LibraryDB, enable: bool = False,
                    progress=None) -> tuple[bool, str, dict | None]:
    """Synchronous service entry used by the isolated store checks."""
    path = None
    try:
        path = download_package(pkg, progress)
        return True, "", apply_package(pkg, path, root, db, enable=enable)
    except Exception as exc:
        return False, str(exc), None
    finally:
        if path:
            os.remove(path)


def update_record(record: dict, pkg: dict, root: str, db: LibraryDB,
                  progress=None) -> tuple[bool, str]:
    """Run a confirmed update from a synchronous service caller."""
    path = None
    try:
        path = download_package(pkg, progress)
        updated = apply_package(pkg, path, root, db, record)
        return True, updated.get("version", "")
    except Exception as exc:
        return False, str(exc)
    finally:
        if path:
            os.remove(path)


def sync_indexes() -> tuple[bool, str]:
    """联网刷新各仓库索引（Blender 原生机制）。"""
    try:
        bpy.ops.extensions.repo_sync_all()
        return True, ""
    except Exception as exc:
        return False, f"同步索引失败: {exc}"
