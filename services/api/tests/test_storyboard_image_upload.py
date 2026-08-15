from __future__ import annotations

import copy
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from PIL import Image


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from routers.generation import upload_shot_image  # noqa: E402
from routers.projects import get_project  # noqa: E402
from routers.shots import select_generated_image  # noqa: E402


def image_upload(image_format: str = "PNG") -> UploadFile:
    content = io.BytesIO()
    Image.new("RGB", (24, 40), (30, 80, 120)).save(content, format=image_format)
    content.seek(0)
    return UploadFile(file=content, filename=f"我的分镜.{image_format.lower()}")


class StoryboardImageUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project_id = "project-1"
        self.shot_id = "shot-1"
        self.state = {
            "projects": [{"id": self.project_id}],
            "shots": [{
                "id": self.shot_id,
                "project_id": self.project_id,
                "shot_index": 1,
                "status": "prompt_ready",
            }],
            "generated_assets": [],
        }

    def test_upload_stores_and_selects_a_storyboard_image(self) -> None:
        def load_db():
            return copy.deepcopy(self.state)

        def save_db(value):
            self.state.clear()
            self.state.update(copy.deepcopy(value))

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "routers.generation.load_db", side_effect=load_db,
        ), patch("routers.generation.save_db", side_effect=save_db), patch(
            "routers.generation.project_dir", return_value=Path(temp_dir),
        ), patch(
            "routers.generation.public_url", side_effect=lambda path: f"/storage/{path.name}",
        ), patch(
            "routers.shots.load_db", side_effect=load_db,
        ), patch(
            "routers.shots.save_db", side_effect=save_db,
        ), patch(
            "routers.projects.load_db", side_effect=load_db,
        ), patch(
            "routers.projects.save_db", side_effect=save_db,
        ):
            result = upload_shot_image(self.project_id, self.shot_id, image_upload())

            stored_path = Path(result["asset"]["local_path"])
            self.assertTrue(stored_path.is_file())
            self.assertEqual((24, 40), (result["asset"]["width"], result["asset"]["height"]))
            self.assertEqual("uploaded", result["asset"]["asset_source"])
            self.assertEqual(result["asset"]["id"], self.state["shots"][0]["selected_asset_id"])
            self.assertEqual("uploaded", self.state["shots"][0]["asset_source"])

            selected = select_generated_image(
                self.project_id,
                self.shot_id,
                {"asset_id": result["asset"]["id"]},
            )
            self.assertEqual("uploaded", selected["shot"]["asset_source"])
            project = get_project(self.project_id)
            self.assertEqual([result["asset"]["id"]], [item["id"] for item in project["generated_assets"]])

    def test_upload_rejects_non_image_content_without_leaving_a_file(self) -> None:
        upload = UploadFile(file=io.BytesIO(b"not an image"), filename="fake.png")
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "routers.generation.load_db", return_value=copy.deepcopy(self.state),
        ), patch("routers.generation.project_dir", return_value=Path(temp_dir)):
            with self.assertRaises(HTTPException) as raised:
                upload_shot_image(self.project_id, self.shot_id, upload)
            self.assertEqual(400, raised.exception.status_code)
            self.assertEqual([], list(Path(temp_dir).rglob("*.*")))


if __name__ == "__main__":
    unittest.main()
