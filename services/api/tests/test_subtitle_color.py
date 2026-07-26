from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services import video_export_service  # noqa: E402


class SubtitleColorTests(unittest.TestCase):
    def test_mp4_and_jianying_subtitles_are_white(self) -> None:
        self.assertEqual("&H00FFFFFF", video_export_service.SUBTITLE_ASS_PRIMARY_COLOR)
        self.assertEqual((1.0, 1.0, 1.0), video_export_service.SUBTITLE_RGB_COLOR)

        mp4_source = inspect.getsource(video_export_service.render_project_video)
        draft_source = inspect.getsource(video_export_service.create_jianying_native_draft)
        self.assertIn("SUBTITLE_ASS_PRIMARY_COLOR", mp4_source)
        self.assertIn("color=SUBTITLE_RGB_COLOR", draft_source)


if __name__ == "__main__":
    unittest.main()
