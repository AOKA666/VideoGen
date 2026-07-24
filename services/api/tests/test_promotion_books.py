from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT))

from routers.projects import (  # noqa: E402
    AiScriptPayload,
    ProjectCreate,
    PromotionBookCreate,
    create_project,
    create_promotion_book,
    generate_ai_script,
    list_promotion_books,
)


class PromotionBookTests(unittest.TestCase):
    def test_book_list_returns_the_three_supported_titles(self) -> None:
        db = {
            "promotion_books": ["国之脊梁", "科学家的家书"],
            "projects": [{"promotion_book_title": "《大国工匠》"}],
        }
        with patch("routers.projects.load_db", return_value=db):
            result = list_promotion_books()

        self.assertEqual(
            ["《女性人物传记》", "《历史深处的民国》", "《国之脊梁》"],
            result["books"],
        )

    def test_supported_book_is_normalized_without_mutating_the_database(self) -> None:
        db = {"promotion_books": ["国之脊梁"], "projects": []}
        with (
            patch("routers.projects.load_db", return_value=db),
            patch("routers.projects.save_db") as save_db,
        ):
            result = create_promotion_book(PromotionBookCreate(title="  《女性人物传记》  "))

        self.assertEqual("《女性人物传记》", result["title"])
        self.assertEqual(["国之脊梁"], db["promotion_books"])
        save_db.assert_not_called()

    def test_project_keeps_its_selected_promotion_book(self) -> None:
        db = {"projects": [], "promotion_books": ["国之脊梁"]}
        with (
            patch("routers.projects.load_db", return_value=db),
            patch("routers.projects.save_db") as save_db,
            patch("routers.projects.project_dir"),
        ):
            result = create_project(ProjectCreate(
                raw_script="一段用于创建项目的文案。",
                promotion_book_title="《历史深处的民国》",
            ))

        self.assertEqual("历史深处的民国", result["project"]["promotion_book_title"])
        save_db.assert_called_once_with(db)

    def test_frontend_uses_fixed_book_selects_without_custom_add_flow(self) -> None:
        source = (PROJECT_ROOT / "apps/web/src/main.jsx").read_text(encoding="utf-8")
        self.assertIn('aria-label="选择带货书籍"', source)
        self.assertIn('aria-label="选择结尾带书书籍"', source)
        self.assertIn("《女性人物传记》", source)
        self.assertIn("《历史深处的民国》", source)
        self.assertNotIn("新增图书", source)

    def test_ai_script_generation_receives_selected_book(self) -> None:
        with patch(
            "routers.projects.generate_guozhijiliang_script",
            return_value={"script": "人物故事"},
        ) as generate:
            result = generate_ai_script(AiScriptPayload(
                promotion_book_title="《女性人物传记》",
            ))

        generate.assert_called_once_with("", "", "女性人物传记")
        self.assertEqual("人物故事", result["script"])

    def test_frontend_sends_selected_book_to_ai_script_generation(self) -> None:
        source = (PROJECT_ROOT / "apps/web/src/main.jsx").read_text(encoding="utf-8")

        self.assertIn("'/api/projects/generate-ai-script'", source)
        self.assertIn("promotion_book_title: promotionBookTitle", source)
        self.assertIn("留空则选择一位经历不易的真实女性", source)
        self.assertIn("留空则选择一位晚清或民国关键人物", source)


if __name__ == "__main__":
    unittest.main()
