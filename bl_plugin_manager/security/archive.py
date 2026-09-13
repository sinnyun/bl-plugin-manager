"""Bounded inspection and extraction rules for plugin archives."""

from __future__ import annotations

import os
import ntpath
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class UnsafeArchiveError(ValueError):
    pass


class ArchiveLimitError(ValueError):
    pass


@dataclass(frozen=True)
class ArchivePlan:
    members: tuple[str, ...]
    expanded_bytes: int


def inspect_archive(path: str | os.PathLike[str], *, max_members: int = 2048,
                    max_file_bytes: int = 256 * 1024 * 1024,
                    max_total_bytes: int = 512 * 1024 * 1024,
                    max_ratio: float = 200.0, max_path_length: int = 240) -> ArchivePlan:
    names: list[str] = []
    total = 0
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > max_members:
            raise ArchiveLimitError("archive has too many members")
        for info in infos:
            raw = info.filename.replace("\\", "/")
            posix = PurePosixPath(raw)
            if (not raw or posix.is_absolute() or ".." in posix.parts
                    or ntpath.splitdrive(raw)[0] or "\x00" in raw):
                raise UnsafeArchiveError(f"unsafe archive member: {info.filename!r}")
            if len(raw) > max_path_length:
                raise ArchiveLimitError("archive member path is too long")
            if info.file_size > max_file_bytes:
                raise ArchiveLimitError("archive member is too large")
            if info.compress_size and info.file_size / info.compress_size > max_ratio:
                raise ArchiveLimitError("archive compression ratio is too high")
            total += info.file_size
            if total > max_total_bytes:
                raise ArchiveLimitError("archive expands beyond total size limit")
            names.append(raw)
    return ArchivePlan(tuple(names), total)


def extract_archive(path: str | os.PathLike[str], destination: str | os.PathLike[str], **limits) -> ArchivePlan:
    plan = inspect_archive(path, **limits)
    target = Path(destination).resolve()
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for name in plan.members:
            out = (target / Path(name)).resolve()
            if os.path.commonpath((str(target), str(out))) != str(target):
                raise UnsafeArchiveError(f"archive member escapes destination: {name!r}")
            info = archive.getinfo(name)
            if info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(out, "wb") as sink:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    sink.write(chunk)
    return plan

