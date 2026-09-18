"""跨电脑匹配：本机扫描结果必须连接到同一份总资料库条目。"""

import importlib
import sys
import types
import unittest


def _module():
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module("bl_plugin_manager.services.catalog_sync")


def _entry(rel, name, kind="addon", pkg_id="", folder=None):
    return {
        "rel": rel, "kind": kind, "pkg_id": pkg_id, "id": pkg_id,
        "name": name, "folder_name": folder or rel.split("/")[-1],
    }


class LinkScanTests(unittest.TestCase):
    def test_extension_manifest_id_matches_across_different_folder_names(self):
        mod = _module()
        catalog = {"ext:prompt-tools": {
            "plugin_id": "ext:prompt-tools", "kind": "extension",
            "pkg_id": "prompt_tools", "name": "Prompt Tools",
            "display_name": "提示词工具", "category": "AI", "note": "常用",
        }}
        result = mod.link_scan(catalog, {}, [
            _entry("extensions/提示词工具_v2", "Prompt Tools",
                   kind="extension", pkg_id="prompt_tools"),
        ])
        record = result.plugins["ext:prompt-tools"]
        self.assertEqual(record["display_name"], "提示词工具")
        self.assertEqual(record["category"], "AI")
        self.assertEqual(record["note"], "常用")
        self.assertEqual(result.bindings["ext:prompt-tools"], "extensions/提示词工具_v2")
        self.assertEqual(result.added, 0)
        self.assertEqual(result.matched, 1)

    def test_renamed_folder_still_matches_via_historical_hint(self):
        mod = _module()
        catalog = {"addon:mesh-tools": {
            "plugin_id": "addon:mesh-tools", "kind": "addon",
            "name": "Mesh Tools", "display_name": "网格工具",
            "folders": ["MeshTools_v2.1"], "names": ["mesh tools"],
        }}
        result = mod.link_scan(catalog, {}, [_entry("addons/MeshTools", "Mesh Tools")])
        self.assertIn("addon:mesh-tools", result.plugins)
        self.assertEqual(len(result.plugins), 1, "改名后不得产生重复条目")
        self.assertEqual(result.plugins["addon:mesh-tools"]["display_name"], "网格工具")
        self.assertIn("MeshTools", result.plugins["addon:mesh-tools"]["folders"])

    def test_second_install_in_another_folder_does_not_steal_alias(self):
        """同一台电脑上同名插件的第二个副本应另立条目，不抢占原记录别名。"""
        mod = _module()
        catalog = {"addon:legacy-demo": {
            "plugin_id": "addon:legacy-demo", "kind": "addon",
            "name": "Legacy Demo", "display_name": "我的别名",
            "names": ["legacy-demo"], "folders": ["LegacyDemo"],
        }}
        bindings = {"addon:legacy-demo": "addons/LegacyDemo"}
        # 原目录仍存在于本机 → 这是第二个副本。
        result = mod.link_scan(catalog, bindings, [
            _entry("addons/LegacyDemo", "Legacy Demo"),
            _entry("addons/IdemDemo", "Legacy Demo"),
        ], occupied_folders={"legacydemo"})
        self.assertEqual(len(result.plugins), 2, "第二个副本应新建条目")
        self.assertEqual(result.assignment["addons/LegacyDemo"], "addon:legacy-demo")
        second = result.assignment["addons/IdemDemo"]
        self.assertNotEqual(second, "addon:legacy-demo")
        self.assertEqual(result.plugins["addon:legacy-demo"]["display_name"], "我的别名")

    def test_same_folder_name_change_is_a_rename_not_a_duplicate(self):
        """插件在原目录改名（版本不变）时必须连回原记录并刷新名称。"""
        mod = _module()
        catalog = {"addon:old-name": {
            "plugin_id": "addon:old-name", "kind": "addon",
            "name": "Old Name", "display_name": "我的别名",
            "category": "工具", "names": ["old-name"], "folders": ["SameFolder"],
        }}
        result = mod.link_scan(catalog, {}, [
            _entry("addons/SameFolder", "New Name"),
        ])
        self.assertEqual(len(result.plugins), 1, "改名不得产生重复条目")
        self.assertEqual(result.present, {"addon:old-name"})
        record = result.plugins["addon:old-name"]
        self.assertEqual(record["name"], "New Name", "名称应刷新为新声明名")
        self.assertEqual(record["display_name"], "我的别名", "别名必须保留")
        self.assertEqual(record["category"], "工具")
        self.assertIn("new-name", record["names"])

    def test_kind_mismatch_is_not_matched(self):
        mod = _module()
        catalog = {"addon:demo": {
            "plugin_id": "addon:demo", "kind": "addon",
            "name": "Demo", "display_name": "别名", "folders": ["Demo"],
        }}
        result = mod.link_scan(catalog, {}, [_entry("extensions/Demo", "Demo", kind="extension")])
        self.assertNotIn("addon:demo", result.present)
        self.assertNotEqual(result.assignment["extensions/Demo"], "addon:demo")

    def test_user_fields_are_never_overwritten_by_scan(self):
        mod = _module()
        catalog = {"addon:demo": {
            "plugin_id": "addon:demo", "kind": "addon", "name": "Demo",
            "version": "1.0", "folders": ["Demo"], "names": ["demo"],
            "display_name": "别名", "category": "我的分类",
            "tags": ["a"], "note": "我的备注", "favorite": True,
        }}
        result = mod.link_scan(catalog, {}, [
            {**_entry("addons/Demo", "Demo"), "version": "2.0",
             "description": "新描述", "author": "新作者"},
        ])
        record = result.plugins["addon:demo"]
        self.assertEqual(record["version"], "2.0", "插件自带元数据应刷新")
        self.assertEqual(record["description"], "新描述")
        self.assertEqual(record["author"], "新作者")
        for field, value in (("display_name", "别名"), ("category", "我的分类"),
                             ("tags", ["a"]), ("note", "我的备注"), ("favorite", True)):
            self.assertEqual(record[field], value, f"{field} 属于用户数据，不得被扫描覆盖")

    def test_folder_rename_links_when_name_unchanged(self):
        mod = _module()
        catalog = {"addon:demo": {
            "plugin_id": "addon:demo", "kind": "addon", "name": "Demo",
            "folders": ["Demo_1.0"], "names": ["demo"], "display_name": "别名",
        }}
        result = mod.link_scan(catalog, {}, [_entry("addons/Demo_2.0", "Demo")])
        self.assertEqual(result.present, {"addon:demo"})
        self.assertEqual(result.plugins["addon:demo"]["display_name"], "别名")
        self.assertEqual(len(result.plugins), 1)

    def test_missing_plugin_keeps_binding_and_record(self):
        mod = _module()
        catalog = {"addon:gone": {"plugin_id": "addon:gone", "kind": "addon",
                                  "name": "Gone", "display_name": "别名"}}
        result = mod.link_scan(catalog, {"addon:gone": "addons/Gone"}, [])
        self.assertEqual(result.bindings["addon:gone"], "addons/Gone")
        self.assertEqual(result.plugins["addon:gone"]["display_name"], "别名")
        self.assertEqual(result.present, set())

    def test_accumulated_bindings_only_grow_with_scans(self):
        mod = _module()
        catalog = {}
        first = mod.link_scan(catalog, {}, [_entry("addons/A", "A"), _entry("addons/B", "B")])
        self.assertEqual(len(first.bindings), 2)
        second = mod.link_scan(first.plugins, first.bindings, [_entry("addons/A", "A")])
        self.assertIn(second.assignment["addons/A"], second.plugins)
        # B 这次没扫到：绑定保留，记录不丢。
        self.assertEqual(len(second.bindings), 2)
        self.assertEqual(len(second.plugins), 2)

    def test_slug_is_stable_and_unicode_safe(self):
        mod = _module()
        self.assertEqual(mod.slug("Mesh  Tools!!"), "mesh-tools")
        self.assertEqual(mod.slug("快速打组"), "快速打组")
        self.assertEqual(mod.slug(""), "")


