from __future__ import annotations

import math
from pathlib import Path

from services.generation_service import (
    alignment_text,
    marker_start_sec,
    marker_time_by_weight,
    strip_product_marker,
)

MIN_ITEM_DURATION_SEC = 0.5
MAX_ITEM_DURATION_SEC = 30.0
MAX_ITEMS = 20
MIN_PIP_WIDTH = 0.12
MIN_PIP_HEIGHT = 0.12
DEFAULT_TRANSITION_SEC = 0.5
DEFAULT_FADE_SEC = 0.3
DEFAULT_PIP_BOX = {"x": 0.62, "y": 0.31, "w": 0.34, "h": 0.22}


def default_product_placement() -> dict:
    return {
        "enabled": False,
        "items": [],
        "transition_sec": DEFAULT_TRANSITION_SEC,
        "fade_sec": DEFAULT_FADE_SEC,
        "pip_box": dict(DEFAULT_PIP_BOX),
        "start_sec": None,
        "marker_present": False,
        "marker_offset": -1,
        "start_sec_stale": False,
        "updated_at": "",
    }


def normalize_pip_box(value: dict | None) -> dict:
    raw = value if isinstance(value, dict) else {}

    def number(key: str, fallback: float) -> float:
        try:
            parsed = float(raw.get(key, fallback))
        except (TypeError, ValueError):
            return fallback
        return parsed if math.isfinite(parsed) else fallback

    x = max(0.0, min(number("x", DEFAULT_PIP_BOX["x"]), 1.0 - MIN_PIP_WIDTH))
    y = max(0.0, min(number("y", DEFAULT_PIP_BOX["y"]), 1.0 - MIN_PIP_HEIGHT))
    width = max(MIN_PIP_WIDTH, min(number("w", DEFAULT_PIP_BOX["w"]), 1.0 - x))
    height = max(MIN_PIP_HEIGHT, min(number("h", DEFAULT_PIP_BOX["h"]), 1.0 - y))
    return {
        "x": round(x, 4),
        "y": round(y, 4),
        "w": round(width, 4),
        "h": round(height, 4),
    }


