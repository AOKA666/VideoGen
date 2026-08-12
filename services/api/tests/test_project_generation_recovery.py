from __future__ import annotations

import copy
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from routers.projects import get_project  # noqa: E402


class ProjectGenerationRecoveryTests(unittest.TestCase):
    def test_completed_shots_recover_stale_generating_project(self) -> None:
        project_id = "project-1"
        state = {
            "projects": [{
                "id": project_id,
                "status": "generating_images",
                "generation_stage": "generating_ai_images",
                "generation_completed": 1,
                "generation_total": 2,
                "current_generation_message": "正在并发生成 AI 图片：1/2",
            }],
            "shots": [
                {"id": "shot-1", "project_id": project_id, "shot_index": 1, "status": "ai_generated"},
                {"id": "shot-2", "project_id": project_id, "shot_index": 2, "status": "ai_generated"},
            ],
            "generated_assets": [],
        }
        state_lock = threading.Lock()

        def fake_load_db():
            with state_lock:
                return copy.deepcopy(state)

        def fake_save_db(value):
            with state_lock:
                state.clear()
                state.update(copy.deepcopy(value))

        with patch("routers.projects.load_db", side_effect=fake_load_db), patch(
            "routers.projects.save_db", side_effect=fake_save_db,
        ):
            result = get_project(project_id)

        project = result["project"]
        self.assertEqual("shots_ready", project["status"])
        self.assertEqual("done", project["generation_stage"])
        self.assertEqual(2, project["generation_completed"])
        self.assertEqual("", project["current_generation_message"])


if __name__ == "__main__":
    unittest.main()
