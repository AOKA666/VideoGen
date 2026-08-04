from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from routers.generation import download_generated_image_png  # noqa: E402


class GeneratedImageDownloadTests(unittest.TestCase):
    def test_unicode_filename_uses_rfc5987_content_disposition(self) -> None:
        project_id = "project-1"
        asset_id = "asset-1"

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            image_path = project_root / "source.png"
            Image.new("RGB", (8, 8), (120, 80, 40)).save(image_path)
            state = {
                "generated_assets": [{
                    "id": asset_id,
                    "project_id": project_id,
                    "local_path": str(image_path),
                    "file_name": "新国风宋式水墨工笔.png",
                }],
            }

            with patch("routers.generation.load_db", return_value=state), patch(
                "routers.generation.project_dir", return_value=project_root,
            ):
                response = download_generated_image_png(project_id, asset_id)

        disposition = response.headers["content-disposition"]
        disposition.encode("latin-1")
        self.assertTrue(disposition.startswith("attachment; filename*=UTF-8''"))
        self.assertIn("%E6%96%B0%E5%9B%BD%E9%A3%8E", disposition)


if __name__ == "__main__":
    unittest.main()
