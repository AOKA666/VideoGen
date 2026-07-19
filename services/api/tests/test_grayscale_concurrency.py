from __future__ import annotations

import copy
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from routers.generation import convert_generated_image_grayscale  # noqa: E402


class GrayscaleConcurrencyTests(unittest.TestCase):
    def test_concurrent_conversions_keep_both_asset_updates(self) -> None:
        project_id = "project-1"
        state_lock = threading.Lock()
        conversion_barrier = threading.Barrier(2)

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            assets = []
            for index in (1, 2):
                path = project_root / f"image-{index}.png"
                Image.new("RGB", (8, 8), (index * 50, 100, 150)).save(path)
                assets.append({
                    "id": f"asset-{index}",
                    "project_id": project_id,
                    "local_path": str(path),
                    "file_name": path.name,
                })
            state = {"generated_assets": assets}

            def fake_load_db():
                with state_lock:
                    return copy.deepcopy(state)

            def fake_save_db(value):
                with state_lock:
                    state.clear()
                    state.update(copy.deepcopy(value))

            def fake_convert(_path):
                conversion_barrier.wait(timeout=2)

            with patch("routers.generation.load_db", side_effect=fake_load_db), patch(
                "routers.generation.save_db", side_effect=fake_save_db,
            ), patch("routers.generation.project_dir", return_value=project_root), patch(
                "routers.generation._convert_grayscale", side_effect=fake_convert,
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(
                            convert_generated_image_grayscale,
                            project_id,
                            f"asset-{index}",
                        )
                        for index in (1, 2)
                    ]
                    [future.result(timeout=5) for future in futures]

        self.assertTrue(all(asset.get("is_grayscale") for asset in state["generated_assets"]))
        self.assertTrue(all(
            asset.get("image_operations", [])[-1]["operation"] == "grayscale"
            for asset in state["generated_assets"]
        ))


if __name__ == "__main__":
    unittest.main()
