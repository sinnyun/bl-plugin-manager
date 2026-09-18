"""Shared, replacement-sensitive JSON read cache for the local stores.

The N-panel constructs a ``LibraryDB`` several times per redraw, and each view
needs three files (master catalog, this device's profile, this environment's
runtime state).  Re-parsing all three every draw is wasteful, so reads are
memoised against a file signature that changes on replacement — including
``st_ctime_ns``/``st_ino``, so an atomic replace is never served from a stale
entry even when size and mtime are coincidentally identical.

Cached values are deep-copied in and out; callers routinely mutate the returned
structure, and that must never leak back into the cache.
"""

from __future__ import annotations

import copy
import json
import os


_CACHE: dict[str, tuple] = {}


def signature(path: str):
    """身份 + 创建/修改时间 + 大小；不可读时返回 None。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (getattr(st, "st_dev", 0), getattr(st, "st_ino", 0),
            st.st_ctime_ns, st.st_mtime_ns, st.st_size)


def read(path: str, *, validate=None):
    """返回 (signature, data)。data 为 None 表示不可读/非对象。

    ``validate`` 可用于拒绝结构不合法的内容而不缓存它。
    """
    sig = signature(path)
    if sig is None:
        return None, None
    hit = _CACHE.get(path)
    if hit and hit[0] == sig:
        return sig, copy.deepcopy(hit[1])
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return sig, None
    if not isinstance(data, dict):
        return sig, None
    if validate is not None and not validate(data):
        return sig, None
    _CACHE[path] = (sig, copy.deepcopy(data))
    return sig, data


def store(path: str, data) -> None:
    """写入后刷新缓存，避免紧接着的读取又解析一遍。"""
    sig = signature(path)
    if sig is None:
        return
    _CACHE[path] = (sig, copy.deepcopy(data))


def invalidate(path: str = "") -> None:
    if path:
        _CACHE.pop(path, None)
    else:
        _CACHE.clear()
