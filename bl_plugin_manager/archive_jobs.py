"""Download packages off-thread and publish installations on Blender's main thread."""

from __future__ import annotations

import os
from collections import deque
from pathlib import Path

import bpy

from . import bridge, items, store
from .db import LibraryDB
from .tasks import TaskController


_active = None
_failed = {}


class ArchiveQueue:
    def __init__(self, root, requests):
        self.root = root
        self.library_id = LibraryDB(root).data["library_id"]
        self.waiting = deque(requests)
        self.total = len(requests)
        self.done = 0
        self.current = None
        self.worker = TaskController()
        self.failed = []
        self.cancelled = False

    def project(self, prefs):
        prefs.archive_running = True
        prefs.archive_cancelling = self.cancelled
        prefs.archive_status = (
            f"{self.done}/{self.total} · {self.current[0]['name']}"
            if self.current else f"{self.done}/{self.total} 项已处理"
        )
        prefs.archive_progress = min(1.0, (self.done + self.worker.progress / 100) / self.total)
        _redraw()

    def launch_next(self):
        self.current = self.waiting.popleft()
        self.worker.start(store.download_package, self.current[0],
                          lambda value: setattr(self.worker, "progress", value),
                          lambda: self.worker.cancelled, discard=_delete_download)

    def publish(self):
        package, key, old_version = self.current
        db = LibraryDB(self.root)
        if db.status != "OK" or db.data["library_id"] != self.library_id:
            raise RuntimeError("插件库已变化，请重新预览")
        record = db.get(key) if key else None
        installed = store.installed_map(db).get(package["id"].casefold())
        if ((key and (not record or record.get("missing") or record.get("version") != old_version))
                or (not key and installed)):
            raise RuntimeError("安装状态已变化，请重新预览")
        store.apply_package(package, self.worker.result, self.root, db, record)


def _delete_download(path):
    if path:
        Path(path).unlink(missing_ok=True)


def _redraw():
    manager = bpy.context.window_manager
    if manager:
        for window in manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()


def start(prefs, requests):
    global _active
    if _active is not None:
        raise RuntimeError("已有安装或更新任务")
    if prefs.compat_running:
        raise RuntimeError("请先结束兼容性测试")
    if not requests:
        raise ValueError("没有待处理的扩展")
    queue = ArchiveQueue(prefs.library_path, requests)
    if not queue.library_id or not bridge.is_registered(queue.root):
        raise RuntimeError("请先启用插件库管理")
    bpy.app.timers.register(_tick, first_interval=0)
    _active = queue
    queue.project(prefs)


def retry(prefs):
    start(prefs, [request for request, _error in _failed.get(prefs.library_path, ())])


def cancel():
    if _active:
        _active.cancelled = True
        _active.worker.cancel()


def shutdown():
    global _active
    cancel()
    if _active and _active.worker.state == "succeeded":
        _delete_download(_active.worker.result)
    _active = None
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)


def _finish(queue, prefs):
    global _active
    _active = None
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    prefs.archive_running = False
    prefs.archive_cancelling = False
    prefs.archive_retry = bool(queue.failed)
    _failed[queue.root] = queue.failed
    prefs.report_items.clear()
    finished = queue.done - len(queue.failed)
    prefix = "已取消" if queue.cancelled else "任务完成"
    prefs.report_summary = f"{prefix}：成功 {finished}，失败 {len(queue.failed)}，未处理 {queue.total - queue.done}"
    for (package, _key, _version), error in queue.failed:
        entry = prefs.report_items.add()
        entry.name, entry.kind, entry.status = package["name"], "安装或更新", "failed"
        entry.path, entry.detail = package.get("repo", ""), error
    items.rebuild_items(prefs)
    _redraw()


def _tick():
    queue = _active
    prefs = bridge.get_prefs()
    if queue is None:
        return None
    if prefs is None or prefs.library_path != queue.root or not os.path.isdir(queue.root):
        cancel()
    if queue.worker.running:
        if prefs:
            queue.project(prefs)
        return 0.1
    if queue.current:
        try:
            if not queue.cancelled:
                if queue.worker.state != "succeeded":
                    raise RuntimeError(queue.worker.error or "下载失败")
                queue.publish()
        except Exception as error:
            queue.failed.append((queue.current, str(error)))
        finally:
            _delete_download(queue.worker.result)
        if not queue.cancelled:
            queue.done += 1
        queue.current = None
    if queue.cancelled or not queue.waiting:
        if prefs:
            _finish(queue, prefs)
        else:
            shutdown()
        return None
    queue.launch_next()
    queue.project(prefs)
    return 0.1
