from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from services.asset_service import analyze_asset, new_id, safe_storage_name
from services.r2_storage import R2StorageError, delete_asset_object, r2_enabled, upload_asset, upload_asset_metadata
from services.store import ASSETS_DIR, load_db, public_url, save_db


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _run(args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=True,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )


def probe_video(path: Path) -> dict[str, Any]:
    result = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    payload = json.loads(result.stdout)
    video = next((item for item in payload.get("streams", []) if item.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("文件中没有可用的视频流")
    duration = float(payload.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise ValueError("无法读取视频时长")
    return {
        "duration": round(duration, 3),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "codec": str(video.get("codec_name") or ""),
        "file_size": path.stat().st_size,
    }


def detect_scene_times(path: Path, duration: float) -> list[float]:
    threshold = min(1.0, max(0.05, float(os.getenv("VIDEO_SCENE_THRESHOLD", "0.32"))))
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-i", str(path),
            "-vf", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    times = [float(value) for value in re.findall(r"pts_time:([0-9.]+)", result.stderr)]
    return sorted({round(value, 3) for value in times if 0.5 < value < duration - 0.5})


def build_clip_ranges(duration: float, scene_times: list[float]) -> list[tuple[float, float]]:
    min_seconds = max(1.0, float(os.getenv("VIDEO_CLIP_MIN_SECONDS", "3")))
    max_seconds = max(min_seconds, float(os.getenv("VIDEO_CLIP_MAX_SECONDS", "15")))
    max_clips = max(1, int(os.getenv("VIDEO_MAX_CLIPS", "60")))
    boundaries = [0.0, *scene_times, duration]
    ranges: list[tuple[float, float]] = []
    start = boundaries[0]
    for boundary in boundaries[1:]:
        if boundary - start < min_seconds and boundary < duration:
            continue
        while boundary - start > max_seconds:
            ranges.append((start, start + max_seconds))
            start += max_seconds
        if boundary - start >= min_seconds or boundary >= duration:
            ranges.append((start, boundary))
            start = boundary
        if len(ranges) >= max_clips:
            break
    if not ranges:
        ranges = [(0.0, min(duration, max_seconds))]
    return [(round(a, 3), round(b, 3)) for a, b in ranges[:max_clips] if b - a >= 0.5]


def clip_ranges_for_mode(duration: float, scene_times: list[float], mode: str) -> list[tuple[float, float]]:
    if mode == "full":
        return [(0.0, round(duration, 3))]
    return build_clip_ranges(duration, scene_times)


def _make_clip(source: Path, target: Path, start: float, end: float) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(start), "-i", str(source), "-t", str(end - start),
        "-vf", "scale='min(1280,iw)':-2,fps=25", "-c:v", "libx264",
        "-preset", "fast", "-crf", os.getenv("VIDEO_PROXY_CRF", "27"),
        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(target),
    ])


def _make_contact_sheet(clip: Path, target: Path, duration: float) -> None:
    frames: list[Path] = []
    for index, ratio in enumerate((0.2, 0.5, 0.8), start=1):
        frame = target.with_name(f"{target.stem}_{index}.jpg")
        _run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss",
            str(max(0.0, duration * ratio)), "-i", str(clip), "-frames:v", "1",
            "-vf", "scale=480:-2", str(frame),
        ])
        frames.append(frame)
    images = [Image.open(frame).convert("RGB") for frame in frames]
    width = max(image.width for image in images)
    height = max(image.height for image in images)
    sheet = Image.new("RGB", (width * len(images), height), "black")
    for index, image in enumerate(images):
        sheet.paste(image, (index * width, 0))
    sheet.save(target, "WEBP", quality=78, method=4)
    for image in images:
        image.close()
    for frame in frames:
        frame.unlink(missing_ok=True)


def _set_parent_status(asset_id: str, status: str, **fields: Any) -> None:
    db = load_db()
    asset = next((item for item in db.get("assets", []) if item.get("id") == asset_id), None)
    if not asset:
        return
    asset.update(fields)
    asset["analysis_status"] = status
    asset["updated_at"] = _now()
    save_db(db)


