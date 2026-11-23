"""Curated workflow preset engine & session manual-enable tracker.

Design principles (v2.5):
1. Categories organize and filter plugins — they never auto-toggle plugins.
2. Presets are curated sets of plugins meant to be enabled together.
3. A preset has NO stale stored ``active`` boolean: its state (``all`` / ``partial``
   / ``off``) is always derived from the real Blender enabled state of its members.
4. Enabling a preset automatically skips known-incompatible or load-failed plugins
   so toggling a preset never triggers error storms.
5. Manually enabled plugins (especially those marked "temporary") are tracked for
   the session and protected when switching or closing presets.
"""

from __future__ import annotations

from .. import constants as C, scan


# ---------------------------------------------------------------------------
# Session-scoped manual / temporary enable state (in-memory, resets on restart)
# ---------------------------------------------------------------------------
MANUAL_SESSION_KEYS: set[str] = set()
TEMP_KEYS: set[str] = set()
_PENDING_PROMPT_KEY: str = ""


def reset_session() -> None:
    """Clear session-only manual/temporary markers (e.g. on clean start or library switch)."""
    global _PENDING_PROMPT_KEY
    MANUAL_SESSION_KEYS.clear()
    TEMP_KEYS.clear()
    _PENDING_PROMPT_KEY = ""


def is_known_broken(rec: dict) -> bool:
    """Return True if the plugin is known to fail or is out of Blender version range."""
    if not rec:
        return True
    if rec.get("load_state") == "failed":
        return True
    compat, _ = scan.blender_compat(rec.get("blender_min", ""), rec.get("blender_max", ""))
    return compat in ("too_old", "too_new")


def _eligible_members(db, keys, *, self_module: str = C.ADDON_ID) -> list[str]:
    out: list[str] = []
    for key in keys:
        rec = db.get(key)
        if not rec or rec.get("missing"):
            continue
        module = rec.get("module") or ""
        if not module or module == self_module:
            continue
        out.append(key)
    return out


def list_presets(db, *, self_module: str = C.ADDON_ID) -> list[dict]:
    """Return all presets with real-time derived state from actual plugin enabled flags."""
    out: list[dict] = []
    for p in db.preset_records():
        raw_members = list(p.get("plugins") or [])
        installed = _eligible_members(db, raw_members, self_module=self_module)
        runnable = [k for k in installed if not is_known_broken(db.get(k))]
        enabled_installed = [k for k in installed if (db.get(k) or {}).get("enabled")]
        enabled_runnable = [k for k in runnable if (db.get(k) or {}).get("enabled")]

        total = len(installed)
        enabled = len(enabled_installed)
        if runnable and len(enabled_runnable) == len(runnable):
            state = "all"
        elif total > 0 and enabled == total:
            state = "all"
        elif enabled > 0:
            state = "partial"
        else:
            state = "off"

        out.append({
            "id": p["id"],
            "name": p["name"],
            "order": p["order"],
            "pinned": bool(p.get("pinned", False)),
            "startup": bool(p.get("startup", False)),
            "description": p.get("description", ""),
            "plugins": raw_members,
            "members": installed,
            "runnable": runnable,
            "total": total,
            "enabled": enabled,
            "broken": len(installed) - len(runnable),
            "state": state,          # "all" | "partial" | "off"
            "active": state == "all",
        })
    return out


def preset_members(db, preset_name: str, *, self_module: str = C.ADDON_ID) -> list[str]:
    rec = db.get_preset(preset_name)
    if not rec:
        return []
    return _eligible_members(db, rec.get("plugins") or [], self_module=self_module)


def active_preset_names(db) -> set[str]:
    return {p["name"] for p in list_presets(db) if p["state"] == "all"}


# ---------------------------------------------------------------------------
# Manual / temporary session tracking
# ---------------------------------------------------------------------------
def is_temporary(key: str) -> bool:
    return bool(key) and key in TEMP_KEYS


def is_manual_session(key: str) -> bool:
    return bool(key) and (key in MANUAL_SESSION_KEYS or key in TEMP_KEYS)


def protected_manual_keys(db) -> set[str]:
    """Return currently-enabled plugins that were manually enabled or marked temporary."""
    return {
        k for k in (MANUAL_SESSION_KEYS | TEMP_KEYS)
        if (db.get(k) or {}).get("enabled")
    }


