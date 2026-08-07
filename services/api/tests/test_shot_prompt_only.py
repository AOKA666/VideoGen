from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from routers.shots import _generate_project_shots  # noqa: E402
from routers.matching import router as matching_router  # noqa: E402


class ShotPromptOnlyTests(unittest.TestCase):
    def test_online_image_search_routes_are_removed(self) -> None:
        paths = {route.path for route in matching_router.routes}

        self.assertNotIn("/api/projects/{project_id}/match-assets", paths)
        self.assertNotIn("/api/projects/{project_id}/stop-image-search", paths)
        self.assertNotIn("/api/projects/{project_id}/retry-failed-shots", paths)
        self.assertNotIn("/api/projects/{project_id}/shots/{shot_id}/retry-image-search", paths)

    def test_prompt_only_saves_prompts_without_generating_or_searching_images(self) -> None:
        project_id = "project-1"
        run_id = "run-1"
        state = {
            "projects": [{
                "id": project_id,
                "raw_script": "测试文案",
                "shot_generation_run_id": run_id,
            }],
            "shots": [],
            "project_assets": [],
            "generated_assets": [],
        }

        def fake_load_db():
            return copy.deepcopy(state)

        def fake_save_db(value):
            state.clear()
            state.update(copy.deepcopy(value))

        generated = [{
            "shot_index": 1,
            "voice_text": "旁白",
            "visual_need": "实验室中的科学家",
        }]
        with patch("routers.shots.load_db", side_effect=fake_load_db), patch(
            "routers.shots.save_db", side_effect=fake_save_db,
        ), patch("routers.shots.generate_shots", return_value=generated) as generate_shots, patch(
            "routers.shots.apply_material_intent",
        ), patch(
            "routers.shots.build_image_prompt", return_value="纪实风格实验室画面",
        ), patch("routers.shots.generate_ai_image") as generate_image:
            _generate_project_shots(
                project_id,
                run_id,
                "prompt_only",
                "openai",
            )

        generate_image.assert_not_called()
        generate_shots.assert_called_once_with("测试文案", model_provider="openai")
        self.assertEqual(1, len(state["shots"]))
        self.assertEqual("纪实风格实验室画面", state["shots"][0]["image_prompt"])
        self.assertEqual("prompt_ready", state["shots"][0]["status"])
        self.assertEqual("shots_ready", state["projects"][0]["status"])
        self.assertEqual("done", state["projects"][0]["generation_stage"])
        self.assertEqual(1, state["projects"][0]["generation_completed"])
        self.assertEqual([], state["generated_assets"])


if __name__ == "__main__":
    unittest.main()
