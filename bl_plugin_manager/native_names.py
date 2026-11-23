"""Runtime-only display name overlay for Blender's native Add-ons & Get Extensions UI.

Never modifies any third-party plugin file (``__init__.py`` or
``blender_manifest.toml``) on disk and never leaves mutated metadata in
``addon_utils`` or ``bl_pkg`` caches after a draw call finishes.

Disabling the preference or unregistering this add-on restores Blender's
original functions immediately.
"""

from __future__ import annotations

import os
import sys

STYLE_ORIG_CN = "orig_cn"   # "Original Name (中文名)" — default
STYLE_CN_ORIG = "cn_orig"   # "中文名 (Original Name)"
STYLE_CN_ONLY = "cn_only"   # "中文名"

STYLE_ITEMS = (
    (STYLE_ORIG_CN, "英文原名 (中文名)", "先显示插件原名，再显示中文别名（兼顾排序与中英文搜索）"),
    (STYLE_CN_ORIG, "中文名 (英文原名)", "先显示中文别名，再显示插件原名（支持中英文搜索）"),
    (STYLE_CN_ONLY, "仅显示中文名", "列表中仅显示中文别名"),
)


def format_native_name(original: str, alias: str, style: str = STYLE_ORIG_CN) -> str:
    """Format a plugin's display name for Blender's native UI without duplication."""
    orig = (original or "").strip()
    cn = (alias or "").strip()
    if not cn:
        return orig
    if not orig:
        return cn
    if cn.casefold() == orig.casefold():
        return cn
    if style == STYLE_CN_ONLY:
        return cn
    if style == STYLE_CN_ORIG:
        if orig.casefold() in cn.casefold():
            return cn
        return f"{cn} ({orig})"
    # Default: STYLE_ORIG_CN — original name first, Chinese alias in parentheses.
    if cn.casefold() in orig.casefold():
        return orig
    if orig.casefold() in cn.casefold():
        return cn
    return f"{orig} ({cn})"


# ---------------------------------------------------------------------------
# Fast file-signature cache for module -> display_name lookup
# ---------------------------------------------------------------------------
_CACHE = {
    "sig": None,
    "by_module": {},
    "by_pkg_id": {},
}

_HOOK_STATE = {
    "installed": False,
    "addons_hooked": False,
    "extensions_hooked": False,
    "orig_addons_draw_items": None,
    "orig_ext_ui_visible": None,
    "bl_extension_ui": None,
    "error": "",
}


def invalidate_cache() -> None:
    _CACHE["sig"] = None
    _CACHE["by_module"] = {}
    _CACHE["by_pkg_id"] = {}


def _stat_sig(path: str):
    if not path:
        return None
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _load_alias_maps(library_path: str) -> tuple[dict[str, str], dict[str, str]]:
    """Build ``(module -> alias, pkg_id -> alias)`` maps with cheap stat caching."""
    if not library_path or not os.path.isdir(library_path):
        return {}, {}
    from . import constants as C
    from .db import LibraryDB

    cat_path = os.path.join(library_path, C.DIR_META, C.DB_FILENAME)
    cat_sig = _stat_sig(cat_path)
    if cat_sig is None:
        return {}, {}

    # Check if we already cached this library+catalog+local-state revision.
    prev_sig = _CACHE["sig"]
    if prev_sig and prev_sig[0] == library_path and prev_sig[1] == cat_sig:
        state_path = prev_sig[2]
        if _stat_sig(state_path) == prev_sig[3]:
            return _CACHE["by_module"], _CACHE["by_pkg_id"]

    db = LibraryDB(library_path)
    library_id = str(db.data.get("library_id") or "")
    state_path = str(db._state_store(library_id).path) if library_id else ""
    by_module: dict[str, str] = {}
    by_pkg_id: dict[str, str] = {}

    for rec in db.plugins.values():
        alias = (rec.get("display_name") or "").strip()
        if not alias:
            continue
        module = (rec.get("module") or "").strip()
        if module:
            by_module[module] = alias
        pkg_id = (rec.get("pkg_id") or rec.get("id") or "").strip()
        if pkg_id and rec.get("kind") == C.KIND_EXTENSION:
            by_pkg_id[pkg_id] = alias
        folder = (rec.get("folder_name") or "").strip()
        if folder and rec.get("kind") in (C.KIND_ADDON, C.KIND_BUILTIN) and folder not in by_module:
            by_module[folder] = alias

    _CACHE["sig"] = (library_path, cat_sig, state_path, _stat_sig(state_path))
    _CACHE["by_module"] = by_module
    _CACHE["by_pkg_id"] = by_pkg_id
    return by_module, by_pkg_id


