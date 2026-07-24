from __future__ import annotations

import sys
import unittest
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.text_service import build_rewrite_prompt, ensure_rewrite_book_promotion, load_book_promotion_guidelines  # noqa: E402


class BookPromotionCopyTests(unittest.TestCase):
    def test_enabled_promotion_requires_product_value_and_purchase_motivation(self) -> None:
        prompt = build_rewrite_prompt(
            "他放弃高薪回到祖国，在实验室工作了一生。",
            "纪实故事型",
            1,
            append_book_promotion=True,
            promotion_book_title="国之脊梁",
        )

        self.assertIn("选中的商品是《国之脊梁》", prompt)
        self.assertIn("了解中国科技如何从一穷二白走到今天", prompt)
        self.assertIn("尤其适合家长和孩子一起读", prompt)
        self.assertIn("控制在250到350字", prompt)
        self.assertIn("不要虚构书中具体人物、章节、数据、名言或出版信息", prompt)

    def test_fallback_promotion_is_more_than_a_book_name_mention(self) -> None:
        promoted = ensure_rewrite_book_promotion("故事正文。", True, "国之脊梁")
        promotion = promoted.removeprefix("故事正文。").strip()

        self.assertGreaterEqual(len([item for item in promotion.split("。") if item.strip()]), 4)
        self.assertEqual(3, len(promotion.split("\n\n")))
        self.assertIn("中国科学家和科技工作者", promotion)
        self.assertIn("家长和孩子一起读", promotion)

    def test_each_supported_book_uses_its_own_promotion_rules(self) -> None:
        female_prompt = build_rewrite_prompt(
            "女性人物故事。", "纪实故事型", 1,
            append_book_promotion=True, promotion_book_title="女性人物传记",
        )
        history_prompt = build_rewrite_prompt(
            "民国人物故事。", "纪实故事型", 1,
            append_book_promotion=True, promotion_book_title="历史深处的民国",
        )

        self.assertIn("五个女人、五种命运", female_prompt)
        self.assertIn("杨绛传", female_prompt)
        self.assertNotIn("晚清为何走向崩塌", female_prompt)
        self.assertIn("晚清为何走向崩塌", history_prompt)
        self.assertIn("军阀、革命、改革和抗战", history_prompt)
        self.assertNotIn("五个女人、五种命运", history_prompt)

    def test_disabled_promotion_does_not_append_selected_book(self) -> None:
        script = "故事正文。"

        self.assertEqual(script, ensure_rewrite_book_promotion(script, False, "女性人物传记"))

    def test_repository_prompt_file_exposes_all_three_books(self) -> None:
        for title in ("女性人物传记", "历史深处的民国", "国之脊梁"):
            self.assertTrue(load_book_promotion_guidelines(title))


if __name__ == "__main__":
    unittest.main()
