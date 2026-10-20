from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException, UploadFile
from PIL import Image

from routers import export, generation, media, video_library
from routers.projects import ProjectCreate, create_project
from services import store, video_export_service
from services.generation_service import (
    PRODUCT_MARKER,
    marker_start_sec,
    narration_shot,
    strip_product_marker,
)
from services.media_service import media_command
from services.product_placement_service import normalize_pip_box, resolve_product_overlays, normalize_item_timing


def make_video(path: Path, seconds: float = 1.0, size: str = "640x480") -> Path:
    media_command([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=red:s={size}:r=30",
        "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ])
    return path


def make_audio(path: Path, seconds: float = 4.0) -> Path:
    media_command([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", "sine=frequency=440:sample_rate=44100", "-t", str(seconds), str(path),
    ])
    return path


def image_upload(color=(20, 120, 200), size=(90, 160)) -> UploadFile:
    content = io.BytesIO()
    Image.new("RGB", size, color=color).save(content, format="PNG")
    content.seek(0)
    return UploadFile(file=content, filename="带货图片.png")


def video_upload(path: Path, name: str = "带货视频.mp4") -> UploadFile:
    return UploadFile(file=io.BytesIO(path.read_bytes()), filename=name)


class PlacementUnitTests(unittest.TestCase):
    def test_original_video_is_not_limited_to_thirty_seconds(self):
        timing = normalize_item_timing(
            {"media_type": "video", "duration_sec": 95.25},
            {"duration_mode": "original", "duration_sec": 3},
        )
        self.assertEqual(timing["duration_sec"], 95.25)
        self.assertEqual(timing["duration_mode"], "original")

    def test_fill_image_stops_sequence_and_covers_last_fraction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "image.png"
            Image.new("RGB", (10, 10)).save(path)
            db = {"product_library": [{"id": "image", "local_path": str(path), "media_type": "image"}]}
            project = {
                "raw_script": "【带货开始】后",
                "audio_duration_sec": 3.2,
                "product_placement": {"items": [
                    {"asset_id": "image", "duration_sec": 3},
                    {"asset_id": "image", "duration_mode": "remaining"},
                    {"asset_id": "image", "duration_sec": 3},
                ]},
            }
            overlays, report = resolve_product_overlays(db, project, 3.2)
            self.assertEqual([item["duration_sec"] for item in overlays], [3, 0.2])
            self.assertEqual(report["skipped_after_fill"], 1)
            self.assertEqual(report["end_sec"], 3.2)
            self.assertFalse(report["truncated"])

    def test_original_video_truncates_at_voice_end(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "video.mp4"
            path.touch()
            db = {"product_library": [{"id": "video", "local_path": str(path),
                                       "media_type": "video", "duration_sec": 60}]}
            project = {"raw_script": "【带货开始】后", "audio_duration_sec": 10,
                       "product_placement": {"items": [{"asset_id": "video", "duration_mode": "original"}]}}
            overlays, report = resolve_product_overlays(db, project, 10)
            self.assertEqual(overlays[0]["requested_duration_sec"], 60)
            self.assertEqual(overlays[0]["duration_sec"], 10)
            self.assertTrue(report["truncated"])

    def test_marker_strip_and_time_mapping(self):
        clean, present, offset = strip_product_marker("前半段【带货开始】后半段")
        self.assertEqual(clean, "前半段后半段")
        self.assertTrue(present)
        self.assertEqual(offset, 3)
        self.assertEqual(strip_product_marker("没有标记")[1:], (False, -1))

        timings = [
            {"text": "前半段", "start_time": 0, "end_time": 2},
            {"text": "后半段", "start_time": 2, "end_time": 4},
        ]
        self.assertAlmostEqual(marker_start_sec("前半段后半段", 3, 4, timings), 2.0)
        weighted = marker_start_sec("前半段后半段", 3, 10, [])
        self.assertGreater(weighted, 0)
        self.assertLess(weighted, 10)

    def test_narration_shot_strips_marker(self):
        shot = narration_shot({"raw_script": "前【带货开始】后", "audio_duration_sec": 3})
        self.assertEqual(shot["voice_text"], "前后")
        self.assertNotIn(PRODUCT_MARKER, shot["voice_text"])

    def test_pip_box_is_clamped(self):
        box = normalize_pip_box({"x": -1, "y": 0.95, "w": 3, "h": 0.01})
        self.assertGreaterEqual(box["x"], 0)
        self.assertLessEqual(box["y"] + box["h"], 1.0)
        self.assertLessEqual(box["x"] + box["w"], 1.0)
        self.assertGreaterEqual(box["w"], 0.12)
        self.assertGreaterEqual(box["h"], 0.12)


class ProductLibraryTests(unittest.TestCase):
    def setUp(self):
        self.previous = store.storage_dir()
        self.storage = tempfile.TemporaryDirectory()
        store.configure_storage(self.storage.name)
        store.ensure_storage()
        self.root = Path(self.storage.name)
        self.video = make_video(self.root / "normal.mp4")

    def tearDown(self):
        store.configure_storage(self.previous)
        self.storage.cleanup()

    def upload_normal(self):
        return media.upload_video(UploadFile(file=io.BytesIO(self.video.read_bytes()), filename="普通.mp4"))

    def test_upload_video_and_image_stay_in_product_library(self):
        self.upload_normal()
        product_video = media.upload_product(video_upload(self.video))
        product_image = media.upload_product(image_upload())
        listing = media.list_media()
        self.assertIn(product_video["id"], {item["id"] for item in listing["products"]})
        self.assertIn(product_image["id"], {item["id"] for item in listing["products"]})
        self.assertNotIn(product_video["id"], {item["id"] for item in listing["videos"]})
        self.assertEqual(product_video["media_type"], "video")
        self.assertEqual(product_image["media_type"], "image")
        self.assertTrue(Path(product_image["local_path"]).suffix == ".png")
        self.assertTrue(Path(product_image["local_path"]).is_file())

    def test_delete_product_cleans_file_and_rejects_referenced_asset(self):
        product = media.upload_product(image_upload())
        media.delete_product(product["id"])
        self.assertFalse(Path(product["local_path"]).exists())

        kept = media.upload_product(image_upload())
        project = create_project(ProjectCreate(raw_script="测试。", workflow_type="video_library"))["project"]
        with store.db_write_transaction():
            db = store.load_db()
            row = next(p for p in db["projects"] if p["id"] == project["id"])
            row["product_placement"] = {"items": [{"asset_id": kept["id"], "duration_sec": 3}]}
            store.save_db(db)
        with self.assertRaises(HTTPException):
            media.delete_product(kept["id"])
        self.assertTrue(Path(kept["local_path"]).exists())

    def test_legacy_video_fill_does_not_slow_down_source(self):
        product = media.upload_product(video_upload(self.video))
        project = create_project(ProjectCreate(
            raw_script=f"前{PRODUCT_MARKER}后", workflow_type="video_library",
        ))["project"]
        project.update({
            "audio_duration_sec": 10.0,
            "narration_script": "前后",
            "narration_subtitle_timings": [{"text": "前后", "start_time": 0, "end_time": 10}],
            "product_placement": {
                "items": [{
                    "asset_id": product["id"], "duration_sec": 3.0,
                    "fill_duration": False, "pip_box": {"x": 0.6, "y": 0.3, "w": 0.3, "h": 0.2},
                }],
            },
        })
        db = {"product_library": [product]}
        overlays, _ = resolve_product_overlays(db, project, 10.0)
        self.assertAlmostEqual(overlays[0]["duration_sec"], 1.0, places=2)
        self.assertFalse(overlays[0]["fill_duration"])

        project["product_placement"]["items"][0]["fill_duration"] = True
        overlays, _ = resolve_product_overlays(db, project, 10.0)
        self.assertEqual(overlays[0]["duration_sec"], 1.0)
        self.assertFalse(overlays[0]["fill_duration"])

    def test_placement_presets_round_trip_and_block_product_delete(self):
        product = media.upload_product(image_upload())
        created = video_library.upsert_placement_preset(video_library.PlacementPresetPayload(
            name="带货预设",
            items=[video_library.PlacementPresetItem(
                asset_id=product["id"], duration_sec=4,
                pip_box={"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4},
            )],
            transition_sec=1.0,
            pip_box={"x": 0.6, "y": 0.3, "w": 0.3, "h": 0.2},
        ))["preset"]
        self.assertEqual(created["name"], "带货预设")
        self.assertEqual(created["items"][0]["duration_sec"], 4.0)
        self.assertEqual(created["items"][0]["pip_box"]["x"], 0.1)
        self.assertEqual(created["transition_sec"], 1.0)
        self.assertTrue(video_library.list_placement_presets()["presets"])
        with self.assertRaises(HTTPException):
            media.delete_product(product["id"])

        updated = video_library.upsert_placement_preset(video_library.PlacementPresetPayload(
            name="改名预设",
            preset_id=created["id"],
            items=[video_library.PlacementPresetItem(asset_id=product["id"], duration_sec=6)],
            transition_sec=2.5,
            pip_box={"x": 0.6, "y": 0.3, "w": 0.3, "h": 0.2},
        ))["preset"]
        self.assertEqual(updated["name"], "改名预设")
        self.assertEqual(updated["transition_sec"], 2.0)
        self.assertEqual(updated["items"][0]["pip_box"]["x"], 0.6)

        video_library.delete_placement_preset(created["id"])
        media.delete_product(product["id"])

    def test_duration_modes_survive_project_and_preset_saves(self):
        image = media.upload_product(image_upload())
        video = media.upload_product(video_upload(self.video))
        project = create_project(ProjectCreate(
            raw_script=f"{PRODUCT_MARKER}测试", workflow_type="video_library",
        ))["project"]
        item_data = [
            {"asset_id": video["id"], "duration_mode": "original", "duration_sec": 0.5},
            {"asset_id": image["id"], "duration_mode": "remaining", "duration_sec": 3},
        ]
        result = media.update_product_placement(project["id"], media.ProductPlacementPayload(
            items=[media.ProductPlacementItem(**item) for item in item_data],
        ))["product_placement"]
        self.assertEqual([item["duration_mode"] for item in result["items"]], ["original", "remaining"])
        self.assertAlmostEqual(result["items"][0]["duration_sec"], 1.0)
        preset = video_library.upsert_placement_preset(video_library.PlacementPresetPayload(
            name="timing modes",
            items=[video_library.PlacementPresetItem(**item) for item in item_data],
        ))["preset"]
        self.assertEqual([item["duration_mode"] for item in preset["items"]], ["original", "remaining"])

    def test_voice_generation_strips_marker_and_resolves_start(self):
        self.upload_normal()
        project = create_project(ProjectCreate(
            raw_script=f"前半段。{PRODUCT_MARKER}后半段。", workflow_type="video_library",
        ))["project"]
        captured = {}

        def synthesize(path, shots, voice_type, speech_rate, max_hanzi=6):
            captured["text"] = shots[0]["voice_text"]
            make_audio(path, 4.0)
            return {
                "duration_sec": 4.0,
                "subtitle_timings": [
                    {"text": "前半段", "start_time": 0.0, "end_time": 2.0},
                    {"text": "后半段", "start_time": 2.0, "end_time": 4.0},
                ],
                "audio_format": "mp3",
                "voice_type": voice_type or "test",
                "provider": "volcengine_tts",
            }

        with patch.object(generation, "synthesize_project_voice", side_effect=synthesize):
            generation.generate_voice(project["id"])
        self.assertNotIn(PRODUCT_MARKER, captured["text"])
        row = next(p for p in store.load_db()["projects"] if p["id"] == project["id"])
        placement = row["product_placement"]
        self.assertTrue(placement["marker_present"])
        self.assertAlmostEqual(placement["start_sec"], 2.0, places=2)
        self.assertFalse(placement["start_sec_stale"])
        self.assertNotIn(PRODUCT_MARKER, row["narration_script"])
        srt = Path(store.storage_dir() / row["subtitle_url"].removeprefix("/storage/")).read_text(encoding="utf-8")
        self.assertNotIn(PRODUCT_MARKER, srt)
        self.assertNotIn("带货开始", srt)

    def test_placement_validation_and_start_time(self):
        self.upload_normal()
        product = media.upload_product(image_upload())
        project = create_project(ProjectCreate(
            raw_script=f"前{PRODUCT_MARKER}后", workflow_type="video_library",
        ))["project"]
        with store.db_write_transaction():
            db = store.load_db()
            row = next(p for p in db["projects"] if p["id"] == project["id"])
            row["audio_duration_sec"] = 10.0
            row["narration_script"] = "前后"
            row["narration_subtitle_timings"] = [{"text": "前后", "start_time": 0, "end_time": 10}]
            store.save_db(db)

        result = media.update_product_placement(project["id"], media.ProductPlacementPayload(
            items=[media.ProductPlacementItem(asset_id=product["id"], duration_sec=99)],
            transition_sec=5,
            pip_box=media.ProductPipBox(x=-1, y=0.95, w=3, h=0.01),
        ))
        placement = result["product_placement"]
        self.assertEqual(placement["items"][0]["duration_sec"], 30.0)
        self.assertEqual(placement["transition_sec"], 2.0)
        self.assertAlmostEqual(placement["start_sec"], 5.0, places=2)
        self.assertEqual(placement["pip_box"]["x"], 0.0)
        self.assertGreaterEqual(placement["pip_box"]["h"], 0.12)
        self.assertTrue(placement["enabled"])

        with self.assertRaises(HTTPException):
            media.update_product_placement(project["id"], media.ProductPlacementPayload(
                items=[media.ProductPlacementItem(asset_id="missing", duration_sec=3)],
            ))

    def test_export_passes_product_placement_into_draft(self):
        self.upload_normal()
        product = media.upload_product(image_upload())
        project = create_project(ProjectCreate(
            raw_script=f"前半段。{PRODUCT_MARKER}后半段。", workflow_type="video_library",
        ))["project"]

        def synthesize(path, shots, voice_type, speech_rate, max_hanzi=6):
            make_audio(path, 4.0)
            return {
                "duration_sec": 4.0,
                "subtitle_timings": [
                    {"text": "前半段", "start_time": 0.0, "end_time": 2.0},
                    {"text": "后半段", "start_time": 2.0, "end_time": 4.0},
                ],
                "audio_format": "mp3",
                "voice_type": voice_type or "test",
                "provider": "volcengine_tts",
            }

        with patch.object(generation, "synthesize_project_voice", side_effect=synthesize):
            generation.generate_voice(project["id"])
        media.update_product_placement(project["id"], media.ProductPlacementPayload(
            items=[media.ProductPlacementItem(asset_id=product["id"], duration_sec=1.5)],
            transition_sec=0.5,
            pip_box=media.ProductPipBox(x=0.5, y=0.2, w=0.4, h=0.4),
        ))
        captured = {}

        def fake_draft(*args, **kwargs):
            captured.update(kwargs)
            return {"status": "ready", "draft_name": "test", "draft_path": str(self.root / "draft")}

        with patch.object(export, "create_jianying_native_draft", side_effect=fake_draft):
            report = export.export_package(project["id"])

        placement = captured["product_placement"]
        self.assertTrue(placement)
        self.assertEqual(len(placement["items"]), 1)
        self.assertEqual(placement["items"][0]["media_type"], "image")
        self.assertAlmostEqual(placement["start_sec"], 2.0, places=2)
        self.assertEqual(placement["pip_box"]["x"], 0.5)
        self.assertTrue(report["verification"]["product_placement"]["enabled"])
        exported_srt = (store.project_dir(project["id"]) / "exports" / "package" / "subtitles.srt").read_text(
            encoding="utf-8",
        )
        self.assertNotIn(PRODUCT_MARKER, exported_srt)


@unittest.skipUnless(
    __import__("importlib").util.find_spec("pyJianYingDraft") is not None,
    "pyJianYingDraft unavailable",
)
class ProductDraftTests(unittest.TestCase):
    def test_products_track_plays_original_speed_and_fades(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "drafts").mkdir()
            clip = make_video(root / "clip.mp4", 1.0)
            product = make_video(root / "product.mp4", 1.0)
            image_path = root / "product.png"
            Image.new("RGB", (90, 160), color=(20, 120, 200)).save(image_path)
            audio = make_audio(root / "voice.mp3", 6.0)
            srt = root / "subtitles.srt"
            srt.write_text("1\n00:00:00,000 --> 00:00:06,000\n测试字幕\n\n", encoding="utf-8")
            shots = [{"shot_index": 1, "start_time": 0, "end_time": 6.0, "duration_sec": 6.0}]
            template = Path(__file__).resolve().parents[3] / "assets" / "template-daodejing.png"
            with patch.object(video_export_service, "jianying_drafts_root", return_value=root / "drafts"):
                report = video_export_service.create_jianying_native_draft(
                    root / "package", "pip_test", shots, [clip], audio, srt,
                    background_template_path=template,
                    scene_media="video", copy_scenes=False, animate_scenes=False,
                    clip_window=(0, 589, 1080, 742),
                    subtitle_transform_y=video_export_service._transform_y_for_visual_y(1180),
                    product_placement={
                        "items": [
                            {"path": str(product), "duration_sec": 3.0},
                            {
                                "path": str(image_path), "duration_sec": 2.0,
                                "pip_box": {"x": 0.1, "y": 0.5, "w": 0.2, "h": 0.25},
                            },
                        ],
                        "start_sec": 1.0,
                        "transition_sec": 0.5,
                        "fade_sec": 0.3,
                        "pip_box": {"x": 0.5, "y": 0.25, "w": 0.4, "h": 0.5},
                    },
                )
            content = json.loads(
                (Path(report["draft_path"]) / "draft_content.json").read_text(encoding="utf-8"),
            )
            tracks = {track.get("name"): track for track in content["tracks"]}
            self.assertIn("products", tracks)
            product_segments = tracks["products"]["segments"]
            clip_segment = tracks["clips"]["segments"][0]
            self.assertGreater(product_segments[0]["render_index"], clip_segment["render_index"])
            self.assertEqual(len(product_segments), 2)  # original-speed video + image, no looping
            self.assertEqual(
                [segment["target_timerange"]["start"] for segment in product_segments],
                [1_000_000, 2_000_000],
            )
            self.assertEqual(product_segments[0]["target_timerange"]["duration"], 1_000_000)
            self.assertEqual(product_segments[0]["source_timerange"]["duration"], 1_000_000)
            self.assertAlmostEqual(product_segments[0]["speed"], 1.0, places=3)
            self.assertTrue(all(segment["volume"] == 0 for segment in product_segments))
            first = product_segments[0]
            self.assertAlmostEqual(first["clip"]["scale"]["x"], 0.4, places=3)
            self.assertAlmostEqual(first["clip"]["transform"]["x"], 0.4, places=3)
            self.assertAlmostEqual(first["clip"]["transform"]["y"], 0.0, places=3)
            self.assertIn("KFTypeAlpha", json.dumps(first, ensure_ascii=False))
            self.assertIn("KFTypeAlpha", json.dumps(product_segments[-1], ensure_ascii=False))
            self.assertTrue(content["materials"].get("transitions"))
            # Per-item boxes must not be overwritten by the shared default.
            image_segment = product_segments[-1]
            self.assertAlmostEqual(image_segment["clip"]["scale"]["x"], 0.2, places=3)
            self.assertAlmostEqual(image_segment["clip"]["transform"]["x"], -0.6, places=3)
            self.assertAlmostEqual(image_segment["clip"]["transform"]["y"], -0.25, places=3)


if __name__ == "__main__":
    unittest.main()
