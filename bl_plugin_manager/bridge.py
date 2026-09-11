"""Blender 桥接层：把插件库注册为脚本目录 / 扩展仓库，并提供启停与模块解析。"""

from __future__ import annotations

import os
import json
import sys

import addon_utils
import bpy

from . import constants as C


# ---------------------------------------------------------------------------
# 偏好与目录
# ---------------------------------------------------------------------------
def get_prefs():
    """取本插件自己的偏好设置；未启用时返回 None。"""
    try:
        entry = bpy.context.preferences.addons.get(C.ADDON_ID)
    except AttributeError:
        return None
    return entry.preferences if entry else None


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path or "")))


def library_dirs(root: str) -> dict:
    root = os.path.abspath(root)
    return {
        "root": root,
        "addons": os.path.join(root, C.DIR_ADDONS),
        "extensions": os.path.join(root, C.DIR_EXTENSIONS),
        "inbox": os.path.join(root, C.DIR_INBOX),
        "trash": os.path.join(root, C.DIR_TRASH),
        "meta": os.path.join(root, C.DIR_META),
        "backups": os.path.join(root, C.DIR_BACKUPS),
    }


def ensure_library_dirs(root: str) -> dict:
    dirs = library_dirs(root)
    for key in ("root", "addons", "extensions", "inbox", "trash", "meta", "backups"):
        os.makedirs(dirs[key], exist_ok=True)
    return dirs


# ---------------------------------------------------------------------------
# 注册状态查询
# ---------------------------------------------------------------------------
def _script_dirs():
    return bpy.context.preferences.filepaths.script_directories


def find_script_dir(root: str):
    target = _norm(root)
    for i, item in enumerate(_script_dirs()):
        if _norm(item.directory) == target:
            return i, item
    return -1, None


def find_repo(module: str = C.REPO_MODULE, directory: str = ""):
    repos = bpy.context.preferences.extensions.repos
    for i, repo in enumerate(repos):
        if repo.module == module:
            return i, repo
    if directory:
        target = _norm(directory)
        for i, repo in enumerate(repos):
            if _norm(getattr(repo, "custom_directory", "") or repo.directory) == target:
                return i, repo
    return -1, None


def _repo_directory(repo) -> str:
    return getattr(repo, "custom_directory", "") or getattr(repo, "directory", "")


def _repo_attrs(repo) -> dict:
    """Capture user-visible repository settings before an explicit rewire."""
    attrs = {"module": getattr(repo, "module", ""),
             "name": getattr(repo, "name", ""),
             "directory": getattr(repo, "directory", ""),
             "custom_directory": getattr(repo, "custom_directory", ""),
             "remote_url": getattr(repo, "remote_url", ""),
             "enabled": bool(getattr(repo, "enabled", True)),
             "use_custom_directory": bool(getattr(repo, "use_custom_directory", False)),
             "use_remote_url": bool(getattr(repo, "use_remote_url", False))}
    return attrs


def _official_snapshot_path(root: str) -> str:
    return os.path.join(library_dirs(root)["meta"], "official_repo_backup.json")


