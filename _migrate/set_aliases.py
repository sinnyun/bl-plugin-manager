"""为插件库中的所有插件补齐合适的中文别名（显示名称）。

* 以文件夹名为主键（最稳定，不随 id 变化）；
* 只填 display_name（别名），不改实际插件名，也不动分类/启用状态；
* 顺便修掉之前遗留的异常别名（Auto Reload Images 的 "996"）；
* 纯标准库实现，可直接用系统 Python 运行（library.json 与 Blender 版本无关）。

用法：
    python set_aliases.py --dry     # 预演
    python set_aliases.py --run     # 写入
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

LIB = r"D:\nastongbu\qitaziliao\blender_addons"
DB = os.path.join(LIB, ".pm", "library.json")

# 文件夹名 -> 中文别名
ALIASES: dict[str, str] = {
    # ---------------- 传统插件 ----------------
    "abratools": "Abra 动画工具包",
    "ActionCommander": "动作管理器",
    "AI_Studio_V1": "AI 助手",
    "动画图层插件": "动画图层",
    "Auto_Reload_Images-Blender_addon-master": "自动重载图片",
    "rig_tools": "自动绑骨工具",
    "bbd_lite": "骨骼动力学 Lite",
    "BooleanQuadReady": "布尔四边准备",
    "botaniq_full": "植物库",
    "cats-blender-plugin-master": "Cats 模型处理",
    "Dimensions_Ruler": "尺寸标尺",
    "drag_import-102": "拖拽导入",
    "HairBricksAddon": "毛发砖块",
    "hdri_maker": "HDRI 环境制作",
    "icity": "3D 城市生成",
    "new_CDR": "导入 CorelDRAW",
    "InteriorEssentials-室内模型库": "室内设计资产",
    "blender_mcp": "Blender MCP 连接",
    "mixamo_blender4_main": "Mixamo 绑骨",
    "运动图形": "运动节点",
    "render_button": "渲染按钮",
    "Particles-X": "粒子系统 Pro",
    "Petik": "四边拓扑 Petik",
    "Pinguin_bl": "图片转 3D",
    "polyhavenassets": "Poly Haven 资产库",
    "polyviews": "多视图管理",
    "程序_天空_系统": "程序化天空",
    "Quick-Studio": "快速影棚",
    "retargify": "动作重定向",
    "Saved Views": "保存视图",
    "softwrap2": "拓扑传递 Softwrap",
    "stylizedshaders": "风格化着色器",
    "super_io": "超级导入导出",
    "碰撞骨骼": "摆动骨骼物理",
    "vector_to_3d": "矢量转 3D",
    "voxel_skinning": "体素热扩散蒙皮",
    "ZenSets": "集合管理 Zen Sets",
    # ---------------- 扩展插件 ----------------
    "antlandscape": "地形景观生成",
    "align_tools": "对齐工具",
    "amaranth": "综合增强 Amaranth",
    "Auto_Highlight_in_Outliner": "大纲自动高亮",
    "auto_mirror": "自动镜像",
    "auto_reload": "自动重载",
    "auto_rig_pro": "自动绑骨 Pro",
    "auto_rig_pro_quick_rig": "快速绑骨 Quick Rig",
    "Batch_Material_Helper": "批量材质助手",
    "blendguard": "文件安全检测",
    "BLT_Addon": "BLT 汉化翻译",
    "bonex": "骨骼物理模拟",
    "bool_tool": "布尔工具",
    "brushstroke_tools": "笔触绘画工具",
    "bulk_asset_tools": "批量资产管理",
    "cell_fracture": "细胞破碎",
    "chinese_text_input_redhalostudio": "中文输入",
    "copy_object_name_to_data": "对象名复制到数据",
    "theme_deep_grey": "深灰主题",
    "deep_paint": "立体绘画 Pro",
    "DJH_GeoNodes": "DJH 几何节点库",
    "dynamic_terrain": "动态地形",
    "EasyEnv": "单图环境生成",
    "edit_linked_library": "编辑链接库",
    "extra_curve_objectes": "额外曲线物体",
    "extra_mesh_objects": "额外网格物体",
    "Extra_Nodes": "额外几何节点",
    "extreme_pbr": "极致 PBR 材质",
    "fadeassets": "卡通风格资产",
    "FlowProManager": "流程资产管理器",
    "grease_pencil_tools": "蜡笔工具",
    "GroupPro": "打组 Pro",
    "hot_node": "快捷节点",
    "IMEBridge": "中文输入法桥接",
    "io_scene_max": "导入 3ds Max",
    "jiggle_physics": "抖动物理",
    "Key_Ops_Toolkit": "快捷键工具包",
    "lattice_helper": "晶格变形助手",
    "leafig": "边缘裁剪转网格",
    "looptools": "循环工具",
    "M4A1TOOLS": "M4A1 工具集",
    "material_utilities": "材质工具集",
    "mmd_tools": "MMD 工具",
    "n_panel_sub_tabs": "N 面板分组标签",
    "node_arrange": "节点排列",
    "openscatter": "开放散布",
    "orient_and_origin_to_selected": "原点到所选",
    "photographer": "摄影师（相机灯光）",
    "PlaceHelper": "放置助手",
    "plating_greeble_gen": "装甲板生成器",
    "plugin_manager_pro": "插件管理器 Pro",
    "PolyQuilt_Fork": "PolyQuilt 重拓扑",
    "popoti_align_helper": "POPOTI 对齐助手",
    "profiling_buddy": "性能分析助手",
    "proxy_picker": "代理选择器",
    "Quick_Asset_Saver": "快速资产管理",
    "ramp_generator": "渐变生成器",
    "retopoflow": "Retopoflow 重拓扑",
    "RigFlex": "软体骨骼物理",
    "rigi_all": "Rigify 全流程加速",
    "simple_deform_helper": "简单形变助手",
    "stablegen": "AI 纹理生成",
    "stored_views": "视图存储",
    "Straighten_UV": "UV 展平",
    "t3d_gn_presets": "T3D 几何节点预设",
    "ucupaint": "分层绘画 Ucupaint",
    "univ": "智能 UV 工具",
    "UV_Flatten_Tool": "UV 展平工具",
    "vectart_import": "矢量图导入",
    "ZenUVChecker": "Zen UV 检查器",
}

# 需要清掉的历史遗留异常别名
BAD_ALIASES = {"996"}


def main():
    dry = "--dry" in sys.argv or "--run" not in sys.argv
    if not os.path.isfile(DB):
        print("找不到库文件:", DB)
        return

    with open(DB, "r", encoding="utf-8") as f:
        data = json.load(f)

    plugins = data.get("plugins", {})
    applied, already, missing, skipped_manual = [], [], [], []

    covered_folders = set()
    for key, rec in plugins.items():
        folder = rec.get("folder_name") or ""
        want = ALIASES.get(folder)
        cur = (rec.get("display_name") or "").strip()

        if want is None:
            continue
        covered_folders.add(folder)

        if cur == want:
            already.append(folder)
            continue
        # 用户自己设过（不是遗留垃圾值）就不覆盖
        if cur and cur not in BAD_ALIASES:
            skipped_manual.append((folder, cur, want))
            continue
        applied.append((folder, rec.get("name"), cur, want))
        if not dry:
            rec["display_name"] = want

    # 映射里有、但库里没有的文件夹名（拼写不符或未安装）
    for folder in ALIASES:
        if folder not in covered_folders:
            missing.append(folder)

    if not dry:
        # 备份后写入
        stamp = time.strftime("%Y%m%d_%H%M%S")
        shutil.copy2(DB, DB + f".bak_{stamp}")
        with open(DB, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"{'[预演] ' if dry else ''}将设置别名: {len(applied)}")
    for folder, name, old, new in applied:
        tail = f"（原: {old}）" if old else ""
        print(f"   {name or folder:44s} -> {new}{tail}")
    print(f"\n已是目标别名: {len(already)}")
    if skipped_manual:
        print(f"保留用户自定义（{len(skipped_manual)}）:")
        for folder, cur, want in skipped_manual:
            print(f"   {folder}: 保留「{cur}」(建议「{want}」)")
    if missing:
        print(f"\n映射中未匹配到（{len(missing)}）:")
        for m in missing:
            print("   !", m)
    if not dry:
        print(f"\n已写入 {DB}")


main()
