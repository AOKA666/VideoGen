from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.image_scoring_service import rank_images_for_shot  # noqa: E402
from services.web_image_service import ImageResult, WebImageSearchProvider  # noqa: E402


def result(name: str, source: str) -> ImageResult:
    return ImageResult(
        keyword="钱学森回国",
        title=name,
        thumb_url=f"https://images.example/{name}-thumb.jpg",
        image_url=f"https://images.example/{name}.jpg",
        source_page=f"https://{source}.example/{name}",
        source=source,
    )


class WebImageOptimizationTests(unittest.TestCase):
    @patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": "test-key"})
    def test_configured_brave_channel_contributes_candidates(self) -> None:
        provider = WebImageSearchProvider(provider_name="tencent")
        with (
            patch.object(provider, "_search_tencent", return_value=[result("primary", "tencent")]),
            patch.object(provider, "_search_brave", return_value=[result("secondary", "brave")]),
        ):
            items = provider.search("钱学森回国", limit=10)

        self.assertEqual(["primary", "secondary"], [item.title for item in items])

    @patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": "test-key"})
    def test_fast_round_can_defer_brave_channel(self) -> None:
        provider = WebImageSearchProvider(provider_name="tencent", enable_secondary=False)
        with (
            patch.object(provider, "_search_tencent", return_value=[result("primary", "tencent")]),
            patch.object(provider, "_search_brave", return_value=[result("secondary", "brave")]) as brave,
        ):
            items = provider.search("钱学森回国", limit=10)

        self.assertEqual(["primary"], [item.title for item in items])
        brave.assert_not_called()

    def test_semantic_score_ranks_before_square_aspect_ratio(self) -> None:
        shot = {"search_keywords": ["钱学森回国"]}
        relevant_wide = {
            "title": "钱学森回国",
            "keyword": "钱学森回国",
            "width": 1200,
            "height": 800,
            "source_page": "https://news.example/relevant",
        }
        unrelated_square = {
            "title": "普通城市风景",
            "keyword": "城市风景",
            "width": 1000,
            "height": 1000,
            "source_page": "https://example.com/unrelated",
        }

        ranked = rank_images_for_shot(shot, [unrelated_square, relevant_wide], visual_limit=0)

        self.assertEqual("钱学森回国", ranked[0]["title"])


if __name__ == "__main__":
    unittest.main()
