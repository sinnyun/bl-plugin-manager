"""扫描插件库中所有扩展插件的 manifest，报告不符合 Blender 规范之处。"""
import os
import re

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
EXT = os.path.join(LIB, "extensions")

try:
    import tomllib
except Exception:
    tomllib = None

problems = []
ok = 0
for e in sorted(os.scandir(EXT), key=lambda x: x.name.lower()):
    if not e.is_dir() or e.name.startswith("."):
        continue
    mp = os.path.join(e.path, "blender_manifest.toml")
    if not os.path.isfile(mp):
        problems.append((e.name, "缺少 blender_manifest.toml"))
        continue
    raw = open(mp, "rb").read()
    data = None
    if tomllib:
        try:
            data = tomllib.loads(raw.decode("utf-8", errors="replace"))
        except Exception as ex:
            problems.append((e.name, f"TOML 解析失败: {ex}"))
            continue
    if data is None:
        continue

    issues = []
    # 目录名必须是合法标识符（模块名由此决定）
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", e.name):
        issues.append(f"目录名非法(模块名): '{e.name}'")
    pid = str(data.get("id", ""))
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pid):
        issues.append(f"id 非法: '{pid}'")
    lic = data.get("license")
    if lic is None:
        issues.append("缺少 license")
    elif isinstance(lic, str):
        issues.append(f"license 是字符串(需列表): {lic!r}")
    elif not (isinstance(lic, list) and lic and all(isinstance(x, str) and x for x in lic)):
        issues.append(f"license 非法: {lic!r}")
    ver = str(data.get("version", ""))
    if not re.fullmatch(r"\d+(\.\d+)*", ver):
        issues.append(f"版本号格式异常: {ver!r}")

    if issues:
        problems.append((e.name, "; ".join(issues)))
    else:
        ok += 1

print(f"扩展插件: 正常 {ok}，有问题 {len(problems)}")
for name, issue in problems:
    print(f"  ! {name}: {issue}")