class MigrateLegacyTests(unittest.TestCase):
    def test_legacy_path_keys_become_plugin_ids_and_keep_user_data(self):
        mod = _module()
        legacy = {
            "addons/MeshTools_v2.1": {
                "rel": "addons/MeshTools_v2.1", "kind": "addon",
                "name": "Mesh Tools", "version": "2.1",
                "display_name": "网格工具", "category": "建模",
                "note": "常用", "tags": ["mesh"], "favorite": True, "startup": True,
            },
        }
        plugins, bindings = mod.migrate_legacy(legacy)
        self.assertEqual(len(plugins), 1)
        plugin_id = next(iter(plugins))
        record = plugins[plugin_id]
        self.assertEqual(bindings[plugin_id], "addons/MeshTools_v2.1")
        self.assertEqual(record["display_name"], "网格工具")
        self.assertEqual(record["category"], "建模")
        self.assertEqual(record["note"], "常用")
        self.assertTrue(record["favorite"])
        self.assertEqual(record["kind"], "addon")

    def test_legacy_entries_that_collide_get_distinct_ids(self):
        mod = _module()
        legacy = {
            "addons/A": {"rel": "addons/A", "kind": "addon", "name": "Same Name"},
            "addons/B": {"rel": "addons/B", "kind": "addon", "name": "Same Name"},
        }
        plugins, _bindings = mod.migrate_legacy(legacy)
        self.assertEqual(len(plugins), 2)


if __name__ == "__main__":
    unittest.main()
