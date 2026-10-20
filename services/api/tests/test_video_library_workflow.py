from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import UploadFile

from routers import export, generation, media
from routers.projects import BulkDeletePayload, ProjectCreate, bulk_delete_projects, create_project
from services import store
from services.generation_service import chinese_char_count
from services.media_service import media_command
from services import video_export_service


def make_video(path: Path, seconds: float = 0.7, size: str = "320x180") -> Path:
    media_command([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=red:s={size}:r=30",
        "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ])
    return path


def make_audio(path: Path, seconds: float = 1.13) -> Path:
    media_command([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", "sine=frequency=440:sample_rate=44100", "-t", str(seconds), str(path),
    ])
    return path


class VideoLibraryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.previous = store.storage_dir()
        self.storage = tempfile.TemporaryDirectory()
        store.configure_storage(self.storage.name)
        store.ensure_storage()
        self.root = Path(self.storage.name)
        self.video = make_video(self.root / "source.mp4")

    def tearDown(self):
        store.configure_storage(self.previous)
        self.storage.cleanup()

    def upload_video(self):
        return media.upload_video(
            UploadFile(file=io.BytesIO(self.video.read_bytes()), filename="素材.mp4")
        )

    def create_video_project(self):
        return create_project(
            ProjectCreate(raw_script="真正厉害的人往往懂得沉默。", workflow_type="video_library")
        )["project"]

    def test_voice_generation_narrates_script_and_matches_clips(self):
        self.upload_video()
        project = self.create_video_project()

        calls = []

        def synthesize(path, shots, voice_type, speech_rate, max_hanzi=6):
            self.assertEqual(max_hanzi, 9)
            calls.append((voice_type, speech_rate))
            make_audio(path, 1.13)
            return {
                "duration_sec": 1.13,
                "subtitle_timings": [
                    {"text": "真正厉害的人往往懂得沉默", "start_time": 0, "end_time": 1.13},
                ],
                "audio_format": "mp3",
                "voice_type": voice_type or "S_6Sd6jOE42",
                "provider": "volcengine_tts",
                "alignment_method": "volcengine_word_timestamps",
                "timestamp_provider": "volcengine_tts_subtitle",
            }

        with patch.object(generation, "synthesize_project_voice", side_effect=synthesize):
            result = generation.generate_voice(
                project["id"],
                generation.VoicePayload(audio_source="tts", voice_type="voice-a", speech_rate=25),
            )

        project_row = next(p for p in store.load_db()["projects"] if p["id"] == project["id"])
        self.assertEqual(project_row["status"], "voice_ready")
        self.assertEqual(project_row["voice_type"], "voice-a")
        self.assertEqual(project_row["speech_rate"], 25)
        self.assertFalse(project_row.get("render_url"))
        self.assertTrue(Path(project_row["audio_path"]).is_file())
        self.assertEqual(Path(project_row["audio_path"]).name, "main_voice.mp3")
        self.assertEqual(result["provider"], "volcengine_tts")
        shots = [s for s in store.load_db()["shots"] if s["project_id"] == project["id"]]
        self.assertTrue(shots)
        self.assertEqual(shots[-1]["end_time"], 1.13)

        calls.clear()
        with patch.object(generation, "synthesize_project_voice", side_effect=synthesize):
            generation.generate_voice(project["id"])
        self.assertEqual(calls[-1], ("voice-a", 25))

        srt = Path(self.root / project_row["subtitle_url"].removeprefix("/storage/"))
        self.assertTrue(srt.is_file())
        lines = [
            line for line in srt.read_text(encoding="utf-8").splitlines()
            if line and not line.isdigit() and "-->" not in line
        ]
        self.assertTrue(lines)
        self.assertTrue(all(chinese_char_count(line) <= 9 for line in lines))

    def test_uploaded_audio_skips_tts_and_is_normalized(self):
        self.upload_video()
        project = self.create_video_project()
        wav = make_audio(self.root / "voice.wav", 1.4)
        media.upload_project_audio(
            project["id"], UploadFile(file=io.BytesIO(wav.read_bytes()), filename="录音.wav"),
        )
        with patch.object(
            generation, "synthesize_project_voice",
            side_effect=AssertionError("TTS must be skipped for uploaded audio"),
        ):
            generation.generate_voice(
                project["id"], generation.VoicePayload(audio_source="upload"),
            )
        row = next(p for p in store.load_db()["projects"] if p["id"] == project["id"])
        self.assertEqual(row["status"], "voice_ready")
        self.assertEqual(row["audio_source"], "upload")
        self.assertAlmostEqual(row["audio_duration_sec"], 1.4, places=2)
        self.assertEqual(Path(row["audio_path"]).suffix, ".mp3")
        self.assertTrue(Path(row["audio_path"]).is_file())

    def test_bulk_delete_removes_selected_projects_and_folders(self):
        ids = [
            create_project(ProjectCreate(raw_script=f"批量删除测试{i}。", workflow_type="video_library"))["project"]["id"]
            for i in range(3)
        ]
        with store.db_write_transaction():
            db = store.load_db()
            busy = next(p for p in db["projects"] if p["id"] == ids[2])
            busy["status"] = "video_rendering"
            store.save_db(db)

        result = bulk_delete_projects(BulkDeletePayload(project_ids=[*ids, ids[0], "missing"]))
        self.assertEqual(sorted(result["deleted_ids"]), sorted(ids[:2]))
        self.assertEqual(len(result["skipped"]), 2)
        db = store.load_db()
        self.assertFalse(any(p["id"] in ids[:2] for p in db["projects"]))
        self.assertTrue(any(p["id"] == ids[2] for p in db["projects"]))
        for project_id in ids[:2]:
            self.assertFalse((store.projects_dir() / project_id).exists())
        self.assertTrue((store.projects_dir() / ids[2]).exists())

    def test_export_uses_video_clips_template_and_keeps_narration_srt(self):
        self.upload_video()
        project = self.create_video_project()

        def synthesize(path, shots, voice_type, speech_rate, max_hanzi=6):
            self.assertEqual(max_hanzi, 9)
            make_audio(path, 1.13)
            return {
                "duration_sec": 1.13,
                "subtitle_timings": [
                    {"text": "真正厉害的人往往懂得沉默", "start_time": 0, "end_time": 1.13},
                ],
                "audio_format": "mp3",
                "voice_type": voice_type or "S_6Sd6jOE42",
                "provider": "volcengine_tts",
            }

        with patch.object(generation, "synthesize_project_voice", side_effect=synthesize):
            generation.generate_voice(project["id"])

        with store.db_write_transaction():
            db = store.load_db()
            row = next(p for p in db["projects"] if p["id"] == project["id"])
            row.update({"title_line1": "标题第一行", "title_line2": "标题第二行"})
            store.save_db(db)
        srt_before = (store.project_dir(project["id"]) / "subtitles" / "subtitles.srt").read_text(
            encoding="utf-8",
        )
        captured = {}

        def fake_draft(package_destination, project_name, shots, scene_paths, audio_path,
                       subtitles_path, cover_path=None, **kwargs):
            captured.update({
                "scene_paths": [Path(path) for path in scene_paths],
                "audio_path": Path(audio_path),
                "subtitles_path": Path(subtitles_path),
                "kwargs": kwargs,
                "shots": shots,
            })
            return {
                "status": "ready", "native_draft": True, "draft_name": "VideoGen_test",
                "draft_path": str(self.root / "draft"), "file_count": 1,
            }

        with patch.object(export, "create_jianying_native_draft", side_effect=fake_draft):
            report = export.export_package(project["id"])

        self.assertEqual(captured["kwargs"]["scene_media"], "video")
        self.assertFalse(captured["kwargs"]["copy_scenes"])
        self.assertFalse(captured["kwargs"]["animate_scenes"])
        self.assertEqual(captured["kwargs"]["clip_window"], (0, 589, 1080, 742))
        self.assertAlmostEqual(captured["kwargs"]["subtitle_transform_y"], -0.2292, places=3)
        self.assertTrue(captured["kwargs"]["background_template_path"].is_file())
        self.assertTrue(all(path.suffix.lower() == ".mp4" for path in captured["scene_paths"]))
        exported_srt = (store.project_dir(project["id"]) / "exports" / "package" / "subtitles.srt")
        self.assertEqual(exported_srt.read_text(encoding="utf-8"), srt_before)
        self.assertIn("external_media_references", report["verification"])
        self.assertFalse(report["verification"]["portable_zip"])
        row = next(p for p in store.load_db()["projects"] if p["id"] == project["id"])
        self.assertEqual(row["status"], "draft_ready")


def _can_create_jianying_draft() -> bool:
    try:
        import pymediainfo
        import pyJianYingDraft  # noqa: F401
        return pymediainfo.MediaInfo.can_parse()
    except Exception:
        return False


@unittest.skipUnless(_can_create_jianying_draft(), "pyJianYingDraft/pymediainfo unavailable")
class JianyingVideoDraftTests(unittest.TestCase):
    def test_both_export_modes_keep_editable_cover_on_first_export_and_reexport(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = make_audio(root / "voice.mp3", 1.13)
            video = make_video(root / "clip.mp4", 1.2, "640x480")
            picture = root / "scene.png"
            cover = root / "cover.png"
            Image.new("RGB", (108, 192), (20, 50, 90)).save(picture)
            Image.new("RGB", (108, 192), (190, 40, 60)).save(cover)
            srt = root / "subtitles.srt"
            srt.write_text("1\n00:00:00,000 --> 00:00:01,130\n封面测试\n\n", encoding="utf-8")
            shots = [{"shot_index": 1, "start_time": 0, "end_time": 1.13, "duration_sec": 1.13}]
            (root / "drafts").mkdir()
            with patch.object(video_export_service, "jianying_drafts_root", return_value=root / "drafts"):
                for mode, scene in [("image", picture), ("video", video)]:
                    previous_id = None
                    for attempt in range(2):
                        with self.subTest(mode=mode, attempt=attempt):
                            report = video_export_service.create_jianying_native_draft(
                                root / "package", f"cover_{mode}", shots, [scene], audio, srt,
                                cover_path=cover, scene_media=mode, animate_scenes=False,
                            )
                            live = Path(report["draft_path"])
                            self.assertNotEqual(previous_id, report["cover"]["cover_draft_id"])
                            previous_id = report["cover"]["cover_draft_id"]
                            for destination in [live, root / "package"]:
                                content = json.loads((destination / "draft_content.json").read_text(encoding="utf-8"))
                                composition = next(m["draft"] for m in content["materials"]["drafts"]
                                                   if m["id"] == content["cover"]["cover_draft_id"])
                                self.assertEqual(composition["new_version"], "189.0.0")
                                segment = composition["tracks"][0]["segments"][0]
                                time = segment["target_timerange"]
                                self.assertEqual(composition["duration"], time["start"] + time["duration"])
                                self.assertGreater(composition["duration"], content["duration"])
                                self.assertEqual(segment["track_render_index"], 101)
                                material = composition["materials"]["videos"][0]
                                resource = destination / "Resources/cover" / material["path"].rsplit("/", 1)[1]
                                self.assertTrue(resource.is_file())
                                self.assertTrue((destination / "draft_local_cover.jpg").is_file())
                                with Image.open(resource) as image:
                                    self.assertEqual(image.size, (108, 192))
                                    self.assertGreater(image.getpixel((50, 100))[0], 180)

    def test_uploaded_cover_is_an_independent_composition(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "uploaded.png"
            Image.new("RGB", (108, 192), (30, 120, 60)).save(source)
            content = {
                "duration": 10_000_000,
                "tracks": [{"name": "main", "segments": []}],
                "materials": {"drafts": [{"id": "unrelated", "type": "composition"}]},
            }
            content_path = root / "draft_content.json"
            content_path.write_text(json.dumps(content), encoding="utf-8")
            meta_path = root / "draft_meta_info.json"
            meta_path.write_text('{"draft_id":"keep","draft_cover":""}', encoding="utf-8")
            for _ in range(2):
                report = video_export_service._install_jianying_cover(root, source)
                actual = json.loads(content_path.read_text(encoding="utf-8"))
                self.assertEqual(actual["duration"], content["duration"])
                self.assertEqual(actual["tracks"], content["tracks"])
                self.assertEqual(len(actual["materials"]["drafts"]), 2)
                cover = next(m for m in actual["materials"]["drafts"]
                             if m["id"] == actual["cover"]["cover_draft_id"])
                self.assertEqual(actual["cover"]["sub_type"], "local")
                composition = cover["draft"]
                self.assertEqual(composition["new_version"], "189.0.0")
                segment = composition["tracks"][0]["segments"][0]
                material = next(m for m in composition["materials"]["videos"]
                                if m["id"] == segment["material_id"])
                self.assertTrue(material["path"].startswith("##_draftpath_placeholder_"))
                self.assertEqual(material["path"].split("/Resources/cover/")[1],
                                 Path(report["cover_path"]).name)
                self.assertTrue(Path(report["cover_path"]).is_file())
                self.assertEqual(cover["id"], composition["id"] + "_material")
                timerange = segment["target_timerange"]
                self.assertGreater(timerange["start"], content["duration"])
                self.assertEqual(composition["duration"],
                                 timerange["start"] + timerange["duration"])
                self.assertEqual(segment["render_index"], 101)
                self.assertEqual(segment["track_render_index"], 101)
                self.assertEqual((material["width"], material["height"]), (108, 192))
                self.assertTrue(Path(report["local_cover_path"]).is_file())
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                self.assertEqual(metadata["draft_id"], "keep")
                self.assertEqual(metadata["draft_cover"], report["draft_cover_path"])

    def test_background_template_and_clips_are_layered_into_the_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clip = make_video(root / "clip.mp4", 1.2, "640x480")
            audio = make_audio(root / "voice.mp3", 1.13)
            srt = root / "subtitles.srt"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:01,130\n真正厉害的人\n\n", encoding="utf-8",
            )
            shots = [
                {"shot_index": 1, "start_time": 0, "end_time": 0.7, "duration_sec": 0.7},
                {"shot_index": 2, "start_time": 0.7, "end_time": 1.13, "duration_sec": 0.43},
            ]
            template = Path(__file__).resolve().parents[3] / "assets" / "template-daodejing.png"
            (root / "drafts").mkdir()
            with patch.object(video_export_service, "jianying_drafts_root", return_value=root / "drafts"):
                report = video_export_service.create_jianying_native_draft(
                    root / "package", "layout_test", shots, [clip, clip], audio, srt,
                    title_line1="标题一", title_line2="标题二",
                    background_template_path=template,
                    scene_media="video", copy_scenes=False, animate_scenes=False,
                    clip_window=(0, 589, 1080, 742),
                    subtitle_transform_y=video_export_service._transform_y_for_visual_y(1180),
                )
            content = json.loads(
                (Path(report["draft_path"]) / "draft_content.json").read_text(encoding="utf-8"),
            )
            video_tracks = {
                track["name"]: track for track in content["tracks"] if track["type"] == "video"
            }
            background = video_tracks["background"]["segments"][0]
            clip_segment = video_tracks["clips"]["segments"][0]
            self.assertGreater(clip_segment["render_index"], background["render_index"])
            self.assertEqual(background["target_timerange"]["start"], 0)
            self.assertEqual(clip_segment["volume"], 0)
            self.assertAlmostEqual(
                clip_segment["clip"]["scale"]["x"],
                video_export_service._clip_window_scale(640, 480, 1080, 742),
                places=4,
            )
            subtitle = next(track for track in content["tracks"] if track["name"] == "subtitle")
            self.assertAlmostEqual(
                subtitle["segments"][0]["clip"]["transform"]["y"], -0.2292, places=3,
            )
            self.assertFalse(content["materials"].get("material_animations"))


class ClipWindowGeometryTests(unittest.TestCase):
    def test_wide_clip_needs_no_extra_scale(self):
        self.assertAlmostEqual(video_export_service._clip_window_scale(1920, 1080, 1080, 742), 1.0)

    def test_vertical_clip_fits_window_height(self):
        self.assertAlmostEqual(
            video_export_service._clip_window_scale(1080, 1920, 1080, 742), 742 / 1920, places=5,
        )

    def test_four_three_clip_is_centered(self):
        self.assertAlmostEqual(
            video_export_service._clip_window_scale(640, 480, 1080, 742), 0.916, places=3,
        )

    def test_subtitle_position_maps_to_lower_window(self):
        self.assertAlmostEqual(
            video_export_service._transform_y_for_visual_y(1180), -0.2292, places=4,
        )


if __name__ == "__main__":
    unittest.main()
