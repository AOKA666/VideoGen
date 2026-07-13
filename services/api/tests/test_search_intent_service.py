from __future__ import annotations

import sys
import unittest
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.search_intent_service import apply_intent_to_shot, sanitize_intent  # noqa: E402


class SearchIntentSanitizationTests(unittest.TestCase):
    def test_subject_plus_number_gets_event_suffix(self) -> None:
        result = sanitize_intent({"core_keyword": "\u738b\u4f1f1192"})

        self.assertEqual("\u738b\u4f1f1192\u4e8b\u4ef6", result["core_keyword"])
        self.assertEqual(["\u738b\u4f1f1192\u4e8b\u4ef6"], result["search_keywords"])

    def test_bare_number_uses_numbered_phrase_from_shot_text(self) -> None:
        result = sanitize_intent(
            {"core_keyword": "1192"},
            "\u738b\u4f1f1192\u727a\u7272\u4e8b\u4ef6",
        )

        self.assertEqual("\u738b\u4f1f1192\u727a\u7272\u4e8b\u4ef6", result["core_keyword"])

    def test_multiple_complementary_queries_are_preserved_on_shot(self) -> None:
        intent = sanitize_intent({
            "core_keyword": "钱学森回国",
            "search_keywords": ["钱学森回国", "钱学森乘船", "钱学森归国照"],
        })
        shot = {"voice_text": "钱学森乘船回到祖国"}

        apply_intent_to_shot(shot, intent)

        self.assertEqual(["钱学森回国", "钱学森乘船", "钱学森归国照"], shot["search_keywords"])


if __name__ == "__main__":
    unittest.main()
