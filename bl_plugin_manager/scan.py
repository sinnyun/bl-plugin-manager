"""插件识别：判定传统插件 / 扩展插件，解析版本与描述元数据。

设计目标：
* 解析 bl_info 时只做静态 AST 求值，绝不 exec 用户插件代码（避免副作用与卡死）。
* 同时支持 manifest（Blender 4.2+ 扩展）与 bl_info（传统插件）两种形态。
* 支持从 zip 中嗅探插件根目录。
"""

from __future__ import annotations

import ast
import os
import re
import zipfile

from . import constants as C

try:  # Python 3.11+（Blender 4.2+ 自带）
    import tomllib
except Exception:  # pragma: no cover
    tomllib = None


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def is_ignored(name: str) -> bool:
    return name in C.IGNORED_NAMES or name.startswith(".")


def version_str(value) -> str:
    """把 (1, 2, 3) / "1.2.3" / 1.2 统一成 "1.2.3"。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ".".join(str(x) for x in value)
    return str(value).strip()


def version_key(value):
    """用于比较的数值元组。"""
    nums = re.findall(r"\d+", version_str(value))
    return tuple(int(n) for n in nums) if nums else (0,)


def blender_compat(min_ver, max_ver, current=None) -> tuple[str, str]:
    """判断插件与当前 Blender 版本的兼容性。

    返回 (状态, 说明)，状态取值：
    * "ok"        ：兼容（当前版本落在 [min, max] 内，或未声明限制）
    * "too_old"   ：插件要求更高的 Blender 版本
    * "too_new"   ：插件声明了最高版本，当前 Blender 过新（会被拒绝加载）
    * "unknown"   ：插件未声明任何版本要求

    current 为 None 时取当前 Blender 版本。
    """
    if current is None:
        try:
            import bpy

            current = bpy.app.version
        except Exception:
            current = (0, 0, 0)

    has_min = bool(any(version_key(min_ver)))
    has_max = bool(any(version_key(max_ver)))
    if not has_min and not has_max:
        return "unknown", "未声明支持的 Blender 版本"

    if has_max:
        need_max = version_key(max_ver)
        cur = tuple(current[: len(need_max)])
        if cur > tuple(need_max):
            return "too_new", f"最高仅支持 {version_str(max_ver)}"

    if has_min:
        need = version_key(min_ver)
        cur = tuple(current[: len(need)])
        if cur < tuple(need):
            return "too_old", f"需要 Blender {version_str(min_ver)} 或更高"

    lo = version_str(min_ver) or "—"
    hi = version_str(max_ver) or "—"
    return "ok", f"支持 {lo} ~ {hi}"


def supported_range(min_ver, max_ver) -> str:
    """插件声明支持的 Blender 版本范围（纯描述，与当前版本无关）。

    这样无论你之后换到哪个 Blender 版本，都能直接看到该插件支持哪些版本：
    * 有下限有上限 → "4.2.0 ~ 5.0.0"
    * 只有下限     → "≥ 4.2.0"
    * 只有上限     → "≤ 5.0.0"
    * 都没声明     → "未声明"
    """
    lo = version_str(min_ver)
    hi = version_str(max_ver)
    if lo and hi:
        return f"{lo} ~ {hi}"
    if lo:
        return f"≥ {lo}"
    if hi:
        return f"≤ {hi}"
    return "未声明"


def max_version_label(max_ver) -> str:
    """列表右侧文案：插件**最高支持的 Blender 版本**。

    * 声明了上限   → "≤ 5.0.0"
    * 未声明上限   → "不限"

    这样无论当前用的是哪个 Blender 版本，都能直接看出该插件的版本天花板；
    是否兼容当前版本由颜色/图标单独标注。
    """
    hi = version_str(max_ver)
    return f"≤ {hi}" if hi else "不限"


def compat_label(min_ver, max_ver, current=None) -> str:
    """兼容性文案（保留范围描述，用于详情区展示完整信息）。"""
    return supported_range(min_ver, max_ver)


def compare_versions(a, b) -> int:
    """a>b 返回 1，a<b 返回 -1，相等返回 0。"""
    ka, kb = version_key(a), version_key(b)
    length = max(len(ka), len(kb))
    ka = ka + (0,) * (length - len(ka))
    kb = kb + (0,) * (length - len(kb))
    return (ka > kb) - (ka < kb)


# ---------------------------------------------------------------------------
# bl_info（传统插件）
# ---------------------------------------------------------------------------
def _regex_bl_info(src: str) -> dict | None:
    """兜底：bl_info 含变量/注释导致 literal_eval 失败时，用正则粗取关键字段。"""
    m = re.search(r"bl_info\s*[:=]", src)
    if not m:
        return None
    window = src[m.start(): m.start() + 4000]

    def grab(key: str) -> str:
        mm = re.search(rf"['\"]{key}['\"]\s*:\s*['\"]([^'\"]*)['\"]", window)
        return mm.group(1) if mm else ""

    def grab_tuple(key: str) -> str:
        mm = re.search(rf"['\"]{key}['\"]\s*:\s*\(([^)]*)\)", window)
        if not mm:
            return ""
        return ".".join(re.findall(r"\d+", mm.group(1)))

    name = grab("name")
    version = grab_tuple("version")
    if not name and not version:
        return None
    return {
        "name": name,
        "version": version,
        "blender_min": grab_tuple("blender"),
        "blender_max": grab_tuple("blender_max"),
        "author": grab("author"),
        "description": grab("description"),
        "category": grab("category"),
        "doc_url": grab("doc_url"),
        "location": grab("location"),
        "tags": [],
        "id": "",
    }


def read_bl_info(plugin_dir: str) -> dict | None:
    init_path = os.path.join(plugin_dir, C.LEGACY_INIT)
    if not os.path.isfile(init_path):
        return None
    try:
        with open(init_path, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError:
        return None

    try:
        tree = ast.parse(src)
    except SyntaxError:
        return _regex_bl_info(src)

    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "bl_info":
                try:
                    info = ast.literal_eval(value)
                except Exception:
                    return _regex_bl_info(src)
                if isinstance(info, dict):
                    return _normalize_bl_info(info)
    return _regex_bl_info(src)


def _normalize_bl_info(info: dict) -> dict:
    return {
        "name": str(info.get("name", "")).strip(),
        "version": version_str(info.get("version")),
        "blender_min": version_str(info.get("blender")),
        "blender_max": version_str(info.get("blender_max")),
        "author": str(info.get("author", "")).strip(),
        "description": str(info.get("description", "")).strip(),
        "category": str(info.get("category", "")).strip(),
        "doc_url": str(info.get("doc_url", "")).strip(),
        "location": str(info.get("location", "")).strip(),
        "tags": [],
        "id": "",
    }


# ---------------------------------------------------------------------------
# blender_manifest.toml（扩展插件）
# ---------------------------------------------------------------------------
def _fallback_toml(text: str) -> dict:
    """极简 TOML 解析：只处理顶层 key = value。"""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.split("#", 1)[0].strip()
        if val.startswith("[") and val.endswith("]"):
            out[key.strip()] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
        else:
            out[key.strip()] = val.strip().strip("'\"")
    return out


def read_manifest(plugin_dir: str) -> dict | None:
    path = os.path.join(plugin_dir, C.MANIFEST_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    data = None
    if tomllib is not None:
        try:
            data = tomllib.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            data = None
    if not isinstance(data, dict):
        data = _fallback_toml(raw.decode("utf-8", errors="replace"))
    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return {
        "id": str(data.get("id", "")).strip(),
        "name": str(data.get("name", "")).strip(),
        "version": version_str(data.get("version")),
        "blender_min": version_str(data.get("blender_version_min")),
        # 扩展插件可声明最高兼容版本；超过它 Blender 会拒绝加载（如 mmd_tools）
        "blender_max": version_str(data.get("blender_version_max")),
        "author": str(data.get("maintainer", "")).strip(),
        "description": str(data.get("tagline", "")).strip(),
        "category": ", ".join(str(t) for t in tags[:2]),
        "doc_url": str(data.get("website", "")).strip(),
        "location": "",
        "tags": [str(t) for t in tags],
        "type": str(data.get("type", "")).strip(),
    }


# ---------------------------------------------------------------------------
# 类型判定
# ---------------------------------------------------------------------------
def detect_kind(plugin_dir: str) -> str | None:
    if not os.path.isdir(plugin_dir):
        return None
    if os.path.isfile(os.path.join(plugin_dir, C.MANIFEST_NAME)):
        return C.KIND_EXTENSION
    if os.path.isfile(os.path.join(plugin_dir, C.LEGACY_INIT)):
        return C.KIND_ADDON
    return None


def validate_plugin(plugin_dir: str) -> tuple[str | None, str]:
    """判断目录是否为可用的插件，并给出不能使用时的具体原因。

    返回 (kind, reason)：kind 为 None 时 reason 说明为什么不能用。
    比 detect_kind 更严格——会校验 manifest 是否可解析、bl_info 是否可读到，
    便于把"装进去却用不了"的原因如实报给用户。
    """
    if not os.path.isdir(plugin_dir):
        return None, "路径不是目录"

    has_manifest = os.path.isfile(os.path.join(plugin_dir, C.MANIFEST_NAME))
    has_init = os.path.isfile(os.path.join(plugin_dir, C.LEGACY_INIT))

    if has_manifest:
        data = read_manifest(plugin_dir)
        if data is None:
            return None, "blender_manifest.toml 无法解析"
        # 关键字段缺失是 Blender 拒绝加载的最常见原因
        missing = [k for k in ("id", "version", "name") if not str(data.get(k, "")).strip()]
        if missing:
            return None, f"blender_manifest.toml 缺少必填字段: {', '.join(missing)}"
        return C.KIND_EXTENSION, ""

    if has_init:
        info = read_bl_info(plugin_dir)
        if info is None:
            return None, "__init__.py 中没有找到 bl_info（不是有效的传统插件）"
        return C.KIND_ADDON, ""

    return None, "缺少 blender_manifest.toml，也没有含 bl_info 的 __init__.py"


def read_meta(plugin_dir: str, kind: str) -> dict | None:
    if kind == C.KIND_EXTENSION:
        meta = read_manifest(plugin_dir)
    else:
        meta = read_bl_info(plugin_dir)
    if meta is None:
        meta = {"name": "", "version": "", "id": "", "tags": [], "blender_max": ""}
    meta.setdefault("blender_max", "")
    return meta


def metadata_fingerprint(plugin_dir: str, kind: str) -> list[list[int | str]]:
    """Return a cheap fingerprint for files that define plugin metadata.

    A plugin can change its name, description or compatibility declaration without
    changing its own version.  Synchronization therefore needs to detect changes
    to the metadata source itself, not only compare the plugin version.
    """
    names = (C.MANIFEST_NAME,) if kind == C.KIND_EXTENSION else (C.LEGACY_INIT,)
    out: list[list[int | str]] = []
    for name in names:
        path = os.path.join(plugin_dir, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        out.append([name, int(st.st_mtime_ns), int(st.st_size)])
    return out


# ---------------------------------------------------------------------------
# manifest 合规校验（扩展插件被 Blender 拒绝加载的常见原因）
# ---------------------------------------------------------------------------
_MODULE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def manifest_issues(plugin_dir: str, raw: bytes | None = None,
                    data: dict | None = None) -> list[str]:
    """检查扩展插件目录的 manifest 是否满足 Blender 的加载要求。

    返回问题描述列表；空列表表示没有问题。这些正是 Blender 在扫描扩展仓库时
    会打印 "skipping" / "must be a list" 的原因。

    raw / data 可传入已读到的内容，避免重复读文件（批量校验时显著更快）。
    """
    issues: list[str] = []
    folder = os.path.basename(os.path.normpath(plugin_dir))
    path = os.path.join(plugin_dir, C.MANIFEST_NAME)

    # 目录名即模块名，必须是合法标识符
    if not _MODULE_RE.fullmatch(folder):
        issues.append(f"目录名 '{folder}' 不是合法模块名（含空格或特殊字符），Blender 会跳过该扩展")

    if data is None and raw is None:
        if not os.path.isfile(path):
            return issues + ["缺少 blender_manifest.toml"]
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return issues + ["无法读取 blender_manifest.toml"]

    if data is None:
        if tomllib is not None:
            try:
                data = tomllib.loads(raw.decode("utf-8", errors="replace"))
            except Exception as exc:
                return issues + [f"manifest 解析失败: {exc}"]
        if not isinstance(data, dict):
            data = _fallback_toml(raw.decode("utf-8", errors="replace"))

    pid = str(data.get("id", "")).strip()
    if not pid:
        issues.append("缺少 id")
    elif not _MODULE_RE.fullmatch(pid):
        issues.append(f"id '{pid}' 不是合法标识符")

    if not str(data.get("blender_version_min", "")).strip():
        issues.append("缺少 blender_version_min")

    lic = data.get("license")
    if lic is None:
        issues.append("缺少 license")
    elif isinstance(lic, str):
        issues.append("license 必须是列表，当前是字符串")
    elif isinstance(lic, list):
        if not lic or not all(isinstance(x, str) and x for x in lic):
            issues.append("license 必须是非空字符串列表")
    else:
        issues.append(f"license 类型非法: {type(lic).__name__}")

    if not str(data.get("name", "")).strip():
        issues.append("缺少 name")
    if not str(data.get("version", "")).strip():
        issues.append("缺少 version")

    return issues


# ---------------------------------------------------------------------------
# zip 支持
# ---------------------------------------------------------------------------
def inspect_zip(zip_path: str) -> dict | None:
    """返回 {"prefix": 插件根前缀(可能为空), "kind": ...} 或 None。"""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [n.replace("\\", "/") for n in zf.namelist() if not n.endswith("/")]
    except (zipfile.BadZipFile, OSError):
        return None
    if not names:
        return None

    roots = []
    for n in names:
        head = n.split("/")[0]
        if head not in roots:
            roots.append(head)

    def probe(prefix: str):
        if prefix:
            if not any(n == prefix + "/" + C.MANIFEST_NAME for n in names) and not any(
                n == prefix + "/" + C.LEGACY_INIT for n in names
            ):
                return None
            # 前缀下必须确实有文件
            if not any(n.startswith(prefix + "/") for n in names):
                return None
        if any(n == (prefix + "/" if prefix else "") + C.MANIFEST_NAME for n in names):
            return C.KIND_EXTENSION
        if any(n == (prefix + "/" if prefix else "") + C.LEGACY_INIT for n in names):
            return C.KIND_ADDON
        return None

    # 优先单根目录结构
    if len(roots) == 1:
        kind = probe(roots[0])
        if kind:
            return {"prefix": roots[0], "kind": kind}
    kind = probe("")
    if kind:
        return {"prefix": "", "kind": kind}
    # 退化：任一子目录看起来像插件
    for prefix in roots:
        kind = probe(prefix)
        if kind:
            return {"prefix": prefix, "kind": kind}
    return None


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
def scan_dir(base: str, kind_hint: str | None = None) -> list[dict]:
    """扫描一个容器目录，返回其下每个插件子目录的信息。"""
    results = []
    if not os.path.isdir(base):
        return results
    try:
        entries = sorted(os.scandir(base), key=lambda e: e.name.lower())
    except OSError:
        return results
    for entry in entries:
        if not entry.is_dir() or is_ignored(entry.name):
            continue
        # 严格校验：目录必须确实含 manifest 或可用的 __init__.py（含 bl_info）
        # 才能算插件。此前用 `detect_kind(...) or kind_hint` 会把普通文件夹
        # （如插件自带的 DLL 目录）当成插件混进列表。
        kind, _reason = validate_plugin(entry.path)
        if kind is None:
            results.append({
                "abs": entry.path, "name": entry.name, "kind": None,
                "valid": False, "meta": {}, "reason": _reason,
            })
            continue
        meta = read_meta(entry.path, kind) or {}
        results.append(
            {
                "abs": entry.path,
                "name": entry.name,
                "kind": kind,
                "valid": kind is not None,
                "meta": meta,
            }
        )
    return results
