from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT))

from routers.projects import (  # noqa: E402
    HistoryBookPayload,
    ProjectCreate,
    PromotionBookCreate,
    create_project,
    create_promotion_book,
    delete_promotion_book,
    list_promotion_books,
    select_history_workflow_book,
)


class PromotionBookTests(unittest.TestCase):
    def test_book_list_returns_the_editable_catalog(self) -> None:
        db = {
            "promotion_books": ["国之脊梁", "科学家的家书"],
            "promotion_books_catalog_initialized": True,
            "projects": [{"promotion_book_title": "《大国工匠》"}],
        }
        with patch("routers.projects.load_db", return_value=db):
            result = list_promotion_books()

        self.assertEqual(["《国之脊梁》", "《科学家的家书》"], result["books"])

    def test_custom_book_is_normalized_and_saved(self) -> None:
        db = {"promotion_books": ["国之脊梁"], "projects": []}
        with (
            patch("routers.projects.load_db", return_value=db),
            patch("routers.projects.save_db") as save_db,
        ):
            result = create_promotion_book(PromotionBookCreate(title="  《帝王将相》  "))

        self.assertEqual("《帝王将相》", result["title"])
        self.assertEqual(["国之脊梁", "帝王将相"], db["promotion_books"])
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
                promotion_book_title="《历史深处的民国》",
            ))

        self.assertEqual("历史深处的民国", result["project"]["promotion_book_title"])
        save_db.assert_called_once_with(db)

    def test_frontend_has_editable_history_book_select(self) -> None:
        source = (PROJECT_ROOT / "apps/web/src/main.jsx").read_text(encoding="utf-8")
        self.assertIn('aria-label="选择本次视频推广书籍"', source)
        self.assertIn("＋ 新增图书…", source)
        self.assertIn("addPromotionBookFromSelect", source)
        self.assertIn("deletePromotionBook", source)
        self.assertIn("/history-workflow/book", source)

    def test_selecting_book_resets_history_workflow(self) -> None:
        project = {
            "id": "project-1",
            "promotion_book_title": "国之脊梁",
            "history_workflow": {"active_step": 2},
            "rewritten_script": "旧正文",
            "status": "script_ready",
        }
        db = {"projects": [project], "promotion_books": ["国之脊梁", "帝王将相"]}
        with (
            patch("routers.projects.load_db", return_value=db),
            patch("routers.projects.save_db") as save_db,
        ):
            result = select_history_workflow_book(
                "project-1",
                HistoryBookPayload(title="《帝王将相》"),
            )

        self.assertEqual("帝王将相", project["promotion_book_title"])
        self.assertEqual({}, project["history_workflow"])
        self.assertEqual("", project["rewritten_script"])
        self.assertEqual("《帝王将相》", result["title"])
        save_db.assert_called_once_with(db)

    def test_deleting_book_keeps_at_least_one_catalog_item(self) -> None:
        db = {"projects": [], "promotion_books": ["国之脊梁", "帝王将相"]}
        with (
            patch("routers.projects.load_db", return_value=db),
            patch("routers.projects.save_db") as save_db,
        ):
            result = delete_promotion_book("帝王将相")

        self.assertEqual(["《国之脊梁》"], result["books"])
        save_db.assert_called_once_with(db)


if __name__ == "__main__":
    unittest.main()