def _active_settings() -> tuple[bool, str, str]:
    """Return ``(enabled, style, library_path)`` from current add-on preferences."""
    try:
        from . import bridge
        prefs = bridge.get_prefs()
    except Exception:
        prefs = None
    if prefs is None:
        return False, STYLE_ORIG_CN, ""
    enabled = bool(getattr(prefs, "native_display_names", True))
    style = str(getattr(prefs, "native_name_style", STYLE_ORIG_CN) or STYLE_ORIG_CN)
    root = str(getattr(prefs, "library_path", "") or "")
    return enabled, style, root


# ---------------------------------------------------------------------------
# Pure transformation helpers (unit-testable without Blender)
# ---------------------------------------------------------------------------
def patch_extension_manifest_map(manifest_map: dict, by_module: dict[str, str],
                                 by_pkg_id: dict[str, str],
                                 style: str = STYLE_ORIG_CN) -> dict:
    """Return a shallow copy of ``addon_extension_manifest_map`` with formatted names."""
    if not isinstance(manifest_map, dict) or not manifest_map:
        return manifest_map
    if not by_module and not by_pkg_id:
        return manifest_map
    patched = None
    for module_name, item in manifest_map.items():
        if item is None or not hasattr(item, "name") or not hasattr(item, "_replace"):
            continue
        alias = by_module.get(module_name)
        if not alias and "." in module_name:
            alias = by_pkg_id.get(module_name.rsplit(".", 1)[-1])
        if not alias:
            continue
        new_name = format_native_name(item.name, alias, style)
        if new_name != item.name:
            if patched is None:
                patched = dict(manifest_map)
            patched[module_name] = item._replace(name=new_name)
    return patched if patched is not None else manifest_map


def patch_local_manifest_dict(pkg_manifest_local: dict, repo_prefix: str,
                              by_module: dict[str, str], by_pkg_id: dict[str, str],
                              style: str = STYLE_ORIG_CN) -> dict:
    """Return a shallow copy of ``pkg_manifest_local`` for Get Extensions UI."""
    if not isinstance(pkg_manifest_local, dict) or not pkg_manifest_local:
        return pkg_manifest_local
    if not by_module and not by_pkg_id:
        return pkg_manifest_local
    patched = None
    for pkg_id, item in pkg_manifest_local.items():
        if item is None or not hasattr(item, "name") or not hasattr(item, "_replace"):
            continue
        alias = by_module.get(repo_prefix + pkg_id) or by_pkg_id.get(pkg_id)
        if not alias:
            continue
        new_name = format_native_name(item.name, alias, style)
        if new_name != item.name:
            if patched is None:
                patched = dict(pkg_manifest_local)
            patched[pkg_id] = item._replace(name=new_name)
    return patched if patched is not None else pkg_manifest_local


def apply_legacy_bl_info_overrides(addon_modules, by_module: dict[str, str],
                                   module_bl_info_fn, is_extension_fn,
                                   style: str = STYLE_ORIG_CN) -> list[tuple[dict, str]]:
    """Temporarily override ``bl_info['name']`` in memory and return restore pairs."""
    restores: list[tuple[dict, str]] = []
    if not by_module:
        return restores
    for mod in addon_modules:
        mod_name = getattr(mod, "__name__", "") or ""
        if not mod_name or is_extension_fn(mod_name):
            continue
        alias = by_module.get(mod_name)
        if not alias:
            continue
        try:
            info = module_bl_info_fn(mod)
        except Exception:
            continue
        if not isinstance(info, dict):
            continue
        orig = str(info.get("name") or "")
        new_name = format_native_name(orig, alias, style)
        if new_name != orig:
            restores.append((info, orig))
            info["name"] = new_name
    return restores


def restore_legacy_bl_info(restores: list[tuple[dict, str]]) -> None:
    for info, orig in restores:
        try:
            info["name"] = orig
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Blender bl_pkg runtime hook installation / removal
# ---------------------------------------------------------------------------
def _find_bl_extension_ui():
    mod = sys.modules.get("bl_pkg.bl_extension_ui")
    if mod is not None:
        return mod
    try:
        import bl_pkg.bl_extension_ui as mod  # type: ignore
        return mod
    except Exception:
        return None


