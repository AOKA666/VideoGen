from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT))

from services.video_export_service import _find_subtitle_font, _find_title_font  # noqa: E402


class ProductionDeploymentTests(unittest.TestCase):
    def test_configured_font_has_priority(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".ttf") as font:
            with patch.dict(os.environ, {
                "VIDEOGEN_SUBTITLE_FONT_FILE": font.name,
                "VIDEOGEN_SUBTITLE_FONT_NAME": "Configured Subtitle",
            }):
                path, name = _find_subtitle_font()
        self.assertEqual(font.name.replace("\\", "/"), path)
        self.assertEqual("Configured Subtitle", name)

    def test_bundled_fonts_are_selected_for_subtitles_and_titles(self) -> None:
        with patch.dict(os.environ, {
            "VIDEOGEN_SUBTITLE_FONT_FILE": "",
            "VIDEOGEN_SUBTITLE_FONT_NAME": "",
            "VIDEOGEN_TITLE_FONT_FILE": "",
            "VIDEOGEN_TITLE_FONT_NAME": "",
        }):
            subtitle_path, subtitle_name = _find_subtitle_font()
            title_path, title_name = _find_title_font()
        self.assertTrue(subtitle_path.endswith("assets/DouyinSansBold.otf"))
        self.assertEqual("Douyin Sans", subtitle_name)
        self.assertTrue(title_path.endswith("assets/龚帆怒放体.ttf"))
        self.assertEqual("gongfannufangti", title_name)

    def test_frontend_uses_same_origin_only_in_production(self) -> None:
        source = (PROJECT_ROOT / "apps/web/src/main.jsx").read_text(encoding="utf-8")
        self.assertIn("import.meta.env.DEV ? 'http://127.0.0.1:8000' : ''", source)

    def test_export_only_produces_a_jianying_draft(self) -> None:
        router_source = (PROJECT_ROOT / "services/api/routers/export.py").read_text(encoding="utf-8")
        frontend_source = (PROJECT_ROOT / "apps/web/src/main.jsx").read_text(encoding="utf-8")
        self.assertNotIn("render_project_video", router_source)
        self.assertNotIn("final_video.mp4", router_source)
        self.assertNotIn("导出 MP4", frontend_source)
        self.assertIn("导出剪映草稿", frontend_source)

    def test_api_is_single_worker_and_storage_is_persistent(self) -> None:
        dockerfile = (PROJECT_ROOT / "Dockerfile.api").read_text(encoding="utf-8")
        compose = (PROJECT_ROOT / "compose.production.yml").read_text(encoding="utf-8")
        self.assertIn('"--workers", "1"', dockerfile)
        self.assertIn("COPY assets/DouyinSansBold.otf", dockerfile)
        self.assertIn("COPY assets/龚帆怒放体.ttf", dockerfile)
        self.assertNotIn("  tunnel:", compose)
        self.assertTrue((PROJECT_ROOT / "compose.tunnel.yml").is_file())
        self.assertIn("./storage:/app/storage:Z", compose)
        self.assertNotIn('ports:\n      - "8000', compose)
        deploy_script = (PROJECT_ROOT / "deploy/deploy.sh").read_text(encoding="utf-8")
        backup_script = (PROJECT_ROOT / "deploy/backup.sh").read_text(encoding="utf-8")
        self.assertNotIn("--remove-orphans", deploy_script)
        self.assertIn('"$ROOT_DIR/deploy/backup.sh"', deploy_script)
        self.assertIn("umask 077", backup_script)

    def test_proxy_blocks_database_and_internal_logs(self) -> None:
        nginx = (PROJECT_ROOT / "deploy/nginx.conf").read_text(encoding="utf-8")
        self.assertIn("location = /storage/db.json { return 404; }", nginx)
        self.assertIn("location ^~ /api/system/logs { return 404; }", nginx)

    def test_secret_files_are_ignored_but_examples_are_tracked(self) -> None:
        ignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env.*", ignore)
        self.assertIn("!.env.production.example", ignore)
        self.assertIn("!apps/web/.env.production.example", ignore)


if __name__ == "__main__":
    unittest.main()
