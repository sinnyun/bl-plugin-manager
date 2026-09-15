"""只读盘点：列出所有插件来源、数量与体积。不修改任何文件。"""
import json
import os

SRC_ROOTS = {
    "用户配置 addons (5.2 默认安装位置)": r"C:\Users\sanliD\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons",
    "用户配置 addons (4.5)": r"C:\Users\sanliD\AppData\Roaming\Blender Foundation\Blender\4.5\scripts\addons",
    "D: 脚本目录 chajian": r"D:\nastongbu\qitaziliao\blender\chajian\addons",
    "D: 脚本目录 gongjuxiaolv": r"D:\nastongbu\qitaziliao\blender\gongjuxiaolv_chajian\addons",
    "D: 脚本目录 zidingyi": r"D:\nastongbu\qitaziliao\blender\zidingyi_chajian\addons",
    "D: 脚本目录 donghuaguge": r"D:\nastongbu\qitaziliao\blender\donghuaguge_chajian\addons",
    "D: 脚本目录 jianmo": r"D:\nastongbu\qitaziliao\blender\jianmo_chajian\addons",
    "D: 脚本目录 python_jiaoben": r"D:\nastongbu\qitaziliao\blender\python_jiaoben\addons",
    "D: 扩展仓库 caizhixuanran": r"D:\nastongbu\qitaziliao\blender\caizhixuanran_chajian",
    "D: 扩展仓库 zidingyi": r"D:\nastongbu\qitaziliao\blender\zidingyi_chajian",
    "D: 扩展仓库 gongjuxiaolv": r"D:\nastongbu\qitaziliao\blender\gongjuxiaolv_chajian",
    "D: 扩展仓库 donghuaguge": r"D:\nastongbu\qitaziliao\blender\donghuaguge_chajian",
    "D: 扩展仓库 wulimoni": r"D:\nastongbu\qitaziliao\blender\wulimoni_chajian",
    "D: 扩展仓库 jianmo": r"D:\nastongbu\qitaziliao\blender\jianmo_chajian",
}
BLENDER_DIR = r"D:\nastongbu\qitaziliao\blender"
TARGET = r"D:\nastongbu\qitaziliao\blender_addons"

SKIP = {"__pycache__", ".git", ".blender_ext", ".pm", "node_modules", ".local", ".cache"}


def dir_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def has_manifest(p):
    return os.path.isfile(os.path.join(p, "blender_manifest.toml"))


def has_init(p):
    return os.path.isfile(os.path.join(p, "__init__.py"))


def scan(base):
    out = {"exists": os.path.isdir(base), "plugins": [], "loose_files": []}
    if not out["exists"]:
        return out
    for e in sorted(os.scandir(base), key=lambda x: x.name.lower()):
        if e.is_dir():
            if e.name in SKIP or e.name.startswith("."):
                continue
            kind = "extension" if has_manifest(e.path) else ("addon" if has_init(e.path) else "?")
            out["plugins"].append({"name": e.name, "kind": kind})
        elif e.is_file():
            if e.name in SKIP:
                continue
            try:
                sz = e.stat().st_size
            except OSError:
                sz = 0
            out["loose_files"].append({"name": e.name, "size": sz})
    return out


def main():
    report = {"sources": {}, "blender_dir": {}, "summary": {}}
    total_plugins = 0
    for label, base in SRC_ROOTS.items():
        r = scan(base)
        report["sources"][label] = {
            "path": base,
            "exists": r["exists"],
            "count": len(r["plugins"]),
            "kinds": {
                "addon": sum(1 for p in r["plugins"] if p["kind"] == "addon"),
                "extension": sum(1 for p in r["plugins"] if p["kind"] == "extension"),
                "unknown": sum(1 for p in r["plugins"] if p["kind"] == "?"),
            },
            "names": [p["name"] for p in r["plugins"]],
            "loose_files": [f["name"] for f in r["loose_files"]],
        }
        total_plugins += len(r["plugins"])

    # blender 目录下未安装的 zip / 散装插件
    bd = {"zips": [], "dirs": []}
    if os.path.isdir(BLENDER_DIR):
        for e in sorted(os.scandir(BLENDER_DIR), key=lambda x: x.name.lower()):
            if e.name in SKIP or e.name.startswith("."):
                continue
            if e.is_dir():
                bd["dirs"].append(e.name)
            elif e.is_file() and e.name.lower().endswith(".zip"):
                try:
                    bd["zips"].append({"name": e.name, "mb": round(e.stat().st_size / 1048576, 1)})
                except OSError:
                    pass
    report["blender_dir"] = bd

    report["summary"] = {
        "total_installed_plugin_dirs": total_plugins,
        "target": TARGET,
        "target_exists": os.path.isdir(TARGET),
    }

    # 统计体积（仅目标候选，避免过慢）
    sizes = {}
    for label, base in SRC_ROOTS.items():
        if os.path.isdir(base):
            sizes[label] = round(dir_size(base) / 1048576, 1)
    report["sizes_mb"] = sizes

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
