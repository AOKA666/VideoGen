from __future__ import annotations

import copy
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from routers.shots import _generate_ai_images, ai_image_concurrency  # noqa: E402


class ShotAiConcurrencyTests(unittest.TestCase):
    def test_concurrency_is_configurable_and_bounded(self) -> None:
        with patch.dict(os.environ, {"AI_IMAGE_CONCURRENCY": "5"}):
            self.assertEqual(2, ai_image_concurrency(2))
            self.assertEqual(5, ai_image_concurrency(10))
        with patch.dict(os.environ, {"AI_IMAGE_CONCURRENCY": "99"}):
            self.assertEqual(8, ai_image_concurrency(20))
        with patch.dict(os.environ, {"AI_IMAGE_CONCURRENCY": "invalid"}):
            self.assertEqual(5, ai_image_concurrency(10))

    def test_ai_images_are_generated_concurrently_but_saved_safely(self) -> None:
        project_id = "project-1"
        run_id = "run-1"
        shots = [
            {"id": f"shot-{index}", "shot_index": index, "visual_need": f"画面 {index}"}
            for index in range(1, 5)
        ]
        state = {
            "projects": [{"id": project_id, "shot_generation_run_id": run_id}],
            "shots": copy.deepcopy(shots),
            "generated_assets": [],
        }
        state_lock = threading.Lock()
        active_lock = threading.Lock()
        active = 0
        max_active = 0

        def fake_load_db():
            with state_lock:
                return copy.deepcopy(state)

        def fake_save_db(value):
            with state_lock:
                state.clear()
                state.update(copy.deepcopy(value))

        def fake_generate(path, shot, ratio, prompt_override=None, provider="seedream"):
            nonlocal active, max_active
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"image")
            with active_lock:
                active -= 1
            return {"prompt": shot["visual_need"], "provider": "test", "model": "test"}

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"AI_IMAGE_CONCURRENCY": "3"},
        ), patch("routers.shots.load_db", side_effect=fake_load_db), patch(
            "routers.shots.save_db",
            side_effect=fake_save_db,
        ), patch("routers.shots.project_dir", return_value=Path(temp_dir)), patch(
            "routers.shots.generate_ai_image",
            side_effect=fake_generate,
        ), patch("routers.shots.public_url", side_effect=lambda path: f"/{path.name}"):
            _generate_ai_images(project_id, run_id, shots, "openai")

        self.assertGreaterEqual(max_active, 2)
        self.assertLessEqual(max_active, 3)
        self.assertEqual(4, len(state["generated_assets"]))
        self.assertTrue(all(shot["status"] == "ai_generated" for shot in state["shots"]))
        project = state["projects"][0]
        self.assertEqual("shots_ready", project["status"])
        self.assertEqual(4, project["generation_completed"])


if __name__ == "__main__":
    unittest.main()
