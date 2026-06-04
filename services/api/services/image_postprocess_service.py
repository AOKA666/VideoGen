from __future__ import annotations

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
