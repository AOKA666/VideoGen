from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.text_service import clean_shot_visual_terms, generate_shots, keywords_from_text  # noqa: E402


class ShotTagGenerationTests(unittest.TestCase):
    def test_keywords_from_text_does_not_slice_script_fragments(self) -> None:
        tags = keywords_from_text("alpha beta gamma delta epsilon")

        flattened = tags["people"] + tags["scene"] + tags["era"] + tags["keywords"]
        self.assertEqual([], flattened)

    def test_generate_shots_leaves_tags_empty_when_ai_visuals_unavailable(self) -> None:
        script = "alpha beta gamma delta epsilon\nzeta eta theta iota kappa"

        with patch.dict("os.environ", {"BIGMODEL_API_KEY": ""}):
            shots = generate_shots(script)

        self.assertGreaterEqual(len(shots), 1)
        for shot in shots:
            self.assertEqual([], shot["required_object"])
            self.assertEqual([], shot["required_scene"])
            self.assertEqual([], shot["object_tags"])
            self.assertEqual([], shot["scene_tags"])
            self.assertEqual([], shot["keywords"])
            self.assertEqual([], shot["search_keywords"])

    def test_clean_shot_visual_terms_rejects_sentence_fragments(self) -> None:
        terms = clean_shot_visual_terms(
            [
                "\u7684\u538b\u529b",
                "\u753b\u9762\u9700\u8981",
                "validSubject",
                "subject,with,punctuation",
                "toolongfragmentvalue",
            ],
            max_length=12,
        )

        self.assertEqual(["validSubject"], terms)


if __name__ == "__main__":
    unittest.main()