def install() -> dict:
    """Attach reversible runtime display wrappers to Blender's Add-ons & Get Extensions UI."""
    if _HOOK_STATE["installed"]:
        return status()
    mod = _find_bl_extension_ui()
    if mod is None:
        _HOOK_STATE["error"] = "未检测到 bl_pkg 扩展界面模块"
        return status()

    _HOOK_STATE["bl_extension_ui"] = mod
    _HOOK_STATE["error"] = ""

    # 1) Hook Preferences -> Add-ons item drawing
    orig_addons = getattr(mod, "addons_panel_draw_items", None)
    if callable(orig_addons) and not getattr(orig_addons, "_pm_native_name_wrapped", False):
        def _wrapped_addons_draw_items(*args, **kwargs):
            enabled, style, root = _active_settings()
            if not enabled or not root:
                return orig_addons(*args, **kwargs)
            restores: list[tuple[dict, str]] = []
            try:
                by_module, by_pkg_id = _load_alias_maps(root)
                if by_module or by_pkg_id:
                    if "addon_extension_manifest_map" in kwargs:
                        kwargs = dict(kwargs)
                        kwargs["addon_extension_manifest_map"] = patch_extension_manifest_map(
                            kwargs["addon_extension_manifest_map"], by_module, by_pkg_id, style,
                        )
                    addon_modules = kwargs.get("addon_modules")
                    if addon_modules is not None:
                        import addon_utils
                        mods = list(addon_modules)
                        kwargs = dict(kwargs)
                        kwargs["addon_modules"] = mods
                        restores = apply_legacy_bl_info_overrides(
                            mods, by_module,
                            addon_utils.module_bl_info, addon_utils.check_extension,
                            style=style,
                        )
                return orig_addons(*args, **kwargs)
            except Exception:
                restore_legacy_bl_info(restores)
                restores = []
                return orig_addons(*args, **kwargs)
            finally:
                restore_legacy_bl_info(restores)

        _wrapped_addons_draw_items._pm_native_name_wrapped = True
        mod.addons_panel_draw_items = _wrapped_addons_draw_items
        _HOOK_STATE["orig_addons_draw_items"] = orig_addons
        _HOOK_STATE["addons_hooked"] = True

    # 2) Hook Preferences -> Get Extensions visible item stream (covers search + rendering)
    filter_cls = getattr(mod, "ExtensionUI_FilterParams", None)
    orig_visible = getattr(filter_cls, "extension_ui_visible", None) if filter_cls is not None else None
    prefix_fn = getattr(mod, "pkg_repo_module_prefix", None)
    if (filter_cls is not None and callable(orig_visible) and callable(prefix_fn)
            and not getattr(orig_visible, "_pm_native_name_wrapped", False)):
        def _wrapped_extension_ui_visible(self_params, repo_index, pkg_manifest_local, pkg_manifest_remote):
            enabled, style, root = _active_settings()
            if enabled and root and isinstance(pkg_manifest_local, dict) and pkg_manifest_local:
                try:
                    by_module, by_pkg_id = _load_alias_maps(root)
                    if by_module or by_pkg_id:
                        repos = getattr(self_params, "repos_all", ())
                        repo_prefix = prefix_fn(repos[repo_index]) if 0 <= repo_index < len(repos) else ""
                        pkg_manifest_local = patch_local_manifest_dict(
                            pkg_manifest_local, repo_prefix, by_module, by_pkg_id, style,
                        )
                except Exception:
                    pass
            return orig_visible(self_params, repo_index, pkg_manifest_local, pkg_manifest_remote)

        _wrapped_extension_ui_visible._pm_native_name_wrapped = True
        filter_cls.extension_ui_visible = _wrapped_extension_ui_visible
        _HOOK_STATE["orig_ext_ui_visible"] = orig_visible
        _HOOK_STATE["extensions_hooked"] = True

    _HOOK_STATE["installed"] = bool(_HOOK_STATE["addons_hooked"] or _HOOK_STATE["extensions_hooked"])
    return status()


def uninstall() -> None:
    """Restore Blender's original native Add-ons & Get Extensions functions."""
    mod = _HOOK_STATE.get("bl_extension_ui")
    if mod is not None:
        orig_addons = _HOOK_STATE.get("orig_addons_draw_items")
        if orig_addons is not None and getattr(getattr(mod, "addons_panel_draw_items", None),
                                               "_pm_native_name_wrapped", False):
            mod.addons_panel_draw_items = orig_addons
        filter_cls = getattr(mod, "ExtensionUI_FilterParams", None)
        orig_visible = _HOOK_STATE.get("orig_ext_ui_visible")
        if (filter_cls is not None and orig_visible is not None
                and getattr(getattr(filter_cls, "extension_ui_visible", None),
                            "_pm_native_name_wrapped", False)):
            filter_cls.extension_ui_visible = orig_visible
    _HOOK_STATE.update({
        "installed": False,
        "addons_hooked": False,
        "extensions_hooked": False,
        "orig_addons_draw_items": None,
        "orig_ext_ui_visible": None,
        "bl_extension_ui": None,
        "error": "",
    })
    invalidate_cache()


def status() -> dict:
    enabled, style, root = _active_settings()
    mapped = 0
    if enabled and root:
        try:
            by_module, by_pkg_id = _load_alias_maps(root)
            mapped = len(set(by_module) | set(by_pkg_id))
        except Exception:
            mapped = 0
    return {
        "installed": bool(_HOOK_STATE["installed"]),
        "addons_hooked": bool(_HOOK_STATE["addons_hooked"]),
        "extensions_hooked": bool(_HOOK_STATE["extensions_hooked"]),
        "enabled": enabled,
        "style": style,
        "mapped_count": mapped,
        "error": _HOOK_STATE["error"],
    }
