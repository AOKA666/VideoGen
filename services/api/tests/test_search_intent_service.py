from __future__ import annotations

import sys
import unittest
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.search_intent_service import sanitize_intent  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
