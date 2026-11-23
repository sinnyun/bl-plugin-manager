"""Blender store integration with a loopback HTTP repository and real ZIP validation."""

import functools
import hashlib
import json
import os
import tempfile
import threading
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import bpy

from bl_plugin_manager import store, updates
from bl_plugin_manager.db import LibraryDB


def write_package(directory, version):
    """A complete synthetic extension with a predictable manifest identity."""
    path = directory / f"fixture-{version}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("blender_manifest.toml",
                         'schema_version = "1.0.0"\nid = "store_fixture"\n'
                         f'version = "{version}"\nname = "Store Fixture"\n'
                         'tagline = "Test the local repository"\nmaintainer = "Test"\n'
                         'type = "add-on"\nblender_version_min = "4.2.0"\n'
                         'license = ["SPDX:GPL-3.0-or-later"]\n')
        archive.writestr("__init__.py", "def register(): pass\ndef unregister(): pass\n")
    return {"id": "store_fixture", "name": "Store Fixture", "version": version,
            "type": "add-on", "archive_url": "./" + path.name,
            "archive_size": path.stat().st_size,
            "archive_hash": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def main():
    expected = Path(os.environ["BL_PLUGIN_MANAGER_EXPECTED_TEST_ROOT"]).resolve()
    for location in (bpy.utils.user_resource("CONFIG"), bpy.utils.user_resource("SCRIPTS"),
                     tempfile.gettempdir(), os.environ["BL_PLUGIN_MANAGER_LOCAL_STATE"],
                     os.environ["BL_PLUGIN_MANAGER_MACHINE_CONFIG"]):
        assert Path(location).resolve().is_relative_to(expected), f"unisolated path: {location}"

    with tempfile.TemporaryDirectory(prefix="pm-store-integration-") as temp:
        root = Path(temp)
        repo_folders = [root / "first", root / "second"]
        for folder in repo_folders:
            (folder / ".blender_ext").mkdir(parents=True)
        initial = write_package(repo_folders[0], "1.0.0")
        update = write_package(repo_folders[0], "1.1.0")
        rival = write_package(repo_folders[1], "2.0.0")

        with ThreadingHTTPServer(("127.0.0.1", 0),
                                 functools.partial(QuietHandler, directory=root)) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                repos = bpy.context.preferences.extensions.repos
                local_url = f"http://127.0.0.1:{server.server_port}/"
                for folder, item in zip(repo_folders, (initial, rival)):
                    repo = repos.new(name=folder.name, module="test_" + folder.name)
                    repo.use_remote_url = True
                    repo.remote_url = local_url + folder.name + "/index.json"
                    repo.use_custom_directory = True
                    repo.custom_directory = str(folder)
                    (folder / ".blender_ext" / "index.json").write_text(
                        json.dumps({"data": [item]}), encoding="utf-8")
                catalog = [pkg for pkg in store.read_catalog() if pkg["id"] == "store_fixture"]
                assert len(catalog) == 2
                package = next(pkg for pkg in catalog if pkg["repo"] == "first")
                assert package["archive_url"].startswith(local_url + "first/")
                library_root = str(root / "library")
                ok, error, record = store.install_package(package, library_root, LibraryDB(library_root))
                assert ok, error
                assert record["version"] == "1.0.0"
                assert record["archive_source"] == package["source"]
                assert store.search(catalog, LibraryDB(library_root), query="store_fixture")[0]["installed"]

                db = LibraryDB(library_root)
                db.get(record["key"])["note"] = "preserve my note"
                db.save()
                (repo_folders[0] / ".blender_ext" / "index.json").write_text(
                    json.dumps({"data": [update]}), encoding="utf-8")
                updates.check_updates(db, library_root)
                assert db.get(record["key"])["latest_version"] == "1.1.0"
                pkg = next(pkg for pkg in store.read_catalog() if pkg["source"] == package["source"])

                wrong_hash = dict(pkg, archive_hash="sha256:" + "0" * 64)
                ok, error = store.update_record(db.get(record["key"]), wrong_hash, library_root, db)
                assert not ok and "sha256" in error
                assert LibraryDB(library_root).get(record["key"])["version"] == "1.0.0"

                ok, error = store.update_record(db.get(record["key"]), pkg, library_root, db)
                assert ok, error
                installed = LibraryDB(library_root).get(record["key"])
                assert (installed["version"], installed["note"]) == ("1.1.0", "preserve my note")
            finally:
                server.shutdown()
                worker.join(2)
    print("===STORE_INTEGRATION_OK===")


if __name__ == "__main__":
    main()
