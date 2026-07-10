from __future__ import annotations

import sys
import unittest
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.video_ingest_service import build_clip_ranges, clip_ranges_for_mode  # noqa: E402


class VideoClipRangeTests(unittest.TestCase):
    def test_short_scenes_are_merged(self) -> None:
        ranges = build_clip_ranges(12, [1, 2, 6, 9])
        self.assertEqual([(0.0, 6), (6, 9), (9, 12)], ranges)

    def test_long_scene_is_split(self) -> None:
        ranges = build_clip_ranges(34, [])
        self.assertEqual([(0.0, 15.0), (15.0, 30.0), (30.0, 34)], ranges)

    def test_full_mode_preserves_duration(self) -> None:
        ranges = clip_ranges_for_mode(34.567, [4, 8, 12], "full")
        self.assertEqual([(0.0, 34.567)], ranges)


if __name__ == "__main__":
    unittest.main()
