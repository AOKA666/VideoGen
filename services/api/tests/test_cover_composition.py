from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from services.generation_service import (  # noqa: E402
    _cover_title_start_y,
    compose_uploaded_cover,
    normalize_cover_title_positions,
)


class CoverCompositionTests(unittest.TestCase):
    def test_cover_keeps_the_full_nine_by_sixteen_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.png"
            output_path = root / "cover.png"
            source = Image.new("RGB", (90, 160), (30, 160, 60))
            draw = ImageDraw.Draw(source)
            draw.rectangle((0, 0, 89, 19), fill=(220, 30, 30))
            draw.rectangle((0, 140, 89, 159), fill=(30, 60, 220))
            source.save(source_path)

            compose_uploaded_cover(source_path, output_path, "标题一", "标题二")

            with Image.open(output_path) as cover:
                self.assertEqual((1080, 1920), cover.size)
                self.assertEqual((220, 30, 30), cover.getpixel((12, 12)))
                self.assertEqual((30, 60, 220), cover.getpixel((12, 1907)))
                white_rows: dict[int, int] = {}
                yellow_rows: dict[int, int] = {}
                for y in range(0, 700):
                    for x in range(cover.width):
                        pixel = cover.getpixel((x, y))
                        if pixel == (255, 255, 255):
                            white_rows[y] = white_rows.get(y, 0) + 1
                        elif pixel == (255, 220, 0):
                            yellow_rows[y] = yellow_rows.get(y, 0) + 1
                self.assertTrue(white_rows)
                self.assertTrue(yellow_rows)
                self.assertLess(min(white_rows), min(yellow_rows))
                self.assertGreater(max(yellow_rows.values()), 150)

    def test_title_center_is_mirrored_across_the_canvas_center(self) -> None:
        total_height = 220
        top_start = _cover_title_start_y(1920, total_height)
        top_center = top_start + total_height / 2
        former_bottom_center = 1511
        self.assertEqual(1920, top_center + former_bottom_center)
        self.assertLess(top_center, 960)

    def test_each_title_line_accepts_an_independent_position_and_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.png"
            output_path = root / "cover.png"
            Image.new("RGB", (90, 160), (20, 30, 40)).save(source_path)
            positions = {
                "line1": {"x": 0.25, "y": 0.65, "font_size": 72},
                "line2": {"x": 0.75, "y": 0.82, "font_size": 180},
            }

            compose_uploaded_cover(source_path, output_path, "第一行", "第二行", positions)

            with Image.open(output_path) as cover:
                white_pixels = []
                yellow_pixels = []
                for y in range(900, cover.height):
                    for x in range(cover.width):
                        pixel = cover.getpixel((x, y))
                        if pixel == (255, 255, 255):
                            white_pixels.append((x, y))
                        elif pixel == (255, 220, 0):
                            yellow_pixels.append((x, y))
                self.assertTrue(white_pixels)
                self.assertTrue(yellow_pixels)
                self.assertLess(sum(x for x, _ in white_pixels) / len(white_pixels), 540)
                self.assertGreater(sum(x for x, _ in yellow_pixels) / len(yellow_pixels), 540)
                self.assertGreater(len(yellow_pixels), len(white_pixels))

    def test_title_position_values_are_clamped(self) -> None:
        positions = normalize_cover_title_positions({
            "line1": {"x": -2, "y": 4, "font_size": 900},
        })
        self.assertEqual({"x": 0.03, "y": 0.97, "font_size": 260}, positions["line1"])
        self.assertEqual(124, positions["line2"]["font_size"])

    def test_cover_applies_black_mask_below_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.png"
            output_path = root / "cover.png"
            Image.new("RGB", (90, 160), (200, 100, 50)).save(source_path)

            compose_uploaded_cover(
                source_path,
                output_path,
                "标题一",
                "标题二",
                mask_opacity=0.5,
            )

            with Image.open(output_path) as cover:
                self.assertEqual((100, 50, 25), cover.getpixel((12, 1200)))


if __name__ == "__main__":
    unittest.main()
