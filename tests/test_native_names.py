"""Unit tests for runtime native display name formatting and non-destructive hooks."""

import importlib
import sys
import types
import unittest
from collections import namedtuple


def _load_native():
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module("bl_plugin_manager.native_names")


FakeManifest = namedtuple("FakeManifest", ("id", "name", "tagline"))


class NativeNamesTests(unittest.TestCase):
    def test_format_orig_cn_default_and_styles(self):
        mod = _load_native()
        self.assertEqual(
            mod.format_native_name("Node Wrangler", "节点管理", mod.STYLE_ORIG_CN),
            "Node Wrangler (节点管理)",
        )
        self.assertEqual(
            mod.format_native_name("Node Wrangler", "节点管理", mod.STYLE_CN_ORIG),
            "节点管理 (Node Wrangler)",
        )
        self.assertEqual(
            mod.format_native_name("Node Wrangler", "节点管理", mod.STYLE_CN_ONLY),
            "节点管理",
        )
        # Avoid duplicate wrapping when alias already contains original or equals original
        self.assertEqual(
            mod.format_native_name("Node Wrangler", "Node Wrangler", mod.STYLE_ORIG_CN),
            "Node Wrangler",
        )
        self.assertEqual(
            mod.format_native_name("Node Wrangler", "Node Wrangler 节点管理", mod.STYLE_ORIG_CN),
            "Node Wrangler 节点管理",
        )
        self.assertEqual(
            mod.format_native_name("Node Wrangler", "", mod.STYLE_ORIG_CN),
            "Node Wrangler",
        )

    def test_extension_manifest_map_patch_is_non_destructive(self):
        mod = _load_native()
        orig_item = FakeManifest("looptools", "LoopTools", "Mesh tools")
        untouched = FakeManifest("other", "Other Ext", "Desc")
        original_map = {
            "bl_ext.blender_org.looptools": orig_item,
            "bl_ext.blender_org.other": untouched,
        }
        patched = mod.patch_extension_manifest_map(
            original_map,
            by_module={"bl_ext.blender_org.looptools": "循环工具"},
            by_pkg_id={},
            style=mod.STYLE_ORIG_CN,
        )
        self.assertEqual(patched["bl_ext.blender_org.looptools"].name, "LoopTools (循环工具)")
        self.assertEqual(patched["bl_ext.blender_org.other"].name, "Other Ext")
        # Original dict and namedtuple must remain untouched
        self.assertEqual(original_map["bl_ext.blender_org.looptools"].name, "LoopTools")

    def test_local_manifest_dict_patch_for_get_extensions(self):
        mod = _load_native()
        orig_item = FakeManifest("bool_tool", "Bool Tool", "Boolean ops")
        local_map = {"bool_tool": orig_item}
        patched = mod.patch_local_manifest_dict(
            local_map,
            repo_prefix="bl_ext.blender_org.",
            by_module={},
            by_pkg_id={"bool_tool": "布尔工具"},
            style=mod.STYLE_ORIG_CN,
        )
        self.assertEqual(patched["bool_tool"].name, "Bool Tool (布尔工具)")
        self.assertEqual(local_map["bool_tool"].name, "Bool Tool")

    def test_legacy_bl_info_override_restores_cleanly(self):
        mod = _load_native()
        fake_mod = types.SimpleNamespace(__name__="node_wrangler",
                                         bl_info={"name": "Node Wrangler"})
        ext_mod = types.SimpleNamespace(__name__="bl_ext.blender_org.foo",
                                        bl_info={"name": "Foo"})
        restores = mod.apply_legacy_bl_info_overrides(
            [fake_mod, ext_mod],
            by_module={"node_wrangler": "节点管理", "bl_ext.blender_org.foo": "忽略"},
            module_bl_info_fn=lambda m: m.bl_info,
            is_extension_fn=lambda name: name.startswith("bl_ext."),
            style=mod.STYLE_ORIG_CN,
        )
        self.assertEqual(fake_mod.bl_info["name"], "Node Wrangler (节点管理)")
        self.assertEqual(ext_mod.bl_info["name"], "Foo")
        mod.restore_legacy_bl_info(restores)
        self.assertEqual(fake_mod.bl_info["name"], "Node Wrangler")

    def test_warm_from_db_populates_cache_and_memoizes_without_disk_io(self):
        mod = _load_native()
        mod.invalidate_cache()
        fake_db = types.SimpleNamespace(
            data={"library_id": "lib-1"},
            plugins={
                "ext:bool_tool": {
                    "kind": "extension",
                    "pkg_id": "bool_tool",
                    "module": "bl_ext.blender_org.bool_tool",
                    "display_name": "布尔工具",
                },
            },
            _state_store=lambda lid: types.SimpleNamespace(path=""),
        )
        mod.warm_from_db("/fake/net/lib", fake_db)
        by_m, by_p = mod._load_alias_maps("/fake/net/lib")
        self.assertEqual(by_m["bl_ext.blender_org.bool_tool"], "布尔工具")
        self.assertEqual(by_p["bool_tool"], "布尔工具")
        mod.invalidate_cache()


if __name__ == "__main__":
    unittest.main()