def capture_official_repo_state(root: str) -> dict:
    """Persist the official repository configuration for a reversible unification."""
    _idx, repo = find_official_repo()
    state = {"exists": repo is not None,
             "repo": _repo_attrs(repo) if repo is not None else None}
    os.makedirs(library_dirs(root)["meta"], exist_ok=True)
    path = _official_snapshot_path(root)
    if not os.path.isfile(path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
    return state


def restore_official_repo_state(root: str) -> tuple[bool, str]:
    """Restore an official repository snapshot created by explicit unification."""
    path = _official_snapshot_path(root)
    if not os.path.isfile(path):
        return True, ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        _idx, current = find_official_repo()
        target = os.path.join(os.path.abspath(root), C.DIR_EXTENSIONS)
        current_points_here = current is not None and _norm(_repo_directory(current)) == _norm(target)
        if not current_points_here:
            return True, ""
        original = state.get("repo") if state.get("exists") else None
        if original is None:
            if current is not None:
                bpy.context.preferences.extensions.repos.remove(current)
        else:
            for key in ("name", "directory", "custom_directory", "remote_url",
                        "enabled", "use_custom_directory", "use_remote_url"):
                if key in original:
                    try:
                        setattr(current, key, original[key])
                    except Exception:
                        pass
        os.remove(path)
        return True, ""
    except Exception as exc:
        return False, f"恢复官方仓库配置失败: {exc}"


# 官方商店仓库的模块名（Blender 内置，指向 extensions.blender.org）
OFFICIAL_REPO_MODULE = "blender_org"
OFFICIAL_SOURCE_URL = "https://extensions.blender.org/api/v1/extensions/"


def find_official_repo():
    """找到官方商店仓库（blender_org）。"""
    return find_repo(module=OFFICIAL_REPO_MODULE)


def unify_store_with_library(root: str) -> dict:
    """让 Blender 官方商店仓库直接使用插件库的 extensions 目录。

    这样「Blender 自己的商店面板」与「本插件库」操作的是同一批文件：
    在官方面板里下载/更新扩展，就是在插件库中管理；两边不会重复。
    同时移除本插件早年另建的 pmlib 仓库（避免两个来源指向同一目录）。
    """
    dirs = library_dirs(root)
    ext_dir = dirs["extensions"]
    os.makedirs(ext_dir, exist_ok=True)
    out = {"official": False, "removed_pmlib": False, "online": False}

    # 1) 官方商店仓库指向库的 extensions. 这是显式操作，先保存原配置。
    idx, repo = find_official_repo()
    target = _norm(ext_dir)
    if repo is None or _norm(_repo_directory(repo)) != target:
        try:
            capture_official_repo_state(root)
        except Exception as exc:
            print("[插件库] 保存官方仓库配置失败:", exc)
    if repo is not None:
        try:
            repo.enabled = True
            repo.use_remote_url = True
            repo.remote_url = OFFICIAL_SOURCE_URL
            repo.use_custom_directory = True
            repo.custom_directory = ext_dir
            out["official"] = True
        except Exception as exc:
            print("[插件库] 设置官方商店仓库失败:", exc)
    else:
        # 没有则新建一个（等价于官方商店）
        try:
            repo = bpy.context.preferences.extensions.repos.new(
                name="extensions.blender.org", module=OFFICIAL_REPO_MODULE)
            repo.use_remote_url = True
            repo.remote_url = OFFICIAL_SOURCE_URL
            repo.use_custom_directory = True
            repo.custom_directory = ext_dir
            repo.enabled = True
            out["official"] = True
        except Exception as exc:
            print("[插件库] 创建官方商店仓库失败:", exc)

    # 2) 移除自家另建的 pmlib 仓库（已由官方面板/本插件共用同一目录）
    for r in list(bpy.context.preferences.extensions.repos):
        if getattr(r, "module", "") == C.REPO_MODULE:
            try:
                bpy.context.preferences.extensions.repos.remove(r)
                out["removed_pmlib"] = True
            except Exception:
                pass

    # 3) 开启"允许联网访问"（安装/更新扩展所需）
    try:
        bpy.context.preferences.system.use_online_access = True
        out["online"] = bool(bpy.context.preferences.system.use_online_access)
    except Exception as exc:
        print("[插件库] 开启联网访问失败:", exc)

    refresh_blender()
    return out


def library_state(root: str, force: bool = False) -> dict:
    """库的挂载状态。

    * script_dir：库根是否已注册为 Blender 脚本目录；
    * repo      ：是否有扩展仓库指向库的 extensions（官方商店仓库也算）；
    * official  ：指向库 extensions 的是否正是「官方商店」仓库（这样官方面板
                  与本插件共用同一目录，下载/更新都进库管理）。

    面板 draw 会频繁调用，故结果按「脚本目录数 + 仓库数」做廉价缓存。
    """
    if not root:
        return {"script_dir": False, "repo": False, "official": False}

    try:
        script_sig = tuple(sorted(
            (_norm(getattr(item, "directory", "")), getattr(item, "name", ""))
            for item in bpy.context.preferences.filepaths.script_directories
        ))
        repo_sig = tuple(sorted(
            (getattr(repo, "module", ""), _norm(_repo_directory(repo)),
             bool(getattr(repo, "enabled", True)))
            for repo in bpy.context.preferences.extensions.repos
        ))
        stamp = (script_sig, repo_sig)
    except Exception:
        stamp = None
    global _STATE_CACHE
    if not force and stamp is not None:
        hit = _STATE_CACHE.get(root)
        if hit and hit[0] == stamp:
            return hit[1]

    dirs = library_dirs(root)
    si, _ = find_script_dir(dirs["root"])
    ri, repo = find_repo(module="", directory=dirs["extensions"])
    official = bool(repo is not None
                    and getattr(repo, "module", "") == OFFICIAL_REPO_MODULE)
    state = {"script_dir": si >= 0, "repo": ri >= 0, "official": official}
    if stamp is not None:
        _STATE_CACHE[root] = (stamp, state)
    return state


_STATE_CACHE: dict = {}


def invalidate_state_cache() -> None:
    _STATE_CACHE.clear()


def is_registered(root: str) -> bool:
    state = library_state(root)
    return state["script_dir"] and state["repo"]


# ---------------------------------------------------------------------------
# 注册 / 注销
# ---------------------------------------------------------------------------
def register_library(root: str, save: bool = True) -> dict:
    """把插件库挂到 Blender 上：库根作为脚本目录，extensions 作为扩展仓库。

    普通挂载只使用本插件自己的本地 pmlib 仓库，不会隐式改写 Blender
    官方商店仓库；需要让官方商店与库共用目录时，使用显式的「统一商店」操作。
    """
    dirs = ensure_library_dirs(root)
    os.makedirs(dirs["extensions"], exist_ok=True)

    si, _ = find_script_dir(dirs["root"])
    if si < 0:
        item = _script_dirs().new()
        item.name = f"{C.ADDON_NAME} - {os.path.basename(dirs['root'])}"
        item.directory = dirs["root"]

    # 普通挂载不得隐式接管官方商店。复用已经指向本库的仓库，
    # 否则使用/创建本插件自己的 pmlib 仓库。
    ri, repo = find_repo(module="", directory=dirs["extensions"])
    if ri < 0:
        _, repo = find_repo(module=C.REPO_MODULE)
    if repo is None:
        repo = bpy.context.preferences.extensions.repos.new(
            name=C.REPO_NAME, module=C.REPO_MODULE
        )
    repo_module = getattr(repo, "module", "")
    if repo_module == C.REPO_MODULE:
        repo.enabled = True
        repo.use_custom_directory = True
        repo.custom_directory = dirs["extensions"]
        # The pmlib repository is a local index used for module discovery.
        # Do not give it the official remote URL: Blender's global sync then
        # tries to refresh an uninitialized local cache and reports a
        # misleading "not a known repo" error.  The explicit Store/Unify
        # actions own the official remote repository instead.
        repo.use_remote_url = False
        repo.remote_url = ""

    refresh_blender()
    if save:
        save_prefs()
    return library_state(dirs["root"])


def unregister_library(root: str, save: bool = True) -> None:
    """从 Blender 撤销挂载（注意：这两个集合的 remove 接收条目对象，不是索引）。"""
    dirs = library_dirs(root)
    _, sd_item = find_script_dir(dirs["root"])
    if sd_item is not None:
        try:
            _script_dirs().remove(sd_item)
        except Exception as exc:
            print("[插件库] 移除脚本目录失败:", exc)
    _, repo = find_repo(module=C.REPO_MODULE)
    if repo is not None and _norm(_repo_directory(repo)) == _norm(dirs["extensions"]):
        try:
            bpy.context.preferences.extensions.repos.remove(repo)
        except Exception as exc:
            print("[插件库] 移除扩展仓库失败:", exc)
    ok, err = restore_official_repo_state(root)
    if not ok:
        print("[插件库]", err)
    refresh_blender()
    if save:
        save_prefs()


def save_prefs() -> None:
    try:
        bpy.ops.wm.save_userpref()
    except Exception:
        pass


def refresh_blender(full: bool = True, deep: bool = False) -> None:
    """让 Blender 重新发现脚本与扩展。

    分级执行以降低卡顿：
    * full=True（默认）：重新发现模块 + 刷新脚本路径 + 失效模块索引缓存；
    * deep=True：额外刷新扩展仓库界面列表（较贵，仅在安装/移除扩展后需要）；
    * full=False：只失效内部缓存，不做重扫描（同一次批量操作内重复调用时用）。
    """
    if not full:
        invalidate_module_index()
        invalidate_state_cache()
        return

    for step in (
        lambda: bpy.utils.refresh_script_paths(),
        lambda: _refresh_addon_modules(),
    ):
        try:
            step()
        except Exception:
            pass

    invalidate_module_index()
    invalidate_state_cache()
    try:
        bpy.ops.preferences.addon_refresh()
    except Exception:
        pass

    if deep:
        # Blender 5.x expects a repository cache (``.blender_ext``) before
        # ``repo_refresh_all`` can operate on it.  A newly-created local
        # library has no cache yet, and invoking the operator in that state
        # only emits a traceback while doing no useful work.
        try:
            repos = list(bpy.context.preferences.extensions.repos)
            # ``repo_refresh_all`` operates on every repository.  If our
            # ordinary local pmlib repository has not acquired Blender's
            # cache yet, the all-repositories operator fails because it also
            # visits that path.  Leave refresh to ``repo_sync_all`` (the
            # explicit Store action) until a cache exists.
            local_repo_uninitialized = any(
                getattr(repo, "module", "") == C.REPO_MODULE
                and not os.path.isdir(os.path.join(_repo_directory(repo), ".blender_ext"))
                for repo in repos if _repo_directory(repo)
            )
            known_repo = any(
                os.path.isdir(os.path.join(_repo_directory(repo), ".blender_ext"))
                for repo in repos if _repo_directory(repo)
            )
        except Exception:
            local_repo_uninitialized = True
            known_repo = False
        if known_repo and not local_repo_uninitialized:
            try:
                bpy.ops.extensions.repo_refresh_all()
            except Exception:
                pass


def _refresh_addon_modules() -> None:
    """Refresh Blender's fake-module index and discard deleted paths first.

    Blender keeps fake modules in a process-wide cache.  If a library is
    replaced with another temporary/library path using the same folder name,
    ``addon_utils.modules_refresh`` treats it as a duplicate and keeps the
    stale path.  Removing entries whose source file no longer exists lets
    Blender discover the current copy without affecting live add-ons.
    """
    try:
        cache = getattr(addon_utils, "addons_fake_modules", None)
        if cache is not None:
            for name, mod in list(cache.items()):
                path = getattr(mod, "__file__", "")
                if path and not os.path.exists(path):
                    cache.pop(name, None)
                    # ``addon_utils.enable`` imports through sys.modules;
                    # leaving a deleted module there makes it call
                    # os.path.getmtime on the old path and fail with
                    # WinError 3 even after the fake-module cache refreshes.
                    sys.modules.pop(name, None)
        addon_utils.modules_refresh()
    except Exception:
        # Keep the normal refresh path best-effort, as it was before.
        try:
            addon_utils.modules_refresh()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# 模块解析与启停
# ---------------------------------------------------------------------------
# 模块索引缓存：模块名/路径解析在批量操作中会被反复调用，
# 每次重建都会遍历全部已安装插件（152 个约 50ms，累计可达数秒）。
_MODULE_INDEX: dict = {}
_MODULE_INDEX_STAMP = None


def _module_index(refresh: bool = False) -> dict:
    """path(normcase) -> module_name，取自 Blender 已发现的插件模块。

    带缓存：同一"模块发现代次"内直接复用，避免批量解析时重复全量遍历。
    """
    global _MODULE_INDEX, _MODULE_INDEX_STAMP
    stamp = _modules_stamp()
    if not refresh and _MODULE_INDEX and stamp == _MODULE_INDEX_STAMP:
        return _MODULE_INDEX

    index = {}
    try:
        for mod in addon_utils.modules():
            f = getattr(mod, "__file__", None)
            if not f:
                continue
            index[_norm(os.path.dirname(f))] = mod.__name__
    except Exception:
        pass
    _MODULE_INDEX = index
    _MODULE_INDEX_STAMP = stamp
    return index


def _modules_stamp():
    """模块发现代次（廉价判定）。

    刻意不调用 addon_utils.modules()——那正是要避免的开销。
    代次包含脚本目录的完整路径/名称和当前已启用模块，避免不同库复用
    同名插件时因数量相同而错误命中旧路径缓存。
    """
    try:
        paths = tuple(sorted(
            (_norm(getattr(item, "directory", "")), getattr(item, "name", ""))
            for item in bpy.context.preferences.filepaths.script_directories
        ))
    except Exception:
        paths = ()
    try:
        enabled = tuple(sorted(a.module for a in bpy.context.preferences.addons))
    except Exception:
        enabled = ()
    return paths, enabled


def invalidate_module_index() -> None:
    """在真正需要重新发现模块（安装/移除插件、改脚本路径）后调用。"""
    global _MODULE_INDEX_STAMP
    _MODULE_INDEX_STAMP = None
    _MODULE_INDEX.clear()


def resolve_module(plugin_dir: str, kind: str, pkg_id: str = "") -> str:
    """解析插件对应的 Python 模块名（Blender 启用状态用的就是这个名字）。

    注意：扩展插件的模块名是 ``bl_ext.<仓库模块>.<插件文件夹名>``，
    以**目录名**为准，而不是 manifest 里的 id（例如文件夹 ``快速打组`` 的
    manifest id 是 ``GroupPro``，但 Blender 实际加载为
    ``bl_ext.<repo>.快速打组``）。
    """
    index = _module_index()
    found = index.get(_norm(plugin_dir))
    if found:
        return found
    folder = os.path.basename(os.path.normpath(plugin_dir))
    if kind == C.KIND_EXTENSION:
        # 扩展模块名 = bl_ext.<实际仓库模块>.<目录名>；仓库可能是官方商店
        # （blender_org）或旧的自定义仓库，因此动态取"指向本库目录的仓库"。
        return f"bl_ext.{_active_repo_module()}.{folder}"
    return folder


def _active_repo_module() -> str:
    """指向插件库 extensions 的仓库模块名（官方商店优先，其次任意自定义仓库）。

    不能写死常量：早期用 pmlib，后来统一为官方商店的 blender_org，
    写死会导致模块名解析错误（表现为"repository 不存在"）。
    """
    try:
        prefs = bpy.context.preferences
        target = ""
        gp = getattr(prefs, "addons", None)
        entry = gp.get(C.ADDON_ID) if gp else None
        if entry is not None:
            target = _norm(getattr(entry.preferences, "library_path", "") or "")
        if target:
            ext_dir = _norm(os.path.join(target, C.DIR_EXTENSIONS))
            for r in prefs.extensions.repos:
                d = getattr(r, "custom_directory", "") or getattr(r, "directory", "")
                if d and _norm(d) == ext_dir:
                    return getattr(r, "module", C.REPO_MODULE)
        # 退而求其次：找官方商店仓库
        for r in prefs.extensions.repos:
            if getattr(r, "module", "") == OFFICIAL_REPO_MODULE:
                return OFFICIAL_REPO_MODULE
    except Exception:
        pass
    return OFFICIAL_REPO_MODULE


def enabled_modules() -> set:
    """当前已启用的模块名集合（一次遍历，供批量操作使用）。"""
    try:
        return {a.module for a in bpy.context.preferences.addons}
    except Exception:
        return set()


def is_module_enabled(module: str) -> bool:
    try:
        return any(a.module == module for a in bpy.context.preferences.addons)
    except Exception:
        return False


def _residue_modules(module: str) -> tuple[str, ...]:
    """该插件的"归属模块名"集合。

    插件可能捆绑独立的顶层子模块（例如 Cats 捆绑 mmd_tools_local），
    这些子模块的类 __module__ 不带插件名前缀，必须单独列出才能统计到。
    """
    names = {module}
    # Cats 特例：bundled mmd_tools_local
    bundled = {
        "cats-blender-plugin-master": ("mmd_tools_local",),
    }
    names.update(bundled.get(module, ()))
    return tuple(names)


def module_residue(module: str) -> int:
    """统计 bpy.types 中仍注册着、却属于该插件的类数量。

    部分插件 register 到一半抛异常时，Blender 不会自动回滚已注册的类，
    于是面板/属性残留下来、每次重绘都报错，直到重启 Blender 才清。
    这里用于检测这种"部分注册残留"。
    """
    if not module:
        return 0
    n = 0
    try:
        owners = _residue_modules(module)
        for name in dir(bpy.types):
            cls = getattr(bpy.types, name, None)
            m = getattr(cls, "__module__", "") or ""
            for own in owners:
                if m == own or m.startswith(own + "."):
                    n += 1
                    break
    except Exception:
        pass
    return n


def _force_unregister_classes(module: str) -> int:
    """强制注销归属该插件的残留类（不依赖插件自己的 unregister）。

    Blender 在插件 register 中途报错时不会回滚，残留的 Operator/Panel/
    PropertyGroup 会一直报错。这里按类逐个注销，能把大部分残留清掉，
    从而避免"必须重启 Blender"。
    """
    owners = _residue_modules(module)
    done = 0
    for name in list(dir(bpy.types)):
        cls = getattr(bpy.types, name, None)
        m = getattr(cls, "__module__", "") or ""
        if not any(m == o or m.startswith(o + ".") for o in owners):
            continue
        try:
            bpy.utils.unregister_class(cls)
            done += 1
        except Exception:
            pass
    return done


def cleanup_residue(module: str) -> tuple[int, str]:
    """尽力清理某插件的部分注册残留，返回 (剩余残留数, 错误信息)。

    分两步：
    1. 走正常卸载流程（调用插件自己的 unregister，能清干净最好）；
    2. 若仍有残留（插件 unregister 也报错），逐个强制注销残留类。
    """
    err = ""
    try:
        addon_utils.disable(module, default_set=False)
    except Exception as exc:
        err = str(exc)
    try:
        entry = bpy.context.preferences.addons.get(module)
        if entry is not None:
            bpy.context.preferences.addons.remove(entry)
    except Exception:
        pass

    left = module_residue(module)
    if left:
        # 插件自身卸载不干净 → 强制逐个注销
        _force_unregister_classes(module)
        try:
            addon_utils.modules_refresh()
        except Exception:
            pass
        left = module_residue(module)

    try:
        invalidate_module_index()
    except Exception:
        pass
    return left, err


def set_enabled(module: str, enabled: bool) -> tuple[bool, str]:
    """启停插件，返回 (成功, 错误信息)。

    优先用偏好操作符（会正确写入 preferences.addons），失败再退回 addon_utils；
    最后以真实启用状态为准——部分插件 register 阶段会抛异常，Blender 不保证回滚。
    """
    if not module:
        return False, "无法解析模块名"

    err = ""
    try:
        if enabled:
            bpy.ops.preferences.addon_enable(module=module)
        else:
            bpy.ops.preferences.addon_disable(module=module)
    except Exception as exc:
        err = str(exc)
        try:
            if enabled:
                addon_utils.enable(module, default_set=False)
            else:
                addon_utils.disable(module, default_set=False)
        except Exception as exc2:
            err = f"{err} / {exc2}"

    if is_module_enabled(module) == enabled:
        return True, ""

    # 失败：尽量清理"注册到一半"的残留，避免面板持续报错
    base = f"插件内部报错: {err}" if err else "状态未生效"
    if enabled:
        left, _cerr = cleanup_residue(module)
        if left:
            return False, f"{base}；注册残留 {left} 项，需重启 Blender 清理"
        return False, base
    return False, base
