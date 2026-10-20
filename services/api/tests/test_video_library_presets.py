from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image

from routers import generation, music, video_library
from routers.projects import ProjectCreate, create_project
from services import store


def preset_form(name="测试预设", preset_id="", **overrides):
    values = {
        "name": name,
        "preset_id": preset_id,
        "voice_type": "S_6Sd6jOE42",
        "speech_rate": 0,
        "voice_volume": 1.0,
        "background_template_id": "daodejing",
        "background_music_id": "",
        "background_music_start_sec": 0.0,
        "background_music_volume": 0.2,
        "cover_title_positions": "",
        "cover_mask_opacity": 0.35,
        "cover_source_preset_id": "",
        "file": None,
    }
    values.update(overrides)
    return values


def image_upload(color="red"):
    content = io.BytesIO()
    Image.new("RGB", (90, 160), color=color).save(content, format="PNG")
    content.seek(0)
    return UploadFile(file=content, filename="cover.png")


class PresetTests(unittest.TestCase):
    def setUp(self):
        self.previous = store.storage_dir()
        self.storage = tempfile.TemporaryDirectory()
        store.configure_storage(self.storage.name)
        store.ensure_storage()
        self.root = Path(self.storage.name)

    def tearDown(self):
        store.configure_storage(self.previous)
        self.storage.cleanup()

    def test_create_preset_stores_settings_image_and_clamps_values(self):
        result = video_library.upsert_preset(**preset_form(
            name="  沉稳男声  ", speech_rate=-99, voice_volume=3.5,
            cover_mask_opacity=0.9, file=image_upload(),
        ))
        preset = result["preset"]
        self.assertEqual(preset["name"], "沉稳男声")
        self.assertEqual(preset["speech_rate"], -50)
        self.assertEqual(preset["voice_volume"], 3.0)
        self.assertEqual(preset["cover_mask_opacity"], 0.9)
        self.assertTrue(preset["cover_available"])
        self.assertNotIn("title_line1", preset)
        self.assertNotIn("title_line2", preset)
        source = Path(store.load_db()["video_library_presets"][0]["cover_source_path"])
        self.assertTrue(source.is_file())
        self.assertTrue(source.resolve().is_relative_to((store.assets_dir() / "presets").resolve()))

    def test_zero_volume_and_zero_mask_are_preserved(self):
        preset = video_library.upsert_preset(**preset_form(
            voice_volume=0, cover_mask_opacity=0, background_music_volume=0,
        ))["preset"]
        self.assertEqual(preset["voice_volume"], 0.0)
        self.assertEqual(preset["cover_mask_opacity"], 0.0)
        self.assertEqual(preset["background_music_volume"], 0.0)

    def test_invalid_template_and_music_are_rejected(self):
        with self.assertRaises(HTTPException):
            video_library.upsert_preset(**preset_form(background_template_id="missing"))
        with self.assertRaises(HTTPException):
            video_library.upsert_preset(**preset_form(background_music_id="missing"))

    def test_overwrite_keeps_image_or_replaces_it(self):
        created = video_library.upsert_preset(**preset_form(file=image_upload("red")))["preset"]
        kept = video_library.upsert_preset(**preset_form(preset_id=created["id"], name="改名"))
        self.assertEqual(kept["preset"]["name"], "改名")
        self.assertEqual(kept["preset"]["cover_source_url"], created["cover_source_url"])

        first_source = Path(store.load_db()["video_library_presets"][0]["cover_source_path"])
        replaced = video_library.upsert_preset(**preset_form(
            preset_id=created["id"], file=image_upload("blue"),
        ))
        second_source = Path(store.load_db()["video_library_presets"][0]["cover_source_path"])
        self.assertTrue(second_source.is_file())
        self.assertFalse(first_source.exists())
        self.assertTrue(replaced["preset"]["cover_available"])

    def test_preset_cover_can_be_cloned_and_deleted(self):
        source = video_library.upsert_preset(**preset_form(name="源预设", file=image_upload("green")))["preset"]
        clone = video_library.upsert_preset(**preset_form(
            name="复制预设", cover_source_preset_id=source["id"],
        ))["preset"]
        source_path = Path(store.load_db()["video_library_presets"][0]["cover_source_path"])
        clone_row = next(
            item for item in store.load_db()["video_library_presets"] if item["id"] == clone["id"]
        )
        clone_path = Path(clone_row["cover_source_path"])
        self.assertNotEqual(source_path, clone_path)
        self.assertEqual(source_path.read_bytes(), clone_path.read_bytes())

        video_library.delete_preset(clone["id"])
        self.assertFalse(clone_path.exists())
        self.assertTrue(source_path.exists())
        with self.assertRaises(HTTPException):
            video_library.delete_preset("missing")

    def test_title_and_voice_previews_do_not_write_the_database(self):
        before = store.load_db()
        with patch.object(
            video_library, "generate_viral_title",
            return_value={"candidates": [{"line1": "第一行", "line2": "第二行"}]},
        ):
            result = video_library.title_preview(video_library.TitlePreviewPayload(script="测试文案"))
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(store.load_db(), before)

        with patch.object(
            video_library, "synthesize_volcengine_tts",
            return_value={"audio": b"fake-mp3"},
        ):
            response = video_library.voice_preview(
                video_library.VoicePreviewPayload(script="第一句。第二句。", voice_type="v", speech_rate=0),
            )
        self.assertIsInstance(response, StreamingResponse)
        self.assertEqual(response.media_type, "audio/mpeg")
        self.assertEqual(store.load_db(), before)

    def test_delete_music_clears_preset_reference(self):
        db = store.load_db()
        db.setdefault("music_library", []).append({
            "id": "music-1", "name": "音乐", "file_name": "music.mp3",
            "local_path": str(self.root / "outside.mp3"), "file_url": "/storage/x.mp3",
            "duration_sec": 10,
        })
        store.save_db(db)
        preset = video_library.upsert_preset(**preset_form(background_music_id="music-1"))["preset"]
        self.assertEqual(preset["background_music_id"], "music-1")
        music.delete_music("music-1")
        row = next(p for p in store.load_db()["video_library_presets"] if p["id"] == preset["id"])
        self.assertIsNone(row["background_music_id"])
        self.assertEqual(row["background_music_volume"], 0.2)

    def test_generate_cover_can_reuse_preset_image(self):
        preset = video_library.upsert_preset(**preset_form(file=image_upload("purple")))["preset"]
        project = create_project(
            ProjectCreate(raw_script="测试文案。【带货开始】", workflow_type="video_library"),
        )["project"]
        with store.db_write_transaction():
            db = store.load_db()
            row = next(p for p in db["projects"] if p["id"] == project["id"])
            row["title_line1"] = "标题第一行"
            row["title_line2"] = "标题第二行"
            store.save_db(db)

        generation.generate_cover(
            project["id"], file=None, title_positions="", mask_opacity=0.35,
            preset_id=preset["id"],
        )
        state = next(p for p in store.load_db()["projects"] if p["id"] == project["id"])
        source = store.project_dir(project["id"]) / "cover" / "portrait-upload"
        preset_row = next(
            p for p in store.load_db()["video_library_presets"] if p["id"] == preset["id"]
        )
        self.assertEqual(source.read_bytes(), Path(preset_row["cover_source_path"]).read_bytes())
        self.assertTrue((store.project_dir(project["id"]) / "cover" / "cover.png").is_file())
        self.assertTrue(state["cover_url"])

    def test_generate_cover_rejects_unknown_preset(self):
        project = create_project(
            ProjectCreate(raw_script="测试文案。【带货开始】", workflow_type="video_library"),
        )["project"]
        with store.db_write_transaction():
            db = store.load_db()
            row = next(p for p in db["projects"] if p["id"] == project["id"])
            row["title_line1"] = "标题第一行"
            row["title_line2"] = "标题第二行"
            store.save_db(db)
        with self.assertRaises(HTTPException):
            generation.generate_cover(
                project["id"], file=None, title_positions="", mask_opacity=0.35,
                preset_id="missing",
            )


if __name__ == "__main__":
    unittest.main()
