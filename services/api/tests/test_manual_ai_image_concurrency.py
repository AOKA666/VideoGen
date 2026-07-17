from __future__ import annotations

import copy
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from routers.generation import ImageGenerationPayload, generate_image  # noqa: E402


class ManualAiImageConcurrencyTests(unittest.TestCase):
    def test_concurrent_manual_generations_do_not_overwrite_each_other(self) -> None:
        project_id = "project-1"
        state = {
            "projects": [{"id": project_id}],
            "shots": [
                {"id": "shot-1", "project_id": project_id, "shot_index": 1},
                {"id": "shot-2", "project_id": project_id, "shot_index": 2},
            ],
            "generated_assets": [],
        }
        state_lock = threading.Lock()
        generation_barrier = threading.Barrier(2)

        def fake_load_db():
            with state_lock:
                return copy.deepcopy(state)

        def fake_save_db(value):
            with state_lock:
                state.clear()
                state.update(copy.deepcopy(value))

        def fake_generate(path, shot, ratio, prompt):
            generation_barrier.wait(timeout=2)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(shot["id"].encode())
            return {"prompt": prompt, "provider": "test", "model": "test"}

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "routers.generation.load_db",
            side_effect=fake_load_db,
        ), patch("routers.generation.save_db", side_effect=fake_save_db), patch(
            "routers.generation.project_dir",
            return_value=Path(temp_dir),
        ), patch("routers.generation.generate_doubao_image", side_effect=fake_generate), patch(
            "routers.generation.public_url",
            side_effect=lambda path: f"/{path.name}",
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(
                        generate_image,
                        project_id,
                        f"shot-{index}",
                        ImageGenerationPayload(prompt=f"prompt-{index}"),
                    )
                    for index in (1, 2)
                ]
                results = [future.result(timeout=5) for future in futures]

        self.assertEqual(2, len(results))
        self.assertEqual(2, len(state["generated_assets"]))
        self.assertTrue(all(shot["status"] == "ai_generated" for shot in state["shots"]))


if __name__ == "__main__":
    unittest.main()
