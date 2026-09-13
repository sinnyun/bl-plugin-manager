"""Pure scan-to-shared-metadata merge for schema 2 libraries."""

from __future__ import annotations

from ..storage.shared_db import SHARED_PLUGIN_FIELDS


def merge_scan(existing: dict, entries: list[dict]) -> dict:
    result: dict[str, dict] = {}
    for entry in entries:
        key = str(entry.get("key") or "")
        if not key:
            continue
        old = existing.get(key) or {}
        record = {k: old[k] for k in SHARED_PLUGIN_FIELDS if k in old}
        for field in ("key", "kind", "rel", "id", "name", "version"):
            if field in entry:
                record[field] = entry[field]
        result[key] = record
    return result