def process_video_asset(asset_id: str, manual_tags: dict | None = None) -> None:
    db = load_db()
    parent = next((item for item in db.get("assets", []) if item.get("id") == asset_id), None)
    if not parent:
        return
    source = Path(str(parent.get("local_path") or ""))
    created: list[dict[str, Any]] = []
    try:
        _set_parent_status(asset_id, "probing", processing_stage="probing", processing_progress=5)
        metadata = probe_video(source)
        max_duration = max(1, int(os.getenv("VIDEO_MAX_DURATION_SECONDS", "3600")))
        if metadata["duration"] > max_duration:
            raise ValueError(f"视频超过最大允许时长 {max_duration} 秒")
        _set_parent_status(asset_id, "splitting", processing_stage="splitting", processing_progress=15, **metadata)
        processing_mode = str(parent.get("video_processing_mode") or "split")
        scene_times = detect_scene_times(source, metadata["duration"]) if processing_mode == "split" else []
        ranges = clip_ranges_for_mode(metadata["duration"], scene_times, processing_mode)
        for index, (start, end) in enumerate(ranges, start=1):
            clip_id = new_id()
            suffix = f"片段-{index:03d}" if processing_mode == "split" else "完整素材"
            clip_name = safe_storage_name(f"{Path(parent['file_name']).stem}-{suffix}.mp4")
            clip_path = ASSETS_DIR / f"{clip_id}_{clip_name}"
            thumb_path = ASSETS_DIR / f"{clip_id}_thumb.webp"
            _make_clip(source, clip_path, start, end)
            _make_contact_sheet(clip_path, thumb_path, end - start)
            # Do not feed the source video's name into the fallback or prompt:
            # it can bias every clip toward the same label.
            tags = analyze_asset(thumb_path.name, thumb_path, "image")
            for key in ("object", "scene", "keywords"):
                manual = (manual_tags or {}).get(key) or []
                tags[key] = list(dict.fromkeys([*manual, *(tags.get(key) or [])]))[:12]
            now = _now()
            clip = {
                "id": clip_id, "library_id": parent.get("library_id"),
                "parent_asset_id": asset_id, "file_name": clip_name,
                "original_path": parent.get("original_path"), "file_type": "video",
                "file_url": public_url(clip_path), "thumbnail_url": public_url(thumb_path),
                "local_path": str(clip_path), "thumbnail_path": str(thumb_path),
                "file_size": clip_path.stat().st_size,
                "hash": hashlib.sha256(clip_path.read_bytes()).hexdigest(),
                "start_ms": round(start * 1000), "end_ms": round(end * 1000),
                "duration_ms": round((end - start) * 1000), "width": 1280,
                "height": round(metadata["height"] * 1280 / metadata["width"]) if metadata["width"] > 1280 else metadata["height"],
                "storage_tier": "proxy", "video_processing_mode": processing_mode,
                "object": tags.get("object", []),
                "scene": tags.get("scene", []), "keywords": tags.get("keywords", []),
                "media_type": "video",
                "analysis_status": "failed" if tags.get("analysis_error") else "ready",
                "analysis_provider": tags.get("analysis_provider", "local_fallback"),
                "analysis_error": tags.get("analysis_error", ""),
                "source_page": parent.get("source_page", ""), "source_note": parent.get("source_note", ""),
                "copyright_note": parent.get("copyright_note", ""),
                "is_available": not bool(tags.get("analysis_error")),
                "asset_source": "library_upload", "created_at": now, "updated_at": now,
            }
            try:
                upload_asset(clip, clip_path)
            except R2StorageError:
                raise
            if r2_enabled():
                clip_path.unlink(missing_ok=True)
            created.append(clip)
            progress = 20 + round(index / len(ranges) * 70)
            _set_parent_status(asset_id, "analyzing", processing_stage="analyzing", processing_progress=progress)
        db = load_db()
        current = next((item for item in db.get("assets", []) if item.get("id") == asset_id), None)
        if not current:
            return
        db["assets"].extend(created)
        current.update({
            **metadata, "analysis_status": "ready", "processing_stage": "ready",
            "processing_progress": 100, "clip_count": len(created), "is_available": False,
            "analysis_provider": "ffmpeg+minimax", "updated_at": _now(),
        })
        if not current.get("keep_original", False):
            source.unlink(missing_ok=True)
            current["original_deleted"] = True
            current["local_path"] = ""
            current["file_url"] = ""
        try:
            upload_asset_metadata(current)
        except R2StorageError:
            pass
        save_db(db)
    except Exception as exc:
        for clip in created:
            try:
                delete_asset_object(clip)
            except R2StorageError:
                pass
            for field in ("local_path", "thumbnail_path"):
                Path(str(clip.get(field) or "")).unlink(missing_ok=True)
        _set_parent_status(
            asset_id, "failed", processing_stage="failed", processing_progress=100,
            analysis_error=str(exc)[:500],
        )


def reanalyze_video_clip(asset_id: str) -> None:
    db = load_db()
    clip = next((item for item in db.get("assets", []) if item.get("id") == asset_id), None)
    if not clip or not clip.get("parent_asset_id"):
        return
    thumbnail = Path(str(clip.get("thumbnail_path") or ""))
    if not thumbnail.is_file():
        _set_parent_status(
            asset_id, "failed", analysis_error="视频片段缺少关键帧缩略图",
            is_available=False,
        )
        return
    tags = analyze_asset(thumbnail.name, thumbnail, "image")
    db = load_db()
    clip = next((item for item in db.get("assets", []) if item.get("id") == asset_id), None)
    if not clip:
        return
    clip.update({key: tags.get(key, []) for key in ("object", "scene", "keywords")})
    clip["analysis_provider"] = tags.get("analysis_provider", "local_fallback")
    clip["analysis_error"] = tags.get("analysis_error", "")
    clip["analysis_status"] = "failed" if tags.get("analysis_error") else "ready"
    clip["is_available"] = not bool(tags.get("analysis_error"))
    clip["updated_at"] = _now()
    try:
        upload_asset_metadata(clip)
    except R2StorageError:
        pass
    save_db(db)