def note_manual_enable(db, key: str) -> bool:
    """Record a manual plugin enable and decide whether to show the inline prompt.

    Returns True if a prompt should be shown (plugin is not already covered by an
    active preset and not already explicitly marked temporary).
    """
    global _PENDING_PROMPT_KEY
    if not key or not db.get(key):
        return False
    MANUAL_SESSION_KEYS.add(key)
    if key in TEMP_KEYS:
        return False
    # If already a member of any currently fully-active or pinned-enabled preset, no need to nag.
    for p in list_presets(db):
        if key in p["members"] and (p["state"] == "all" or (p["pinned"] and p["enabled"] > 0)):
            return False
    _PENDING_PROMPT_KEY = key
    return True


def note_manual_disable(key: str) -> None:
    global _PENDING_PROMPT_KEY
    if not key:
        return
    MANUAL_SESSION_KEYS.discard(key)
    TEMP_KEYS.discard(key)
    if _PENDING_PROMPT_KEY == key:
        _PENDING_PROMPT_KEY = ""


def mark_temporary(key: str) -> None:
    global _PENDING_PROMPT_KEY
    if not key:
        return
    MANUAL_SESSION_KEYS.add(key)
    TEMP_KEYS.add(key)
    if _PENDING_PROMPT_KEY == key:
        _PENDING_PROMPT_KEY = ""


def unmark_temporary(key: str) -> None:
    if not key:
        return
    TEMP_KEYS.discard(key)


def note_assigned_to_preset(key: str) -> None:
    """After adding a manually-enabled plugin to a preset, clear prompt & temp tag."""
    global _PENDING_PROMPT_KEY
    if not key:
        return
    TEMP_KEYS.discard(key)
    MANUAL_SESSION_KEYS.discard(key)
    if _PENDING_PROMPT_KEY == key:
        _PENDING_PROMPT_KEY = ""


def dismiss_prompt(key: str = "") -> None:
    global _PENDING_PROMPT_KEY
    if not key or _PENDING_PROMPT_KEY == key:
        _PENDING_PROMPT_KEY = ""


def pending_prompt(db) -> dict | None:
    """Return the active manual-enable prompt record, or None if no longer relevant."""
    global _PENDING_PROMPT_KEY
    if not _PENDING_PROMPT_KEY or db is None:
        return None
    rec = db.get(_PENDING_PROMPT_KEY)
    if not rec or not rec.get("enabled") or rec.get("missing"):
        _PENDING_PROMPT_KEY = ""
        return None
    return rec


# ---------------------------------------------------------------------------
# Planning (pure functions — easy to unit-test)
# ---------------------------------------------------------------------------
def _split_runnable(db, keys, *, skip_incompatible: bool) -> tuple[list[str], list[str]]:
    if not skip_incompatible:
        return sorted(keys), []
    ok, skipped = [], []
    for key in sorted(keys):
        if is_known_broken(db.get(key)):
            skipped.append(key)
        else:
            ok.append(key)
    return ok, skipped


def plan_preset_toggle(db, preset_name: str, enable: bool, *,
                       protect_keys: set[str] | None = None,
                       skip_incompatible: bool = True,
                       self_module: str = C.ADDON_ID) -> dict:
    """Compute plugin enable/disable actions for toggling one preset."""
    members = set(preset_members(db, preset_name, self_module=self_module))
    if enable:
        to_enable, skipped_broken = _split_runnable(db, members, skip_incompatible=skip_incompatible)
        return {
            "enable": to_enable,
            "disable": [],
            "kept_shared": [],
            "kept_manual": [],
            "skipped_broken": skipped_broken,
        }

    # Protect members of other active presets, enabled pinned presets, and temporary/manual keys
    shared_protected: set[str] = set()
    for p in list_presets(db, self_module=self_module):
        if p["name"] == preset_name:
            continue
        if p["state"] == "all" or (p["pinned"] and p["enabled"] > 0):
            shared_protected.update(p["members"])

    manual_protected = set(protect_keys if protect_keys is not None else TEMP_KEYS)
    all_protected = shared_protected | manual_protected
    disable = sorted(members - all_protected)
    kept_shared = sorted(members & shared_protected)
    kept_manual = sorted((members & manual_protected) - shared_protected)
    return {
        "enable": [],
        "disable": disable,
        "kept_shared": kept_shared,
        "kept_manual": kept_manual,
        "skipped_broken": [],
    }


