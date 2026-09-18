import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _storage_module(name):
    """Load storage modules without importing Blender-only addon __init__."""
    pkg = types.ModuleType("bl_plugin_manager")
    pkg.__path__ = ["bl_plugin_manager"]
    sys.modules.setdefault("bl_plugin_manager", pkg)
    return importlib.import_module(name)


def _catalog_module():
    return _storage_module("bl_plugin_manager.storage.catalog")


class CatalogV1Tests(unittest.TestCase):
    def test_new_catalog_has_schema_and_default_category(self):
        mod = _catalog_module()
        with tempfile.TemporaryDirectory() as root:
            db = mod.Catalog(Path(root))

            report = db.initialize()

            self.assertEqual(report.status, "CREATED")
            self.assertEqual(db.data["schema"], 1)
            self.assertTrue(db.data["library_id"])
            self.assertEqual(
                db.data["categories"],
                [{"id": "uncategorized", "name": "未分类", "order": 0}],
            )

    def test_catalog_is_keyed_by_plugin_id_not_path(self):
        mod = _catalog_module()
        with tempfile.TemporaryDirectory() as root:
            db = mod.Catalog(Path(root))
            db.initialize()

            db.set_plugin("addon:mesh-tools", {"kind": "addon", "name": "Mesh Tools",
                                               "display_name": "网格工具"})
            db.save()

            reopened = mod.Catalog(Path(root))
            self.assertEqual(reopened.load(), "OK")
            self.assertEqual(reopened.plugins["addon:mesh-tools"]["display_name"], "网格工具")

    def test_machine_specific_fields_are_rejected(self):
        mod = _catalog_module()
        with tempfile.TemporaryDirectory() as root:
            db = mod.Catalog(Path(root))
            db.initialize()
            for field in ("rel", "module", "enabled", "startup", "origin_path", "missing"):
                with self.assertRaises(mod.InvalidCatalogFieldError):
                    db.set_plugin("addon:demo", {"display_name": "D", field: "x"})

    def test_corrupt_catalog_is_read_only_and_not_replaced(self):
        mod = _catalog_module()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / ".pm" / "catalog.json"
            path.parent.mkdir(parents=True)
            path.write_text("{broken", encoding="utf-8")

            db = mod.Catalog(root)
            self.assertEqual(db.load(), "CORRUPT")
            with self.assertRaises(mod.CatalogConflictError):
                db.save()
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_corrupt_legacy_library_blocks_initialization(self):
        mod = _catalog_module()
        with tempfile.TemporaryDirectory() as root:
            pm = Path(root) / ".pm"
            pm.mkdir()
            (pm / "library.json").write_text("{broken", encoding="utf-8")

            db = mod.Catalog(root)
            report = db.initialize()

            self.assertEqual(report.status, "CORRUPT")
            self.assertFalse((pm / "catalog.json").exists())
            self.assertEqual((pm / "library.json").read_text(encoding="utf-8"), "{broken")

    def test_legacy_library_is_read_and_archived_read_only(self):
        mod = _catalog_module()
        with tempfile.TemporaryDirectory() as root:
            pm = Path(root) / ".pm"
            pm.mkdir()
            legacy = pm / "library.json"
            payload = {
                "schema": 2, "library_id": "lib-a", "revision": 3,
                "categories": [{"id": "uncategorized", "name": "未分类", "order": 0}],
                "plugins": {"addons/demo": {"name": "Demo", "display_name": "别名"}},
            }
            legacy.write_text(json.dumps(payload), encoding="utf-8")

            db = mod.Catalog(root)
            db.initialize()
            self.assertEqual(db.read_legacy()["library_id"], "lib-a")
            archive = db.archive_legacy()

            self.assertIsNotNone(archive)
            self.assertFalse(legacy.exists())
            self.assertEqual(json.loads(archive.read_text(encoding="utf-8")), payload)
            self.assertFalse(archive.stat().st_mode & 0o222)

    def test_external_update_is_absorbed_rather_than_overwritten(self):
        """外部/并发写入先被读回，本次记录合并其上，对方新增条目不丢。"""
        mod = _catalog_module()
        with tempfile.TemporaryDirectory() as root:
            first = mod.Catalog(root)
            first.initialize()
            first.set_plugin("addon:a", {"name": "A"})
            first.save()

            stale = mod.Catalog(root)
            stale.load()
            fresh = mod.Catalog(root)
            fresh.load()
            fresh.set_plugin("addon:a", {"name": "B"})
            fresh.set_plugin("addon:new", {"name": "New"})
            fresh.save()

            # 过期句柄保存时：先吸收对方写入，再覆盖自己持有的记录。
            self.assertTrue(stale.refresh_if_stale())
            stale.set_plugin("addon:a", {"name": "A2"})
            stale.save()

            reopened = mod.Catalog(root)
            reopened.load()
            self.assertEqual(reopened.plugins["addon:a"]["name"], "A2")
            self.assertIn("addon:new", reopened.plugins, "对方新增条目必须保留")

    def test_conflict_guard_still_refuses_to_overwrite_a_corrupt_file(self):
        mod = _catalog_module()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / ".pm" / "catalog.json"
            path.parent.mkdir(parents=True)
            path.write_text("{broken", encoding="utf-8")
            db = mod.Catalog(root)
            self.assertEqual(db.load(), "CORRUPT")
            with self.assertRaises(mod.CatalogConflictError):
                db.save()

    def test_foreign_machine_fields_are_dropped_not_fatal(self):
        """混入本机字段的同步文件仍可读取，字段被丢弃而不是判为损坏。"""
        mod = _catalog_module()
        with tempfile.TemporaryDirectory() as root:
            pm = Path(root) / ".pm"
            pm.mkdir()
            (pm / "catalog.json").write_text(json.dumps({
                "schema": 1, "library_id": "lib-a", "revision": 1,
                "categories": [{"id": "uncategorized", "name": "未分类", "order": 0}],
                "plugins": {"addon:a": {"name": "A", "display_name": "别名",
                                        "rel": "addons/A", "module": "A", "enabled": True,
                                        "startup": True}},
            }), encoding="utf-8")

            db = mod.Catalog(root)
            self.assertEqual(db.load(), "OK")
            record = db.plugins["addon:a"]
            self.assertEqual(record["display_name"], "别名")
            for leaked in ("rel", "module", "enabled", "startup"):
                self.assertNotIn(leaked, record)

            db.save()
            written = json.loads((pm / "catalog.json").read_text(encoding="utf-8"))
            self.assertNotIn("rel", written["plugins"]["addon:a"])


if __name__ == "__main__":
    unittest.main()
