"""Composable plugin group engine (multi-group toggle, union protection, solo, startup).

Pure Python service — no ``bpy`` dependency so every state transition can be
unit-tested directly outside Blender.
"""

from __future__ import annotations

from .. import constants as C


def list_groups(db) -> list[dict]:
    """Return all user-defined groups with member counts and local state."""
    records = db.group_records()
    members_by_group: dict[str, list[str]] = {g["name"]: [] for g in records}
    enabled_by_group: dict[str, int] = {g["name"]: 0 for g in records}
    missing_by_group: dict[str, int] = {g["name"]: 0 for g in records}

    for key, rec in db.plugins.items():
        for name in db.plugin_groups(key):
            if name not in members_by_group:
                continue
            members_by_group[name].append(key)
            if rec.get("missing"):
                missing_by_group[name] += 1
            elif rec.get("enabled"):
                enabled_by_group[name] += 1

    out: list[dict] = []
    for g in records:
        name = g["name"]
        members = members_by_group.get(name, [])
        out.append({
            "id": g["id"],
            "name": name,
            "order": g["order"],
            "pinned": bool(g.get("pinned", False)),
            "active": bool(g.get("active", False)),
            "startup": bool(g.get("startup", False)),
            "total": len(members),
            "enabled": enabled_by_group.get(name, 0),
            "missing": missing_by_group.get(name, 0),
            "members": members,
        })
    return out


def group_members(db, group_name: str) -> list[str]:
    """Return installed plugin keys belonging to ``group_name``."""
    return [key for key in db.plugins if group_name in db.plugin_groups(key)]


def _eligible_keys(db, keys, *, self_module: str = C.ADDON_ID) -> list[str]:
    out = []
    for key in keys:
        rec = db.get(key)
        if not rec or rec.get("missing"):
            continue
        module = rec.get("module") or ""
        if not module or module == self_module:
            continue
        out.append(key)
    return out


def active_group_names(db) -> set[str]:
    return {g["name"] for g in db.group_records() if g.get("active")}


def plan_group_toggle(db, group_name: str, active: bool, *,
                      self_module: str = C.ADDON_ID) -> dict:
    """Compute plugin enable/disable actions for toggling one group.

    When turning a group OFF, plugins that also belong to any other currently
    active group are protected and kept enabled (union semantics).
    """
    members = set(_eligible_keys(db, group_members(db, group_name), self_module=self_module))
    if active:
        return {
            "group_updates": {group_name: True},
            "enable": sorted(members),
            "disable": [],
            "kept_shared": [],
        }

    other_active = active_group_names(db) - {group_name}
    protected: set[str] = set()
    for other in other_active:
        protected.update(_eligible_keys(db, group_members(db, other), self_module=self_module))

    disable = sorted(members - protected)
    kept_shared = sorted(members & protected)
    return {
        "group_updates": {group_name: False},
        "enable": [],
        "disable": disable,
        "kept_shared": kept_shared,
    }


def plan_group_solo(db, group_name: str, *, keep_pinned: bool = True,
                    self_module: str = C.ADDON_ID) -> dict:
    """Switch exclusively to ``group_name`` (optionally keeping pinned groups open)."""
    records = db.group_records()
    pinned_names = {g["name"] for g in records if g.get("pinned")} if keep_pinned else set()
    currently_active = {g["name"] for g in records if g.get("active")}

    next_active = {group_name} | (pinned_names & currently_active) | pinned_names
    deactivated = currently_active - next_active

    desired_keys: set[str] = set()
    for name in next_active:
        desired_keys.update(_eligible_keys(db, group_members(db, name), self_module=self_module))

    old_keys: set[str] = set()
    for name in deactivated:
        old_keys.update(_eligible_keys(db, group_members(db, name), self_module=self_module))

    disable = sorted(old_keys - desired_keys)
    enable = sorted(desired_keys)
    group_updates = {name: False for name in deactivated}
    for name in next_active:
        group_updates[name] = True
    return {
        "group_updates": group_updates,
        "activated": sorted(next_active),
        "deactivated": sorted(deactivated),
        "enable": enable,
        "disable": disable,
        "kept_shared": sorted(old_keys & desired_keys),
    }


def plan_deactivate_all(db, *, include_pinned: bool = False,
                        self_module: str = C.ADDON_ID) -> dict:
    """Close all active groups (optionally preserving pinned groups)."""
    records = db.group_records()
    keep = {g["name"] for g in records if g.get("active") and g.get("pinned")} if not include_pinned else set()
    closing = {g["name"] for g in records if g.get("active") and g["name"] not in keep}

    protected: set[str] = set()
    for name in keep:
        protected.update(_eligible_keys(db, group_members(db, name), self_module=self_module))

    closing_keys: set[str] = set()
    for name in closing:
        closing_keys.update(_eligible_keys(db, group_members(db, name), self_module=self_module))

    return {
        "group_updates": {name: False for name in closing},
        "deactivated": sorted(closing),
        "enable": [],
        "disable": sorted(closing_keys - protected),
        "kept_shared": sorted(closing_keys & protected),
    }


def effective_startup_targets(db) -> tuple[set[str], set[str]]:
    """Return ``(plugin_keys, startup_group_names)`` that should start on launch."""
    startup_groups = {g["name"] for g in db.group_records() if g.get("startup")}
    keys: set[str] = set()
    for key, rec in db.plugins.items():
        if rec.get("startup"):
            keys.add(key)
        elif startup_groups and (set(db.plugin_groups(key)) & startup_groups):
            keys.add(key)
    return keys, startup_groups


def apply_group_plan(db, plan: dict, *, set_enabled_fn, is_enabled_fn) -> dict:
    """Execute a plan computed by ``plan_group_*`` and persist updated states."""
    stats = {
        "enabled": 0,
        "disabled": 0,
        "kept_shared": len(plan.get("kept_shared") or ()),
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

    for name, is_active in (plan.get("group_updates") or {}).items():
        db.set_group_state(name, active=bool(is_active))

    db.save()
    return stats