def plan_preset_switch(db, preset_name: str, *,
                       keep_pinned: bool = True,
                       keep_manual: bool = True,
                       protect_keys: set[str] | None = None,
                       skip_incompatible: bool = True,
                       self_module: str = C.ADDON_ID) -> dict:
    """Switch to ``preset_name``: enable it (+ pinned presets) and close other presets.

    Manually enabled / temporary plugins outside the closing presets (and those in
    ``protect_keys``) are preserved so switching presets never kills manual work.
    """
    presets = list_presets(db, self_module=self_module)
    target_names = {preset_name}
    if keep_pinned:
        target_names.update(p["name"] for p in presets if p["pinned"])

    desired_keys: set[str] = set()
    other_keys: set[str] = set()
    for p in presets:
        if p["name"] in target_names:
            desired_keys.update(p["members"])
        elif p["enabled"] > 0:
            other_keys.update(p["members"])

    manual_protected = (
        set(protect_keys if protect_keys is not None else protected_manual_keys(db))
        if keep_manual else set()
    )

    to_enable, skipped_broken = _split_runnable(db, desired_keys, skip_incompatible=skip_incompatible)
    all_keep = desired_keys | manual_protected
    disable = sorted(other_keys - all_keep)
    kept_shared = sorted(other_keys & desired_keys)
    kept_manual = sorted((other_keys & manual_protected) - desired_keys)
    return {
        "enable": to_enable,
        "disable": disable,
        "kept_shared": kept_shared,
        "kept_manual": kept_manual,
        "skipped_broken": skipped_broken,
    }


def plan_deactivate_all_presets(db, *,
                                include_pinned: bool = False,
                                keep_manual: bool = True,
                                protect_keys: set[str] | None = None,
                                self_module: str = C.ADDON_ID) -> dict:
    """Disable members of all non-pinned presets while protecting pinned & manual keys."""
    presets = list_presets(db, self_module=self_module)
    keep_keys: set[str] = set()
    closing_keys: set[str] = set()
    for p in presets:
        if p["pinned"] and not include_pinned:
            keep_keys.update(p["members"])
        elif p["enabled"] > 0:
            closing_keys.update(p["members"])

    manual_protected = (
        set(protect_keys if protect_keys is not None else protected_manual_keys(db))
        if keep_manual else set()
    )
    all_keep = keep_keys | manual_protected
    return {
        "enable": [],
        "disable": sorted(closing_keys - all_keep),
        "kept_shared": sorted(closing_keys & keep_keys),
        "kept_manual": sorted((closing_keys & manual_protected) - keep_keys),
        "skipped_broken": [],
    }


def effective_startup_targets(db, *, self_module: str = C.ADDON_ID) -> tuple[set[str], set[str]]:
    """Return ``(plugin_keys, startup_preset_names)`` for Clean Startup mode.

    Includes:
    * Eligible members of presets marked ``startup=True``
    * Individual plugins marked ``startup=True``
    """
    startup_presets = {p["name"] for p in db.preset_records() if p.get("startup")}
    keys: set[str] = set()
    for p in db.preset_records():
        if p["name"] in startup_presets:
            keys.update(_eligible_members(db, p.get("plugins") or [], self_module=self_module))
    for key, rec in db.plugins.items():
        if rec.get("startup") and not rec.get("missing"):
            module = rec.get("module") or ""
            if module and module != self_module:
                keys.add(key)
    return keys, startup_presets


def apply_preset_plan(db, plan: dict, *, set_enabled_fn, is_enabled_fn) -> dict:
    """Execute a preset plan and persist updated real plugin enabled states."""
    stats = {
        "enabled": 0,
        "disabled": 0,
        "kept_shared": len(plan.get("kept_shared") or ()),
        "kept_manual": len(plan.get("kept_manual") or ()),
        "skipped_broken": len(plan.get("skipped_broken") or ()),
        "failed": [],
        "enabled_keys": [],
        "disabled_keys": [],
    }

    for key in plan.get("disable") or ():
        rec = db.get(key)
        if not rec:
            continue
        module = rec.get("module") or ""
        if not module:
            continue
        if not is_enabled_fn(module):
            rec["enabled"] = False
            continue
        ok, err = set_enabled_fn(module, False)
        rec["enabled"] = bool(is_enabled_fn(module))
        if ok:
            rec["last_error"] = ""
            stats["disabled"] += 1
            stats["disabled_keys"].append(key)
            MANUAL_SESSION_KEYS.discard(key)
            TEMP_KEYS.discard(key)
        else:
            rec["last_error"] = err
            stats["failed"].append({"key": key, "name": rec.get("name") or key, "error": err})

    for key in plan.get("enable") or ():
        rec = db.get(key)
        if not rec:
            continue
        module = rec.get("module") or ""
        if not module:
            continue
        if is_enabled_fn(module):
            rec["enabled"] = True
            continue
        ok, err = set_enabled_fn(module, True)
        rec["enabled"] = bool(is_enabled_fn(module))
        if ok:
            rec["last_error"] = ""
            stats["enabled"] += 1
            stats["enabled_keys"].append(key)
        else:
            rec["last_error"] = err
            stats["failed"].append({"key": key, "name": rec.get("name") or key, "error": err})

    db.save()
    return stats
