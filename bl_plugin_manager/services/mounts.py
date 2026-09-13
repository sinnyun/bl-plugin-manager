"""Pure mount reconciliation policy, independent of Blender's bpy objects."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MountResult:
    entries: tuple[dict, ...]
    removed: int
    ready: bool


def reconcile_entries(entries: list[dict], current_directory: str) -> MountResult:
    """Keep all non-owned entries and exactly one pmlib entry."""
    target = os.path.normcase(os.path.abspath(current_directory)) if current_directory else ""
    output: list[dict] = []
    owned: list[dict] = []
    removed = 0
    for entry in entries:
        if entry.get("module") != "pmlib":
            output.append(dict(entry))
            continue
        directory = entry.get("directory") or entry.get("custom_directory") or ""
        if target and os.path.normcase(os.path.abspath(directory)) == target and not owned:
            item = dict(entry)
            item["directory"] = current_directory
            owned.append(item)
            output.append(item)
        else:
            removed += 1
    if target and not owned:
        item = {"name": "Plugin Library", "module": "pmlib", "directory": current_directory}
        owned.append(item)
        output.append(item)
    ready = bool(target and os.path.isdir(current_directory) and len(owned) == 1)
    return MountResult(tuple(output), removed, ready)

