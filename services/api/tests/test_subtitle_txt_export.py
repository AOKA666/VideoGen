from __future__ import annotations

import sys
import unittest
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.generation_service import (  # noqa: E402
    chinese_char_count,
    generate_export_subtitle_txt,
)


class SubtitleTxtExportTests(unittest.TestCase):
    def test_plain_text_subtitles_put_each_six_character_chunk_on_its_own_line(self) -> None:
        shots = [{
            "id": "shot-1",
            "shot_index": 1,
            "start_time": 0,
            "end_time": 4,
            "voice_text": "真正厉害的人，往往懂得沉默。",
        }]

        content = generate_export_subtitle_txt(shots)
        lines = content.splitlines()

        self.assertTrue(lines)
        self.assertTrue(all(chinese_char_count(line) <= 6 for line in lines))
        self.assertEqual(["真正厉害的人", "往往懂得沉默"], lines)
        self.assertTrue(content.endswith("\n"))

    def test_empty_subtitles_produce_an_empty_text_file(self) -> None:
        self.assertEqual("", generate_export_subtitle_txt([]))


if __name__ == "__main__":
    unittest.main()
