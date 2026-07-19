from __future__ import annotations

import sys
import unittest
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.text_service import build_rewrite_prompt, ensure_rewrite_book_promotion  # noqa: E402


class BookPromotionCopyTests(unittest.TestCase):
    def test_enabled_promotion_requires_product_value_and_purchase_motivation(self) -> None:
        prompt = build_rewrite_prompt(
            "他放弃高薪回到祖国，在实验室工作了一生。",
            "纪实故事型",
            1,
            append_book_promotion=True,
            promotion_book_title="国之脊梁",
        )

        self.assertIn("最后 2 到 3 个自然段", prompt)
        self.assertIn("140 到 220 个中文字符", prompt)
        self.assertIn("塑造产品价值", prompt)
        self.assertIn("明确的读者理由和阅读场景", prompt)
        self.assertIn("自然产生想把书带回家", prompt)
        self.assertIn("允许并必须重新设计完整的产品价值塑造", prompt)

    def test_fallback_promotion_is_more_than_a_book_name_mention(self) -> None:
        promoted = ensure_rewrite_book_promotion("故事正文。", True, "国之脊梁")
        promotion = promoted.split("\n\n")[-1]

        self.assertGreaterEqual(len(promotion), 140)
        self.assertIn("利益、亲情和责任面前如何作答", promotion)
        self.assertIn("和孩子一起慢慢看", promotion)


if __name__ == "__main__":
    unittest.main()
