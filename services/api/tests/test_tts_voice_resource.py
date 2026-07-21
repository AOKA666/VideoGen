from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.generation_service import volc_tts_resource_id  # noqa: E402


class TtsVoiceResourceTests(unittest.TestCase):
    def test_cloned_voice_uses_seed_icl_resource(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual("seed-icl-2.0", volc_tts_resource_id("S_6Sd6jOE42"))

    def test_standard_voice_keeps_seed_tts_resource(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                "seed-tts-2.0",
                volc_tts_resource_id("zh_male_m191_uranus_bigtts"),
            )

    def test_cloned_resource_can_be_overridden(self) -> None:
        with patch.dict("os.environ", {"VOLC_TTS_CLONED_RESOURCE_ID": "seed-icl-1.0"}, clear=True):
            self.assertEqual("seed-icl-1.0", volc_tts_resource_id("S_custom"))


if __name__ == "__main__":
    unittest.main()
