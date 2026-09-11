"""把插件库管理器打包成可安装的 zip（供 Blender「从磁盘安装」使用）。

用法:
    python build_zip.py
产物:
    dist/bl_plugin_manager-<version>.zip
"""

from __future__ import annotations

import os
import re
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "bl_plugin_manager")
OUT_DIR = os.path.join(HERE, "dist")

SKIP_DIRS = {"__pycache__", ".git"}
SKIP_EXT = {".pyc", ".pyo"}


def read_version() -> str:
    try:
        with open(os.path.join(PKG, "constants.py"), "r", encoding="utf-8") as fh:
            src = fh.read()
        m = re.search(r"ADDON_VERSION_STR\s*=\s*[\"']([^\"']+)", src)
        return m.group(1) if m else "0.0.0"
    except OSError:
        return "0.0.0"


def main() -> None:
    version = read_version()
    os.makedirs(OUT_DIR, exist_ok=True)
    zip_path = os.path.join(OUT_DIR, f"bl_plugin_manager-{version}.zip")
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder, dirs, files in os.walk(PKG):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fn in files:
                if os.path.splitext(fn)[1] in SKIP_EXT:
                    continue
                full = os.path.join(folder, fn)
                arc = os.path.join("bl_plugin_manager", os.path.relpath(full, PKG))
                zf.write(full, arc.replace("\\", "/"))
                count += 1
    print(f"已打包 {count} 个文件 -> {zip_path}")


if __name__ == "__main__":
    main()
