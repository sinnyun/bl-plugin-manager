"""Run library discovery and database migration before mounting in Blender."""

from __future__ import annotations

import os
from dataclasses import dataclass

import bpy

from . import bridge, items, library, preferences, scoped_management
from .db import LibraryDB, compatibility_environment
from .storage import machine_config
from .tasks import TaskController


@dataclass
class LibraryJob:
    root: str
    source_paths: tuple[str, str]
    mode: str
    worker: TaskController


_job: LibraryJob | None = None


def running() -> bool:
    return _job is not None and (not _job.worker.cancelled or _job.worker.running)


def _preview(root, load_environment):
    info = library.inspect_target(root)
    if error := library.target_error(info):
        raise ValueError(error)
    database = LibraryDB(root, load_environment=load_environment)
    if database.status in {"CORRUPT", "UNSUPPORTED"}:
        raise ValueError(f"资料库不可写（{database.status}），原文件已保留")
    if not info["exists"]:
        return "新目录；启用管理时创建空库"
    count = info["addon_count"] + info["extension_count"]
    summary = f"库内 {count} 个插件；另有 {len(info['flat_plugins'])} 个待复制"
    if database.catalog.legacy_path.exists():
        summary += "；启用时迁移旧资料"
    return summary


def start(prefs, root, *, mode="connect") -> None:
    global _job
    if running() or prefs.compat_running or prefs.archive_running:
        raise RuntimeError("请先结束当前插件库任务")
    root = os.path.abspath(root)
    environment = compatibility_environment()
    worker = TaskController()
    paths = (prefs.library_path, prefs.pending_library_path)
    previous = machine_config.initialize() if mode != "preview" else None
    bpy.app.timers.register(_tick, first_interval=0.1, persistent=True)
    try:
        if mode == "preview":
            worker.start(_preview, root, environment)
        else:
            worker.start(scoped_management.prepare_library, root, previous, environment, resume=mode == "resume")
    except Exception:
        bpy.app.timers.unregister(_tick)
        raise
    from . import constants as C
    has_legacy = os.path.isfile(os.path.join(root, C.DIR_META, C.LEGACY_DB_FILENAME))
    _job = LibraryJob(root, paths, mode, worker)
    prefs.library_job_running = True
    prefs.library_job_failed = False
    if mode == "preview":
        prefs.library_job_status = "正在后台检查插件库…"
    elif has_legacy:
        prefs.library_job_status = "正在后台迁移旧资料库并扫描插件…"
    elif mode == "resume":
        prefs.library_job_status = "正在后台增量校验插件库…"
    else:
        prefs.library_job_status = "正在后台增量扫描插件库…"
    _redraw()


def shutdown() -> None:
    global _job
    if _job is not None:
        _job.worker.cancel()
        if not _job.worker.running:
            _job = None
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if prefs := bridge.get_prefs():
        prefs.library_job_running = False


def _redraw():
    manager = bpy.context.window_manager
    for window in manager.windows if manager else ():
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "PREFERENCES"}:
                area.tag_redraw()


def _publish_connection(job, prefs):
    result = scoped_management.activate_prepared_library(job.worker.result)
    preferences._LOADING_MACHINE_PATH = True
    try:
        prefs.library_path = job.root
        prefs.pending_library_path = ""
        prefs.library_preview = ""
    finally:
        preferences._LOADING_MACHINE_PATH = False
    items.clear_selection()
    items.rebuild_items(prefs)
    message = "已启用插件库管理"
    if migration := result.get("migration"):
        message += f"；已迁移 {migration['imported']} 条旧资料"
    if result.get("imported"):
        message += f"；已复制 {len(result['imported'])} 个插件到库内"
    failures = result.get("failed", []) + result.get("failures", [])
    if failures:
        message += f"；{len(failures)} 项需要处理"
        prefs.report_summary = message
        prefs.report_items.clear()
        for failure in failures:
            item = prefs.report_items.add()
            item.name = failure.get("key", "插件加载") if isinstance(failure, dict) else "导入失败"
            item.detail = failure.get("error", "") if isinstance(failure, dict) else failure
            item.status = "failed"
    return message


def _tick():
    global _job
    job = _job
    if job is None:
        return None
    if job.worker.running:
        return 0.1
    prefs = bridge.get_prefs()
    try:
        if not prefs or job.worker.cancelled:
            return None
        if (prefs.library_path, prefs.pending_library_path) != job.source_paths:
            raise RuntimeError("插件库路径已变化，请重新连接")
        if job.worker.state != "succeeded":
            raise RuntimeError(job.worker.error or "无法读取插件库")
        if job.mode == "preview":
            prefs.pending_library_path = job.root
            prefs.library_preview = job.worker.result
            prefs.library_job_status = ""
        else:
            prefs.library_job_status = _publish_connection(job, prefs)
    except Exception as error:
        if prefs:
            prefs.library_job_failed = True
            prefs.library_job_status = str(error)
        print("[插件库]", error)
    finally:
        _job = None
        if prefs:
            prefs.library_job_running = False
        _redraw()
    return None
