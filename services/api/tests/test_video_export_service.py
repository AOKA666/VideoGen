from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.video_export_service import render_project_video  # noqa: E402


class ProjectVideoRenderTests(unittest.TestCase):
    def test_render_uses_timed_image_inputs_fades_and_visible_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scenes = [root / "scene_01.png", root / "scene_02.png"]
            captured: dict[str, object] = {}

            def capture_run(command: list[str], cwd: Path | None = None):
                captured["command"] = command
                filter_path = Path(command[command.index("-filter_complex_script") + 1])
                captured["filters"] = filter_path.read_text(encoding="utf-8")

            rendered_probe = {
                "duration_sec": 4.0,
                "video_duration_sec": 4.0,
                "audio_duration_sec": 4.0,
                "width": 1080,
                "height": 1920,
                "video_codec": "h264",
                "audio_codec": "aac",
            }
            with (
                patch("services.video_export_service.shutil.which", return_value="ffmpeg"),
                patch(
                    "services.video_export_service.probe_media",
                    side_effect=[{"duration_sec": 4.0}, rendered_probe],
                ),
                patch("services.video_export_service._run", side_effect=capture_run),
            ):
                report = render_project_video(
                    [
                        {"start_time": 0, "end_time": 2, "duration_sec": 2},
                        {"start_time": 2, "end_time": 4, "duration_sec": 2},
                    ],
                    scenes,
                    root / "voice.wav",
                    root / "subtitles.srt",
                    root / "result.mp4",
                    title_line1="Title one",
                    title_line2="Title two",
                    voice_volume=0.65,
                )

            command = captured["command"]
            filters = str(captured["filters"])
            self.assertEqual(2, command.count("-loop"))
            self.assertIn(
                "[vscene0][vscene1]xfade=transition=fade:duration=0.500000:offset=1.500000[vbase]",
                filters,
            )
            self.assertIn("MarginV=55", filters)
            self.assertEqual(2, filters.count("fontsize=56"))
            self.assertNotIn("\u0332", filters)
            self.assertIn("[2:a]volume=0.650,apad", filters)
            self.assertEqual("fade", report["transition"])
            self.assertEqual(0.5, report["transition_sec"])
            self.assertTrue(report["passed"])

    def test_render_rejects_a_video_stream_that_ends_before_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rendered_probe = {
                "duration_sec": 4.0,
                "video_duration_sec": 1.0,
                "audio_duration_sec": 4.0,
                "width": 1080,
                "height": 1920,
                "video_codec": "h264",
                "audio_codec": "aac",
            }
            with (
                patch("services.video_export_service.shutil.which", return_value="ffmpeg"),
                patch(
                    "services.video_export_service.probe_media",
                    side_effect=[{"duration_sec": 4.0}, rendered_probe],
                ),
                patch("services.video_export_service._run"),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed validation"):
                    render_project_video(
                        [{"start_time": 0, "end_time": 4, "duration_sec": 4}],
                        [root / "scene.png"],
                        root / "voice.wav",
                        root / "subtitles.srt",
                        root / "result.mp4",
                    )


if __name__ == "__main__":
    unittest.main()
