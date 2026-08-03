from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from routers.generation import VoiceSettingsPayload, update_voice_settings  # noqa: E402


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

if __name__ == "__main__":
    unittest.main()
