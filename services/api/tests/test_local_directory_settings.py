from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from main import read_storage_file  # noqa: E402
from routers.settings import DirectorySelectionPayload, select_directory  # noqa: E402
from services import settings_service, store  # noqa: E402
from services.video_export_service import jianying_drafts_root  # noqa: E402


class LocalDirectorySettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_storage = store.storage_dir()

    def tearDown(self) -> None:
        store.configure_storage(self.original_storage)
        store.ensure_storage()

    def test_project_directory_selection_switches_workspace_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as config_dir, tempfile.TemporaryDirectory() as workspace:
            config_path = Path(config_dir) / "settings.json"
            with patch.object(settings_service, "CONFIG_DIR", Path(config_dir)), patch.object(
                settings_service, "CONFIG_FILE", config_path,
            ), patch("routers.settings.CONFIG_FILE", config_path), patch(
                "routers.settings.choose_directory", return_value=workspace,
            ):
                result = select_directory(DirectorySelectionPayload(kind="project"))

            self.assertFalse(result["cancelled"])
            self.assertEqual(str(Path(workspace).resolve()), result["settings"]["project_directory"])
            self.assertEqual(Path(workspace).resolve(), store.storage_dir())
            self.assertTrue((Path(workspace) / "db.json").is_file())
            self.assertTrue(config_path.is_file())

    def test_storage_route_uses_current_workspace_and_hides_database(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            store.configure_storage(workspace)
            store.ensure_storage()
            image = Path(workspace) / "projects" / "demo" / "images" / "shot.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")

            response = read_storage_file("projects/demo/images/shot.png")
            self.assertEqual(image.resolve(), Path(response.path).resolve())
            with self.assertRaises(HTTPException):
                read_storage_file("db.json")

    def test_storage_move_relocates_library_and_template_paths(self) -> None:
        with tempfile.TemporaryDirectory() as old_root, tempfile.TemporaryDirectory() as workspace:
            stale_assets = Path(old_root) / "storage" / "assets"
            assets = Path(workspace) / "assets"
            assets.mkdir()
            for name in ("music_probe.mp3", "template_probe.png"):
                (assets / name).write_bytes(b"probe")
            (Path(workspace) / "db.json").write_text(json.dumps({
                "music_library": [{
                    "id": "music-1",
                    "local_path": str(stale_assets / "music_probe.mp3"),
                    "file_url": "/storage/assets/music_probe.mp3",
                }],
                "video_templates": [{
                    "id": "template-1",
                    "local_path": str(stale_assets / "template_probe.png"),
                    "file_url": "/storage/assets/template_probe.png",
                }],
            }), encoding="utf-8")

            store.configure_storage(workspace)
            store.ensure_storage()
            data = store.load_db()

            music = data["music_library"][0]
            template = data["video_templates"][0]
            self.assertEqual(str((assets / "music_probe.mp3").resolve()), music["local_path"])
            self.assertEqual(str((assets / "template_probe.png").resolve()), template["local_path"])
            self.assertEqual("/storage/assets/music_probe.mp3", music["file_url"])
            self.assertEqual("/storage/assets/template_probe.png", template["file_url"])

    def test_configured_jianying_directory_has_priority(self) -> None:
        with tempfile.TemporaryDirectory() as draft_dir:
            with patch(
                "services.video_export_service.configured_jianying_directory",
                return_value=Path(draft_dir),
            ), patch.dict("os.environ", {"JIANYING_DRAFTS_DIR": ""}):
                self.assertEqual(Path(draft_dir), jianying_drafts_root())


if __name__ == "__main__":
    unittest.main()
