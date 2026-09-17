"""功能完整性审计：把「已定义的操作符」与「UI/header 中可触达的操作符」对比。

任何"定义了但无处调用"的操作符，就是简化时丢失的功能入口。
"""
import re

BASE = r"E:\AI\geren\chajian_guanliqi\bl_plugin_manager"
ops_src = open(f"{BASE}/operators.py", encoding="utf-8").read()
ui_src = open(f"{BASE}/ui.py", encoding="utf-8").read()
pref_src = open(f"{BASE}/preferences.py", encoding="utf-8").read()

defined = set(re.findall(r'bl_idname\s*=\s*"plugin_manager\.([a-z_]+)"', ops_src))

ui_refs = set(re.findall(r'"plugin_manager\.([a-z_]+)"', ui_src))
pref_refs = set(re.findall(r'"plugin_manager\.([a-z_]+)"', pref_src))
reachable = ui_refs | pref_refs

# 这些是"被别的操作符内部调用"或纯弹窗/内部使用的，不算缺失
interactive_only = {
    # 由 scan_inbox 内部自动弹出的报告窗口
    "show_report",
    # 由 assign_category / batch_set_category 的 EnumProperty 承载
    "set_category",
    # 由 store_open 弹窗内条目调用
    "store_install",
}

unreachable = sorted(defined - reachable - interactive_only)

print(f"定义的操作符: {len(defined)}")
print(f"UI/prefs 可触达: {len(reachable & defined)}")
print(f"内部/弹窗调用: {len(interactive_only & defined)}")
print(f"未被任何入口引用: {len(unreachable)}")
for u in unreachable:
    print("   ! ", u)

# 反向：UI 引用了但没定义的
undefined = sorted(reachable - defined)
print(f"\nUI 引用但未定义: {len(undefined)}")
for u in undefined:
    print("   ! ", u)

# 关键功能抽查（这些必须在 UI 里能找到入口）
must_have = {
    "pick_library_path": "选择库目录", "setup_library": "启用/修复",
    "refresh": "刷新", "show_warnings": "诊断", "unmount_library": "卸载挂载",
    "toggle": "启停", "set_startup": "自启标记", "toggle_select": "勾选",
    "select_all": "全选", "batch_enable": "批量启停", "batch_set_category": "批量分类",
    "batch_set_startup": "批量自启", "add_category": "新建分类",
    "pick_category": "选分类", "rename_category": "重命名分类",
    "delete_category": "删除分类", "reset_auto_categories": "清理自动分类",
    "enable_pack": "启用整组", "disable_pack": "停用整组",
    "rename_display": "显示名称", "assign_category": "移入分类",
    "edit_meta": "编辑信息", "open_folder": "打开文件夹",
    "remove_plugin": "移除", "set_favorite": "收藏",
    "import_plugin": "导入文件夹", "import_zip": "导入压缩包",
    "scan_inbox": "扫描投放区", "store_open": "浏览商店",
    "store_sync": "刷新商店", "check_updates": "检查更新",
    "update_all": "一键更新", "unify_store": "统一官方商店",
    "copy_report": "复制报告", "open_report_log": "打开日志",
    "clear_updates": "清除更新标记", "scan_candidates": "扫描可收编",
    "import_candidates": "收编全部", "apply_startup": "同步自启",
    "cancel_compat": "取消兼容性测试",
}
missing_must = [k for k in must_have if k not in reachable]
print(f"\n关键功能入口缺失: {len(missing_must)}")
for m in missing_must:
    print("   ! ", m, must_have[m])

print("\n=== 结论 ===")
ok = not unreachable and not undefined and not missing_must
print("功能完整" if ok else "存在缺口，见上")
