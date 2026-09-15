"""清理旧目录（除 ass/）：先把完整文件清单存档，再删除。

存档到 _migrate/old_listing_backup.txt，以便日后追溯被删内容。
"""

from __future__ import annotations

import os
import shutil
import time

OLD = r"D:\nastongbu\qitaziliao\blender"
HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join(HERE, "old_listing_backup.txt")
KEEP = {"ass"}          # 明确保留
SKIP_IN_LIST = {"__pycache__"}


def listing():
    lines = []
    total_files = 0
    total_bytes = 0
    for top in sorted(os.listdir(OLD)):
        if top in KEEP:
            continue
        troot = os.path.join(OLD, top)
        if not os.path.isdir(troot):
            continue
        lines.append(f"\n########## {top} ##########")
        for root, dirs, files in os.walk(troot):
            dirs[:] = [d for d in dirs if d not in SKIP_IN_LIST]
            rel = os.path.relpath(root, OLD)
            for fn in sorted(files):
                p = os.path.join(root, fn)
                try:
                    sz = os.path.getsize(p)
                except OSError:
                    sz = 0
                total_files += 1
                total_bytes += sz
                lines.append(f"{sz:>12}  {os.path.join(rel, fn)}")
    return lines, total_files, total_bytes


def main():
    import sys

    dry = "--dry" in sys.argv or "--run" not in sys.argv

    lines, nfiles, nbytes = listing()
    header = [
        f"# 旧目录文件清单存档  {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"# 根目录: {OLD}",
        f"# 保留: {', '.join(sorted(KEEP))}",
        f"# 文件总数: {nfiles}  总大小: {nbytes/1048576:.1f} MB",
        "# 格式: 字节数  TAB前为相对路径",
    ]
    with open(ARCHIVE, "w", encoding="utf-8") as f:
        f.write("\n".join(header + lines))
    print(f"[CLEAN] 清单已存档: {ARCHIVE} ({nfiles} 文件, {nbytes/1048576:.1f} MB)")

    targets = [t for t in sorted(os.listdir(OLD)) if t not in KEEP]
    print(f"[CLEAN] 待删除 {len(targets)} 个顶层目录: {targets}")
    if dry:
        print("[CLEAN] [DRY] 未删除")
        return

    for t in targets:
        p = os.path.join(OLD, t)
        try:
            shutil.rmtree(p)
            print(f"[CLEAN] 已删除 {t}")
        except Exception as exc:
            print(f"[CLEAN] 删除失败 {t}: {exc}")

    remain = sorted(os.listdir(OLD))
    print(f"[CLEAN] 剩余: {remain}")


main()
