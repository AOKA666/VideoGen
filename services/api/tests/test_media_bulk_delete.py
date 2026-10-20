from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from fastapi import HTTPException  # noqa: E402

from routers import media  # noqa: E402
from services import store  # noqa: E402


class MediaBulkDeleteTests(unittest.TestCase):
    def setUp(self):
        self.previous = store.storage_dir()
        self.storage = tempfile.TemporaryDirectory()
        store.configure_storage(self.storage.name)
        store.ensure_storage()

        self.videos = {}
        for asset_id in ("free-video", "used-video"):
            path = store.assets_dir() / "videos" / f"{asset_id}.mp4"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"video")
            self.videos[asset_id] = path

        self.products = {}
        for asset_id in ("free-product", "used-product", "preset-product"):
            path = store.assets_dir() / "product_library" / "images" / f"{asset_id}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"image")
            self.products[asset_id] = path

        store.save_db({
            **store.DEFAULT_DB,
            "video_library": [
                {"id": asset_id, "local_path": str(path), "file_name": path.name}
                for asset_id, path in self.videos.items()
            ],
            "shots": [{"id": "shot-1", "project_id": "project-1", "selected_asset_id": "used-video"}],
            "product_library": [
                {"id": asset_id, "local_path": str(path), "file_name": path.name, "media_type": "image"}
                for asset_id, path in self.products.items()
            ],
            "projects": [{
                "id": "project-1",
                "workflow_type": "video_library",
                "status": "voice_ready",
                "product_placement": {"items": [{"asset_id": "used-product"}]},
            }],
            "product_placement_presets": [{
                "id": "preset-1",
                "items": [{"asset_id": "preset-product"}],
            }],
        })

    def tearDown(self):
        store.configure_storage(self.previous)
        self.storage.cleanup()

    def test_bulk_delete_videos_deletes_free_items_and_skips_used(self):
        result = media.bulk_delete_videos(media.BulkAssetDeletePayload(
            asset_ids=["free-video", "used-video", "free-video", "missing"],
        ))

        self.assertEqual(["free-video"], result["deleted_ids"])
        self.assertEqual(
            {"used-video": "素材已被项目使用", "missing": "素材不存在"},
            {item["asset_id"]: item["reason"] for item in result["skipped"]},
        )
        self.assertFalse(self.videos["free-video"].exists())
        self.assertTrue(self.videos["used-video"].exists())
        remaining = {item["id"] for item in store.load_db()["video_library"]}
        self.assertEqual({"used-video"}, remaining)

    def test_bulk_delete_videos_rejects_while_generation_is_running(self):
        db = store.load_db()
        db["projects"][0]["status"] = "video_generating"
        store.save_db(db)

        with self.assertRaises(HTTPException) as error:
            media.bulk_delete_videos(media.BulkAssetDeletePayload(asset_ids=["free-video"]))
        self.assertEqual(409, error.exception.status_code)
        self.assertTrue(self.videos["free-video"].exists())

    def test_bulk_delete_products_skips_project_and_preset_usage(self):
        result = media.bulk_delete_products(media.BulkAssetDeletePayload(
            asset_ids=["free-product", "used-product", "preset-product"],
        ))

        self.assertEqual(["free-product"], result["deleted_ids"])
        self.assertEqual(
            {
                "used-product": "带货素材已被项目使用",
                "preset-product": "带货素材已被带货预设使用",
            },
            {item["asset_id"]: item["reason"] for item in result["skipped"]},
        )
        self.assertFalse(self.products["free-product"].exists())
        self.assertTrue(self.products["used-product"].exists())
        self.assertTrue(self.products["preset-product"].exists())
        remaining = {item["id"] for item in store.load_db()["product_library"]}
        self.assertEqual({"used-product", "preset-product"}, remaining)

    def test_bulk_delete_rejects_empty_selection(self):
        with self.assertRaises(HTTPException) as videos:
            media.bulk_delete_videos(media.BulkAssetDeletePayload(asset_ids=[]))
        self.assertEqual(400, videos.exception.status_code)

        with self.assertRaises(HTTPException) as products:
            media.bulk_delete_products(media.BulkAssetDeletePayload(asset_ids=["  "]))
        self.assertEqual(400, products.exception.status_code)


if __name__ == "__main__":
    unittest.main()
