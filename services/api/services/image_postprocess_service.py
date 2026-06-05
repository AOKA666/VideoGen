from __future__ import annotations

from io import BytesIO
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageFilter
except Exception:  # pragma: no cover - optional image cleanup
    Image = None
    ImageFilter = None
    np = None


def _region_has_watermark(image: Image.Image, box: tuple[int, int, int, int]) -> bool:
    if np is None:
        return False
    crop = image.crop(box).convert("L")
    arr = np.asarray(crop, dtype=np.float32) / 255.0
    if arr.size == 0:
        return False
    bright = float((arr > 0.82).mean())
    dark = float((arr < 0.28).mean())
    contrast = float(arr.std())
    gx = float(np.abs(np.diff(arr, axis=1)).mean()) if arr.shape[1] > 1 else 0.0
    gy = float(np.abs(np.diff(arr, axis=0)).mean()) if arr.shape[0] > 1 else 0.0
    ink = bright + dark
    return contrast > 0.13 and ink > 0.04 and (gx + gy) > 0.035


BLOCKED_WATERMARK_SOURCES = [
    "kongfz", "孔夫子旧书网", "7788", "997788", "book.kongfz.com", "拍摄", "样图",
]


def _component_boxes(mask: np.ndarray, *, max_components: int = 1200) -> list[tuple[int, int, int, int, int]]:
    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    boxes: list[tuple[int, int, int, int, int]] = []
    components = 0
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or visited[y, x]:
                continue
            components += 1
            if components > max_components:
                return boxes
            stack = [(y, x)]
            visited[y, x] = True
            area = 0
            min_y = max_y = y
            min_x = max_x = x
            while stack:
                cy, cx = stack.pop()
                area += 1
                min_y, max_y = min(min_y, cy), max(max_y, cy)
                min_x, max_x = min(min_x, cx), max(max_x, cx)
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            boxes.append((min_x, min_y, max_x + 1, max_y + 1, area))
    return boxes


