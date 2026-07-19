from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from routers.generation import VoiceSettingsPayload, update_voice_settings  # noqa: E402
from services.video_export_service import render_project_video  # noqa: E402


class VoiceVolumeGainTests(unittest.TestCase):
    def test_voice_settings_accept_gain_above_one_and_cap_it_at_two(self) -> None:
        state = {
            "projects": [{"id": "project-1", "audio_url": "/audio/voice.mp3"}],
        }

        def fake_load_db():
            return copy.deepcopy(state)

        def fake_save_db(value):
            state.clear()
            state.update(copy.deepcopy(value))

        with patch("routers.generation.load_db", side_effect=fake_load_db), patch(
            "routers.generation.save_db", side_effect=fake_save_db,
        ):
            result = update_voice_settings("project-1", VoiceSettingsPayload(volume=1.75))
            self.assertEqual(1.75, result["volume"])
            result = update_voice_settings("project-1", VoiceSettingsPayload(volume=3.0))

        self.assertEqual(2.0, result["volume"])
        self.assertEqual(2.0, state["projects"][0]["voice_volume"])

    def test_mp4_render_uses_gain_above_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            captured = {}

            def capture_run(command: list[str], cwd: Path | None = None):
                filter_path = Path(command[command.index("-filter_complex_script") + 1])
                captured["filters"] = filter_path.read_text(encoding="utf-8")

            rendered_probe = {
                "duration_sec": 2.0,
                "video_duration_sec": 2.0,
                "audio_duration_sec": 2.0,
                "width": 1080,
                "height": 1920,
                "video_codec": "h264",
                "audio_codec": "aac",
            }
            with patch(
                "services.video_export_service.shutil.which", return_value="ffmpeg",
            ), patch(
                "services.video_export_service.probe_media",
                side_effect=[{"duration_sec": 2.0}, rendered_probe],
            ), patch("services.video_export_service._run", side_effect=capture_run):
                render_project_video(
                    [{"start_time": 0, "end_time": 2, "duration_sec": 2}],
                    [root / "scene.png"],
                    root / "voice.wav",
                    root / "subtitles.srt",
                    root / "result.mp4",
                    voice_volume=1.75,
                )

        self.assertIn("[1:a]volume=1.750,apad", captured["filters"])


if __name__ == "__main__":
    unittest.main()
