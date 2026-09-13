"""Schema 2 shared library metadata.

This module deliberately has no dependency on Blender or the legacy database
implementation.  A non-schema-2 JSON object is archived byte-for-byte; a
malformed file is left untouched and put into a read-only failure state.
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = 2
DB_NAME = "library.json"
DEFAULT_CATEGORY = {"id": "uncategorized", "name": "未分类", "order": 0}
SHARED_PLUGIN_FIELDS = frozenset({
    "key", "kind", "rel", "id", "name", "version",
    "display_name", "category_id", "tags", "note", "favorite", "startup",
})


@dataclass(frozen=True)
class InitializationReport:
    status: str
    path: Path
    archive: Path | None = None


class InvalidSharedFieldError(ValueError):
    pass


class DatabaseConflictError(RuntimeError):
    pass


class SharedDatabase:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.path = self.root / ".pm" / DB_NAME
        self.data: dict = {}
        self.status = "MISSING"
        self.loaded_signature = None

    @staticmethod
    def _empty() -> dict:
        return {
            "schema": SCHEMA,
            "library_id": str(uuid.uuid4()),
            "revision": 0,
            "categories": [dict(DEFAULT_CATEGORY)],
            "plugins": {},
        }

    def _write_new(self) -> None:
        current = self._signature()
        if self.loaded_signature is not None and current != self.loaded_signature:
            raise DatabaseConflictError(f"数据库已被外部更新: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + f".{uuid.uuid4().hex}.tmp")
        payload = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            self.loaded_signature = self._signature()
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def _archive_existing(self) -> Path:
        archive_dir = self.path.parent / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = archive_dir / f"library.pre-2.0.{stamp}.json"
        index = 1
        while target.exists():
            target = archive_dir / f"library.pre-2.0.{stamp}.{index}.json"
            index += 1
        os.replace(self.path, target)
        target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return target

    def _signature(self):
        try:
            st = self.path.stat()
            return (st.st_dev, st.st_ino, st.st_ctime_ns, st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    @staticmethod
    def _is_schema2(value: object) -> bool:
        if not isinstance(value, dict):
            return False
        if value.get("schema") != SCHEMA or not isinstance(value.get("library_id"), str):
            return False
        if not isinstance(value.get("revision"), int) or not isinstance(value.get("plugins"), dict):
            return False
        categories = value.get("categories")
        return isinstance(categories, list) and all(
            isinstance(item, dict) and set(item) <= {"id", "name", "order"}
            and isinstance(item.get("id"), str) and isinstance(item.get("name"), str)
            for item in categories
        )

    def initialize(self) -> InitializationReport:
        if not self.path.exists():
            self.data = self._empty()
            self._write_new()
            self.status = "OK"
            return InitializationReport("CREATED", self.path)

        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            self.data = {}
            self.status = "CORRUPT"
            return InitializationReport("CORRUPT", self.path)

        if self._is_schema2(existing):
            self.data = existing
            self.status = "OK"
            self.loaded_signature = self._signature()
            return InitializationReport("READY", self.path)

        archive = self._archive_existing()
        self.data = self._empty()
        try:
            self._write_new()
        except Exception:
            # Restore the original entry point when initialization cannot finish.
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            os.replace(archive, self.path)
            raise
        self.status = "OK"
        self.loaded_signature = self._signature()
        return InitializationReport("ARCHIVED_AND_CREATED", self.path, archive)

    def update_plugin(self, key: str, changes: dict) -> dict:
        if self.status != "OK":
            raise RuntimeError(f"database is not writable: {self.status}")
        unknown = set(changes) - SHARED_PLUGIN_FIELDS
        if unknown:
            raise InvalidSharedFieldError(
                "machine-specific or unknown shared fields: " + ", ".join(sorted(unknown))
            )
        plugins = self.data.setdefault("plugins", {})
        record = dict(plugins.get(key, {}))
        record.update(changes)
        record["key"] = key
        plugins[key] = record
        self.data["revision"] = int(self.data.get("revision", 0)) + 1
        self._write_new()
        return dict(record)