def recompute_marker(project: dict) -> dict:
    raw = str(project.get("raw_script") or "")
    clean, marker_present, marker_offset = strip_product_marker(raw)
    try:
        duration = float(project.get("audio_duration_sec") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    stored_narration = str(project.get("narration_script") or "")
    stale = bool(stored_narration) and alignment_text(stored_narration) != alignment_text(clean)
    if not marker_present:
        return {
            "marker_present": False,
            "marker_offset": -1,
            "start_sec": None,
            "start_sec_stale": False,
        }
    if duration <= 0:
        return {
            "marker_present": True,
            "marker_offset": marker_offset,
            "start_sec": None,
            "start_sec_stale": stale,
        }
    timings = project.get("narration_subtitle_timings") or []
    if stale:
        start_sec = marker_time_by_weight(clean, marker_offset, duration)
    else:
        start_sec = marker_start_sec(clean, marker_offset, duration, timings)
    return {
        "marker_present": True,
        "marker_offset": marker_offset,
        "start_sec": round(min(max(float(start_sec or 0), 0.0), duration), 3),
        "start_sec_stale": stale,
    }


def normalize_item_timing(asset: dict, item: dict) -> dict:
    mode = item.get("duration_mode", "custom")
    allowed = ("custom", "original") if asset.get("media_type") == "video" else ("custom", "remaining")
    if mode not in allowed:
        mode = "custom"
    try:
        duration = float(item.get("duration_sec") or MIN_ITEM_DURATION_SEC)
    except (TypeError, ValueError):
        duration = MIN_ITEM_DURATION_SEC
    if not math.isfinite(duration):
        duration = MIN_ITEM_DURATION_SEC
    duration = max(MIN_ITEM_DURATION_SEC, min(duration, MAX_ITEM_DURATION_SEC))
    source_duration = float(asset.get("duration_sec") or 0)
    if asset.get("media_type") == "video" and source_duration > 0:
        duration = source_duration if mode == "original" else min(duration, source_duration)
    return {"duration_mode": mode, "duration_sec": round(duration, 3), "fill_duration": False}


def normalize_product_placement(
    db: dict,
    project: dict,
    items: list[dict],
    transition_sec: float,
    pip_box: dict,
) -> dict:
    if len(items) > MAX_ITEMS:
        raise ValueError(f"带货素材最多 {MAX_ITEMS} 个")
    assets = {item.get("id"): item for item in db.get("product_library", [])}
    normalized_items = []
    for index, item in enumerate(items):
        asset = assets.get(str(item.get("asset_id") or ""))
        if not asset or not Path(str(asset.get("local_path") or "")).is_file():
            raise ValueError(f"第 {index + 1} 个带货素材不存在，请重新上传")
        normalized_items.append({
            "asset_id": asset["id"],
            **normalize_item_timing(asset, item),
            "pip_box": normalize_pip_box(item.get("pip_box") or pip_box),
        })

    placement = default_product_placement()
    placement.update({
        "items": normalized_items,
        "transition_sec": round(max(0.0, min(float(transition_sec or 0), 2.0)), 3),
        "pip_box": normalize_pip_box(pip_box),
    })
    placement.update(recompute_marker(project))
    placement["enabled"] = bool(
        placement["marker_present"]
        and placement["start_sec"] is not None
        and normalized_items
    )
    placement["fade_sec"] = DEFAULT_FADE_SEC
    return placement


def resolve_product_overlays(db: dict, project: dict, duration: float) -> tuple[list[dict], dict]:
    placement = default_product_placement()
    placement.update(project.get("product_placement") or {})
    marker = recompute_marker(project)
    transition_sec = max(0.0, min(float(placement.get("transition_sec") or 0), 2.0))
    pip_box = normalize_pip_box(placement.get("pip_box"))
    report = {
        **placement,
        **marker,
        "transition_sec": transition_sec,
        "fade_sec": DEFAULT_FADE_SEC,
        "pip_box": pip_box,
        "item_count": 0,
        "end_sec": None,
        "truncated": False,
    }
    if not marker["marker_present"] or marker["start_sec"] is None:
        report["enabled"] = False
        return [], report
    if not placement.get("items") or duration <= 0:
        report["enabled"] = False
        return [], report

    assets = {item.get("id"): item for item in db.get("product_library", [])}
    cursor = min(max(float(marker["start_sec"]), 0.0), float(duration))
    overlays = []
    truncated = False
    for index, item in enumerate(placement.get("items") or []):
        remaining = float(duration) - cursor
        if remaining <= 1e-6:
            truncated = True
            break
        asset = assets.get(str(item.get("asset_id") or ""))
        if not asset or not Path(str(asset.get("local_path") or "")).is_file():
            raise ValueError(f"第 {index + 1} 个带货素材文件不存在，请到素材库重新选择")
        timing = normalize_item_timing(asset, item)
        wanted = remaining if timing["duration_mode"] == "remaining" else timing["duration_sec"]
        requested = wanted
        source_duration = float(asset.get("duration_sec") or 0)
        effective = min(wanted, remaining)
        if effective < wanted - 1e-6:
            truncated = True
        overlays.append({
            "asset_id": asset["id"],
            "path": str(Path(str(asset["local_path"])).resolve()),
            "media_type": asset.get("media_type") or "video",
            "duration_sec": round(effective, 3),
            "requested_duration_sec": round(requested, 3),
            "fill_duration": False,
            "duration_mode": timing["duration_mode"],
            "source_duration_sec": round(source_duration, 3),
            "pip_box": normalize_pip_box(item.get("pip_box") or pip_box),
        })
        cursor += effective
        if timing["duration_mode"] == "remaining":
            report["skipped_after_fill"] = len(placement["items"]) - index - 1
            break

    report.update({
        "enabled": bool(overlays),
        "items": overlays,
        "item_count": len(overlays),
        "end_sec": round(cursor, 3),
        "truncated": truncated,
    })
    return overlays, report
