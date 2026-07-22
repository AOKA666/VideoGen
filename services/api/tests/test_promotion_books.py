from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT))

from routers.projects import (  # noqa: E402
    ProjectCreate,
    PromotionBookCreate,
    create_project,
    create_promotion_book,
    list_promotion_books,
)


class PromotionBookTests(unittest.TestCase):
    def test_book_list_includes_default_saved_and_historical_project_titles(self) -> None:
        db = {
            "promotion_books": ["国之脊梁", "科学家的家书"],
            "projects": [{"promotion_book_title": "《大国工匠》"}],
        }
        with patch("routers.projects.load_db", return_value=db):
            result = list_promotion_books()

        self.assertEqual(["《国之脊梁》", "《科学家的家书》", "《大国工匠》"], result["books"])

    def test_new_book_is_normalized_saved_and_selected(self) -> None:
        db = {"promotion_books": ["国之脊梁"], "projects": []}
        with (
            patch("routers.projects.load_db", return_value=db),
            patch("routers.projects.save_db") as save_db,
        ):
            result = create_promotion_book(PromotionBookCreate(title="  《中国科学家家书》  "))

        self.assertEqual("《中国科学家家书》", result["title"])
        self.assertIn("中国科学家家书", db["promotion_books"])
        save_db.assert_called_once_with(db)

    def test_project_keeps_its_selected_promotion_book(self) -> None:
        db = {"projects": [], "promotion_books": ["国之脊梁"]}
        with (
            patch("routers.projects.load_db", return_value=db),
            patch("routers.projects.save_db") as save_db,
            patch("routers.projects.project_dir"),
        ):
            result = create_project(ProjectCreate(
                raw_script="一段用于创建项目的文案。",
                promotion_book_title="《科学家的家书》",
            ))

        self.assertEqual("科学家的家书", result["project"]["promotion_book_title"])
        self.assertIn("科学家的家书", db["promotion_books"])
        save_db.assert_called_once_with(db)

    def test_frontend_uses_book_select_and_add_endpoint(self) -> None:
        source = (PROJECT_ROOT / "apps/web/src/main.jsx").read_text(encoding="utf-8")
        self.assertIn('aria-label="选择带货书籍"', source)
        self.assertIn("/api/projects/promotion-books", source)
        self.assertIn("新增图书", source)


if __name__ == "__main__":
    unittest.main()
