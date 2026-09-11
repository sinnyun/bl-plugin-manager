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
def _repo_index_files() -> list[tuple[str, str]]:
    """返回 [(仓库名, index.json 路径)]。"""
    out = []
    try:
        repos = bpy.context.preferences.extensions.repos
    except Exception:
        return out
    for repo in repos:
        directory = getattr(repo, "custom_directory", "") or getattr(repo, "directory", "")
        if not directory:
            continue
        path = os.path.join(directory, ".blender_ext", "index.json")
        if os.path.isfile(path):
            out.append((getattr(repo, "name", "") or getattr(repo, "module", ""), path))
    return out


def read_catalog() -> list[dict]:
    """合并所有仓库索引，返回可用扩展列表（同 id 取版本最高者）。"""
    best: dict[str, dict] = {}
    for repo_name, path in _repo_index_files():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for item in (data.get("data") or []):
            pid = item.get("id")
            if not pid or not item.get("archive_url"):
                continue
            pkg = {
                "id": pid,
                "name": item.get("name") or pid,
                "version": scan.version_str(item.get("version")),
                "tagline": item.get("tagline", ""),
                "tags": [str(t) for t in (item.get("tags") or [])],
                "type": item.get("type", ""),
                "archive_url": item.get("archive_url", ""),
                "archive_hash": item.get("archive_hash", ""),
                "archive_size": int(item.get("archive_size") or 0),
                "blender_version_min": scan.version_str(item.get("blender_version_min")),
                "website": item.get("website", ""),
                "maintainer": item.get("maintainer", ""),
                "license": item.get("license") or [],
                "repo": repo_name,
            }
            cur = best.get(pid)
            if cur is None or scan.compare_versions(pkg["version"], cur["version"]) > 0:
                best[pid] = pkg
    return sorted(best.values(), key=lambda p: (p["name"] or "").lower())


def is_compatible(pkg: dict, blender_version: tuple | None = None) -> bool:
    """该扩展要求的最低 Blender 版本是否满足当前版本。"""
    if blender_version is None:
        blender_version = bpy.app.version
    need = scan.version_key(pkg.get("blender_version_min"))
    if not any(need):
        return True
    need = need[: len(blender_version)]
    cur = tuple(blender_version[: len(need)])
    return need <= cur


def installed_map(db: LibraryDB) -> dict:
    """id / 文件夹名 → 库内记录，用于判断"已安装"。"""
    m = {}
    for rec in db.plugins.values():
        if rec.get("id"):
            m[str(rec["id"]).lower()] = rec
        if rec.get("folder_name"):
            m[str(rec["folder_name"]).lower()] = rec
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
                    "installed_version": (rec or {}).get("version", "")})
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
             progress=None) -> tuple[bool, str]:
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
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
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
def install_package(pkg: dict, root: str, db: LibraryDB, enable: bool = False,
                    progress=None) -> tuple[bool, str, dict | None]:
    """从商店安装一个扩展到插件库。返回 (成功, 错误, 记录)。"""
    tmp = tempfile.mktemp(prefix="pm_store_", suffix=".zip")
    try:
        ok, err = download(pkg.get("archive_url", ""), tmp,
                           pkg.get("archive_hash", ""), int(pkg.get("archive_size") or 0),
                           progress)
        if not ok:
            return False, err, None
        rec = library.import_zip(tmp, root, db, enable=enable,
                                 origin="在线商店", origin_path=pkg.get("website", ""))
        if pkg.get("id") and not rec.get("id"):
            rec["id"] = pkg["id"]
        rec["source"] = "store"
        rec["source_url"] = pkg.get("website", "") or rec.get("source_url", "")
        db.upsert(rec["key"], rec)
        db.save()
        return True, "", rec
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", None
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass


def update_record(rec: dict, pkg: dict, root: str, db: LibraryDB,
                  progress=None, defer_refresh: bool = False) -> tuple[bool, str]:
    """把库中已有插件更新到 pkg 指定的新版本（保留分类/备注/启用/自启）。

    defer_refresh=True 供批量更新使用：不逐个刷新 Blender，由调用方最后统一刷新。
    """
    tmp = tempfile.mktemp(prefix="pm_upd_", suffix=".zip")
    try:
        ok, err = download(pkg.get("archive_url", ""), tmp,
                           pkg.get("archive_hash", ""), int(pkg.get("archive_size") or 0),
                           progress)
        if not ok:
            return False, err
        new_rec = library.replace_from_zip(rec, tmp, root, db,
                                           version=pkg.get("version", ""),
                                           defer_refresh=defer_refresh)
        return True, new_rec.get("version", "")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass


def sync_indexes() -> tuple[bool, str]:
    """联网刷新各仓库索引（Blender 原生机制）。"""
    try:
        bpy.ops.extensions.repo_sync_all()
        return True, ""
    except Exception as exc:
        return False, f"同步索引失败: {exc}"