def detect_blocking_watermark_from_image(image: Image.Image, meta_text: str = "") -> dict:
    meta = (meta_text or "").lower()
    source_hits = [word for word in BLOCKED_WATERMARK_SOURCES if word.lower() in meta]
    if source_hits:
        return {"rejected": True, "reason": f"低质量水印来源：{source_hits[0]}", "regions": []}
    if np is None:
        return {"rejected": False, "reason": "", "regions": []}

    probe = image.convert("RGB")
    probe.thumbnail((520, 520))
    arr = np.asarray(probe, dtype=np.float32)
    if arr.size == 0:
        return {"rejected": False, "reason": "", "regions": []}

    height, width = arr.shape[:2]
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    maxc = arr.max(axis=2)
    minc = arr.min(axis=2)
    saturation = (maxc - minc) / np.maximum(maxc, 1)
    lower_zone = np.zeros((height, width), dtype=bool)
    lower_zone[int(height * 0.55):, :] = True
    center_zone = np.zeros((height, width), dtype=bool)
    center_zone[int(height * 0.28):int(height * 0.72), :] = True
    right_bottom_zone = np.zeros((height, width), dtype=bool)
    right_bottom_zone[int(height * 0.62):, int(width * 0.52):] = True

    red_text = (r > 120) & (r > g * 1.18) & (r > b * 1.18) & ((r - np.maximum(g, b)) > 24)
    red_text &= lower_zone | center_zone | right_bottom_zone
    red_ratio = float(red_text.mean())
    corner_height = max(1, int(height * 0.22))
    corner_width = max(1, int(width * 0.36))
    edge_height = max(1, int(height * 0.12))
    bottom_left_red = float(red_text[-corner_height:, :corner_width].mean())
    bottom_right_red = float(red_text[-corner_height:, -corner_width:].mean())
    edge_left_red = float(red_text[-edge_height:, :corner_width].mean())
    edge_right_red = float(red_text[-edge_height:, -corner_width:].mean())
    if max(bottom_left_red, bottom_right_red) > 0.10 or max(edge_left_red, edge_right_red) > 0.08:
        return {"rejected": True, "reason": "底部角落大红色 Logo/水印", "regions": []}
    red_regions = []
    red_text_like_boxes = []
    red_elongated_boxes = []
    for x1, y1, x2, y2, area in _component_boxes(red_text):
        box_w = x2 - x1
        box_h = y2 - y1
        area_ratio = area / max(width * height, 1)
        fill_ratio = area / max(box_w * box_h, 1)
        if (
            0.0003 <= area_ratio <= 0.04
            and box_h <= height * 0.14
            and box_w >= width * 0.16
            and box_w / max(box_h, 1) >= 2.4
        ):
            red_elongated_boxes.append((x1, y1, x2, y2, area))
            red_regions.append([x1, y1, x2, y2])
        text_like = (
            0.00015 <= area_ratio <= 0.035
            and box_h <= height * 0.18
            and box_w <= width * 0.72
            and 0.06 <= fill_ratio <= 0.88
        )
        if text_like:
            red_text_like_boxes.append((x1, y1, x2, y2, area))
            red_regions.append([x1, y1, x2, y2])

    aligned_text = False
    if len(red_text_like_boxes) >= 2:
        centers = [((box[1] + box[3]) / 2) for box in red_text_like_boxes]
        span_x = max(box[2] for box in red_text_like_boxes) - min(box[0] for box in red_text_like_boxes)
        span_y = max(centers) - min(centers)
        aligned_text = span_x >= width * 0.18 and span_y <= height * 0.16

    if red_ratio > 0.004 and (red_elongated_boxes or aligned_text):
        return {"rejected": True, "reason": "明显红色文字 Logo/水印", "regions": red_regions[:6]}

    gray = np.asarray(probe.convert("L"), dtype=np.float32) / 255.0
    edges = np.zeros_like(gray, dtype=bool)
    if gray.shape[0] > 1 and gray.shape[1] > 1:
        gx = np.abs(np.diff(gray, axis=1, append=gray[:, -1:]))
        gy = np.abs(np.diff(gray, axis=0, append=gray[-1:, :]))
        edges = (gx + gy) > 0.18
    high_contrast_text = edges & ((gray < 0.34) | (gray > 0.72)) & (lower_zone | right_bottom_zone)
    text_regions = []
    for x1, y1, x2, y2, area in _component_boxes(high_contrast_text):
        box_w = x2 - x1
        box_h = y2 - y1
        if area > width * height * 0.003 and box_w > width * 0.14 and box_h > height * 0.035:
            text_regions.append([x1, y1, x2, y2])
    if text_regions and float(high_contrast_text.mean()) > 0.01:
        return {"rejected": True, "reason": "底部/角落大 Logo 或文字水印", "regions": text_regions[:6]}

    return {"rejected": False, "reason": "", "regions": []}


def detect_blocking_watermark(data: bytes, meta_text: str = "") -> dict:
    meta = (meta_text or "").lower()
    source_hits = [word for word in BLOCKED_WATERMARK_SOURCES if word.lower() in meta]
    if source_hits:
        return {"rejected": True, "reason": f"低质量水印来源：{source_hits[0]}", "regions": []}
    if Image is None:
        return {"rejected": False, "reason": "", "regions": []}
    try:
        image = Image.open(BytesIO(data))
    except Exception:
        return {"rejected": False, "reason": "", "regions": []}
    return detect_blocking_watermark_from_image(image, meta_text)


def remove_watermark_if_present(path: Path) -> dict:
    if Image is None or ImageFilter is None or np is None or not path.exists():
        return {"watermark_detected": False, "watermark_removed": False, "regions": []}
    try:
        image = Image.open(path).convert("RGB")
    except Exception:
        return {"watermark_detected": False, "watermark_removed": False, "regions": []}

    width, height = image.size
    regions = [
        (0, int(height * 0.80), int(width * 0.42), height),
        (int(width * 0.58), int(height * 0.80), width, height),
        (0, 0, int(width * 0.38), int(height * 0.16)),
        (int(width * 0.62), 0, width, int(height * 0.16)),
    ]
    detected = [box for box in regions if _region_has_watermark(image, box)]
    if not detected:
        return {"watermark_detected": False, "watermark_removed": False, "regions": []}

    cleaned = image.copy()
    for box in detected:
        region = cleaned.crop(box)
        cleaned.paste(region.filter(ImageFilter.GaussianBlur(radius=max(8, min(width, height) // 80))), box)
    cleaned.save(path)
    return {
        "watermark_detected": True,
        "watermark_removed": True,
        "regions": [list(box) for box in detected],
    }
