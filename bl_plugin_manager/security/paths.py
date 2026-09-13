"""Resolve database records without allowing filesystem escape."""

from __future__ import annotations

import ntpath
import os
from pathlib import Path


class UnsafeLibraryPathError(ValueError):
    """The database path is not safely contained by the plugin library."""


_ROOTS = {"addon": "addons", "extension": "extensions"}


def resolve_record_path(root: str | os.PathLike[str], rel: str, kind: str) -> Path:
    if kind not in _ROOTS:
        raise UnsafeLibraryPathError(f"unknown plugin kind: {kind!r}")
    if not isinstance(rel, str) or not rel.strip():
        raise UnsafeLibraryPathError("record path must be a non-empty string")
    if "\x00" in rel or ntpath.isabs(rel) or ntpath.splitdrive(rel)[0]:
        raise UnsafeLibraryPathError("absolute or drive-qualified record path")
    normalized = rel.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise UnsafeLibraryPathError("parent traversal in record path")
    if parts[0].casefold() != _ROOTS[kind].casefold():
        raise UnsafeLibraryPathError("record path kind does not match its container")

    library = Path(root).resolve()
    candidate = (library.joinpath(*parts)).resolve()
    container = (library / _ROOTS[kind]).resolve()
    try:
        common = Path(os.path.commonpath((str(candidate), str(container))))
    except ValueError as exc:
        raise UnsafeLibraryPathError("record path uses a different volume") from exc
    if common != container:
        raise UnsafeLibraryPathError("record path escapes plugin container")
    return candidate

