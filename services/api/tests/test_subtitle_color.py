from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services import video_export_service  # noqa: E402


class SubtitleColorTests(unittest.TestCase):
    def test_jianying_subtitles_are_white_and_have_a_strong_border(self) -> None:
        self.assertEqual((1.0, 1.0, 1.0), video_export_service.SUBTITLE_RGB_COLOR)
        self.assertEqual(70.0, video_export_service.SUBTITLE_JIANYING_BORDER_WIDTH)
        self.assertEqual(20.0, video_export_service.SUBTITLE_JIANYING_FONT_SIZE)

        draft_source = inspect.getsource(video_export_service.create_jianying_native_draft)
        self.assertIn("color=SUBTITLE_RGB_COLOR", draft_source)
        self.assertIn("width=SUBTITLE_JIANYING_BORDER_WIDTH", draft_source)
        self.assertIn("size=SUBTITLE_JIANYING_FONT_SIZE", draft_source)
        self.assertIn("auto_wrapping=True", draft_source)

    def test_jianying_title_uses_editable_feiyang_text_layers(self) -> None:
        draft_source = inspect.getsource(video_export_service.create_jianying_native_draft)
        self.assertIn("draft.FontType.飞扬行书", draft_source)
        self.assertIn('draft.TrackType.text, "title_line1"', draft_source)
        self.assertIn('draft.TrackType.text, "title_line2"', draft_source)
        self.assertNotIn('draft.TrackType.video, "title_overlay"', draft_source)

    def test_jianying_title_geometry_matches_the_previous_image_title(self) -> None:
        geometry = video_export_service._jianying_title_geometry("《道德经》", "命运的巨大漏洞")
        self.assertEqual(20.0, geometry["font_size"])
        self.assertAlmostEqual(0.645, geometry["line1_transform_y"], places=3)
        self.assertAlmostEqual(0.485, geometry["line2_transform_y"], places=3)


if __name__ == "__main__":
    unittest.main()
