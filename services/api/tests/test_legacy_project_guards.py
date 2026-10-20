from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from fastapi import HTTPException  # noqa: E402

from routers import export, generation, projects  # noqa: E402
from services import store  # noqa: E402


class LegacyProjectGuardTests(unittest.TestCase):
    def setUp(self):
        self.previous = store.storage_dir()
        self.storage = tempfile.TemporaryDirectory()
        store.configure_storage(self.storage.name)
        store.ensure_storage()
        self.project_id = "legacy-project"
        store.save_db({
            **store.DEFAULT_DB,
            "projects": [{
                "id": self.project_id,
                "name": "旧项目",
                "raw_script": "旧项目文案。",
                "status": "shots_ready",
                "title_line1": "旧标题一",
                "title_line2": "旧标题二",
            }],
        })

    def tearDown(self):
        store.configure_storage(self.previous)
        self.storage.cleanup()

    def test_active_endpoints_reject_legacy_projects(self):
        with self.assertRaises(HTTPException) as voice:
            generation.generate_voice(self.project_id, None)
        self.assertEqual(400, voice.exception.status_code)

        with self.assertRaises(HTTPException) as subtitles:
            generation.generate_subtitles(self.project_id)
        self.assertEqual(400, subtitles.exception.status_code)

        with self.assertRaises(HTTPException) as cover:
            generation.generate_cover(self.project_id)
        self.assertEqual(400, cover.exception.status_code)

        with self.assertRaises(HTTPException) as package:
            export.export_package(self.project_id)
        self.assertEqual(400, package.exception.status_code)

    def test_legacy_project_can_be_archived_and_deleted_but_not_renamed(self):
        with self.assertRaises(HTTPException) as rename:
            projects.update_script(self.project_id, projects.ScriptUpdate(name="新名字"))
        self.assertEqual(400, rename.exception.status_code)

        result = projects.update_script(self.project_id, projects.ScriptUpdate(archived=True))
        self.assertEqual("saved", result["status"])
        project = next(p for p in store.load_db()["projects"] if p["id"] == self.project_id)
        self.assertTrue(project["archived"])

        deleted = projects.delete_project(self.project_id)
        self.assertEqual("deleted", deleted["status"])
        self.assertFalse(any(p["id"] == self.project_id for p in store.load_db()["projects"]))


if __name__ == "__main__":
    unittest.main()
