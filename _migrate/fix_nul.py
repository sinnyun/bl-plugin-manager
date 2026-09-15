"""修复 library.py 中因转义写坏而含真实 NUL 字节的一行。

改为用 translate 过滤控制字符，彻底避免源码里出现 \\x00 转义。
"""

import io
import os

P = "bl_plugin_manager/library.py"

with io.open(P, "rb") as f:
    data = f.read()

lines = data.split(b"\n")
target = None
for i, ln in enumerate(lines):
    if b"re.sub" in ln and b'"_", name' in ln and b"\x00" in ln:
        target = i
        break

if target is None:
    print("未定位到问题行（可能已修复）")
else:
    lines[target] = b"    name = name.translate(_CTRL_MAP)"
    print("已替换问题行:", target + 1)

src = b"\n".join(lines)

# 在 sanitize_name 之前插入控制字符映射表
marker = b"def sanitize_name(name: str) -> str:"
if b"_CTRL_MAP" not in src:
    inject = (
        b"# Control chars (0x00-0x1F) -> underscore, via translate so that the\n"
        b"# source never needs a literal NUL escape sequence.\n"
        b'_CTRL_MAP = dict((c, "_") for c in range(0x20))\n\n\n'
    )
    src = src.replace(marker, inject + marker, 1)
    print("已插入 _CTRL_MAP")

with io.open(P, "wb") as f:
    f.write(src)
    f.flush()
    os.fsync(f.fileno())

with io.open(P, "rb") as f:
    print("剩余 NUL 字节:", f.read().count(b"\x00"))
