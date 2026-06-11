from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from PIL import Image, ImageOps
from pydantic import BaseModel

from services.asset_service import new_id
from services.generation_service import (
    generate_doubao_image,
    generate_export_srt,
    generate_seedream_cover,
    overlay_title_on_cover,
    remove_watermark_with_seedream,
    synthesize_project_voice,
    write_timeline,
)
from services.store import load_db, project_dir, public_url, save_db
from services.text_service import generate_viral_title

router = APIRouter(prefix="/api/projects", tags=["generation"])


class VoicePayload(BaseModel):
    voice_type: str | None = None
    speech_rate: int | None = None


class CoverPayload(BaseModel):
    title: str
    subtitle: str = ""


def _generated_image(db: dict, project_id: str, asset_id: str) -> tuple[dict, Path]:
    asset = next(
        (
            item for item in db.get("generated_assets", [])
            if item.get("id") == asset_id and item.get("project_id") == project_id
        ),
        None,
    )
    if not asset:
        raise HTTPException(404, "Generated image not found")
    path = Path(str(asset.get("local_path") or "")).resolve()
    project_root = project_dir(project_id).resolve()
    if not path.exists() or project_root not in path.parents:
        raise HTTPException(404, "Generated image file not found")
    return asset, path


def _refresh_image_metadata(asset: dict, path: Path, operation: str) -> None:
    with Image.open(path) as image:
        asset["width"], asset["height"] = image.size
    asset["file_size"] = path.stat().st_size
    asset["hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
    asset["updated_at"] = datetime.now().isoformat(timespec="seconds")
    asset.setdefault("image_operations", []).append({
        "operation": operation,
        "created_at": asset["updated_at"],
    })


def _normalize_image_format(path: Path) -> None:
    with Image.open(path) as source:
        image = source.convert("RGBA" if path.suffix.lower() == ".png" else "RGB")
        image.load()
    save_format = "PNG" if path.suffix.lower() == ".png" else "JPEG"
    save_options = {"quality": 95} if save_format == "JPEG" else {}
    image.save(path, format=save_format, **save_options)


def _crop_square(path: Path) -> None:
    with Image.open(path) as source:
        suffix = path.suffix.lower()
        image = ImageOps.exif_transpose(source).convert("RGBA" if suffix == ".png" else "RGB")
        width, height = image.size
        side = min(width, height)
        left = (width - side) // 2
        top = (height - side) // 2
        cropped = image.crop((left, top, left + side, top + side))
        save_format = {
            ".png": "PNG",
            ".webp": "WEBP",
        }.get(suffix, "JPEG")
        save_options = {"quality": 95} if save_format in {"JPEG", "WEBP"} else {}
        cropped.save(path, format=save_format, **save_options)


@router.post("/{project_id}/generated-assets/{asset_id}/crop-square")
def crop_generated_image_square(project_id: str, asset_id: str):
    db = load_db()
    asset, path = _generated_image(db, project_id, asset_id)
    try:
        _crop_square(path)
    except Exception as exc:
        raise HTTPException(500, f"Crop image failed: {exc}") from exc
    _refresh_image_metadata(asset, path, "crop_square")
    save_db(db)
    return {"status": "success", "asset": asset}


@router.post("/{project_id}/crop-selected-images")
def crop_selected_images(project_id: str):
    db = load_db()
    if not any(item.get("id") == project_id for item in db.get("projects", [])):
        raise HTTPException(404, "Project not found")
    shots = sorted(
        [item for item in db.get("shots", []) if item.get("project_id") == project_id],
        key=lambda item: item.get("shot_index", 0),
    )
    cropped = 0
    skipped = 0
    failed: list[dict] = []
    for shot in shots:
        selected_id = shot.get("selected_asset_id")
        if not selected_id:
            skipped += 1
            continue
        asset = next(
            (
                item for item in db.get("generated_assets", [])
                if item.get("id") == selected_id and item.get("project_id") == project_id
            ),
            None,
        )
        if not asset:
            library_asset = next((item for item in db.get("assets", []) if item.get("id") == selected_id), None)
            source = Path(str((library_asset or {}).get("local_path") or ""))
            if not library_asset or not source.exists():
                skipped += 1
                continue
            asset_id = new_id()
            suffix = source.suffix.lower() if source.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
            target_dir = project_dir(project_id) / "images" / f"shot_{shot['shot_index']:03d}"
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / f"library_crop_{asset_id}{suffix}"
            shutil.copy2(source, path)
            now = datetime.now().isoformat(timespec="seconds")
            asset = {
                "id": asset_id,
                "project_id": project_id,
                "shot_id": shot["id"],
                "type": "image",
                "file_type": "image",
                "file_name": path.name,
                "asset_source": "library_crop",
                "provider": "local_library",
                "source_asset_id": library_asset["id"],
                "file_url": public_url(path),
                "local_path": str(path),
                "status": "success",
                "created_at": now,
            }
            db.setdefault("generated_assets", []).append(asset)
            shot["selected_asset_id"] = asset_id
            shot["asset_source"] = "library_crop"
            selected_id = asset_id
            for candidate in db.get("project_assets", []):
                if candidate.get("project_id") == project_id and candidate.get("shot_id") == shot["id"]:
                    candidate["is_selected"] = False
            db.setdefault("project_assets", []).append({
                "project_id": project_id,
                "shot_id": shot["id"],
                "asset_id": asset_id,
                "asset_source": "library_crop",
                "match_score": shot.get("match_score", 100),
                "match_reason": "素材库图片项目裁剪副本",
                "is_selected": True,
                "created_at": now,
            })
        path = Path(str(asset.get("local_path") or ""))
        try:
            _crop_square(path)
            _refresh_image_metadata(asset, path, "crop_square")
            cropped += 1
        except Exception as exc:
            failed.append({"shot_index": shot.get("shot_index"), "error": str(exc)[:200]})
    save_db(db)
    return {"status": "success", "cropped": cropped, "skipped": skipped, "failed": failed}


@router.post("/{project_id}/generated-assets/{asset_id}/remove-watermark")
def remove_generated_image_watermark(project_id: str, asset_id: str):
    db = load_db()
    asset, path = _generated_image(db, project_id, asset_id)
    shot = next(
        (
            item for item in db.get("shots", [])
            if item.get("id") == asset.get("shot_id") and item.get("project_id") == project_id
        ),
        None,
    )
    try:
        result = remove_watermark_with_seedream(path, shot)
        _normalize_image_format(path)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    asset["watermark"] = result
    asset["watermark_removed"] = True
    asset["watermark_provider"] = result.get("provider")
    asset["watermark_model"] = result.get("model")
    _refresh_image_metadata(asset, path, "remove_watermark")
    for item in db.get("project_assets", []):
        if item.get("asset_id") == asset_id:
            item["watermark"] = result
    save_db(db)
    return {"status": "success", "asset": asset, "watermark": result}


@router.post("/{project_id}/shots/{shot_id}/generate-image")
def generate_image(project_id: str, shot_id: str):
    db = load_db()
    shot = next((s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id), None)
    if not shot:
        raise HTTPException(404, "Shot not found")
    out = project_dir(project_id) / "images" / f"shot_{shot['shot_index']:03d}.png"
    try:
        result = generate_doubao_image(out, shot, "1:1")
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    prompt = result["prompt"]
    generated_id = new_id()
    db["generated_assets"].append({
        "id": generated_id,
        "project_id": project_id,
        "shot_id": shot_id,
        "type": "image",
        "prompt": prompt,
        "provider": result.get("provider"),
        "model": result.get("model"),
        "image_size": result.get("image_size"),
        "remote_url": result.get("remote_url"),
        "seed": result.get("seed"),
        "file_url": public_url(out),
        "local_path": str(out),
        "status": "success",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
    shot["selected_asset_id"] = generated_id
    shot["asset_source"] = "ai_generated"
    shot["status"] = "ai_generated"
    save_db(db)
    return {
        "shot_id": shot_id,
        "image_url": public_url(out),
        "prompt": prompt,
        "provider": result.get("provider"),
        "model": result.get("model"),
        "image_size": result.get("image_size"),
        "status": "success",
    }


@router.post("/{project_id}/generate-voice")
def generate_voice(project_id: str, payload: VoicePayload | None = None):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    shots = sorted([s for s in db["shots"] if s["project_id"] == project_id], key=lambda s: s["shot_index"])
    if not shots:
        raise HTTPException(400, "No shots")
    out = project_dir(project_id) / "audio" / "main_voice.mp3"
    try:
        result = synthesize_project_voice(out, shots, payload.voice_type if payload else None, payload.speech_rate if payload else None)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    timing_by_shot = {
        timing["shot_id"]: timing
        for timing in result.get("shot_timings", [])
        if timing.get("shot_id")
    }
    for shot in shots:
        timing = timing_by_shot.get(shot["id"])
        if not timing:
            continue
        shot["start_time"] = round(timing["start_time"], 3)
        shot["end_time"] = round(timing["end_time"], 3)
        shot["duration_sec"] = round(timing["duration_sec"], 3)
        shot["subtitle_timings"] = timing.get("subtitle_timings", [])
    voice_timeline_path = project_dir(project_id) / "audio" / "voice_timeline.json"
    voice_timeline_path.write_text(
        json.dumps(result.get("shot_timings", []), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    project["voice_style"] = result["voice_type"]
    project["audio_url"] = public_url(out)
    project["voice_timeline_url"] = public_url(voice_timeline_path)
    project["audio_format"] = result["audio_format"]
    project["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return {
        "audio_url": public_url(out),
        "audio_format": result["audio_format"],
        "voice_type": result["voice_type"],
        "provider": result["provider"],
        "duration_sec": result["duration_sec"],
        "voice_timeline_url": public_url(voice_timeline_path),
        "alignment_method": result.get("alignment_method"),
        "timestamp_provider": result.get("timestamp_provider"),
        "long_text_error": result.get("long_text_error"),
    }


@router.post("/{project_id}/generate-title")
def generate_title(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")

    script = str(project.get("rewritten_script") or project.get("raw_script") or "").strip()
    if not script:
        raise HTTPException(400, "No script content available for title generation")

    result = generate_viral_title(script)
    if result.get("error"):
        raise HTTPException(502, f"Title generation failed: {result['error']}")

    now = datetime.now().isoformat(timespec="seconds")
    project["title_line1"] = result["line1"]
    project["title_line2"] = result["line2"]
    project["title_full"] = result["full_title"]
    project["updated_at"] = now
    save_db(db)

    return {
        "status": "success",
        "line1": result["line1"],
        "line2": result["line2"],
        "full_title": result["full_title"],
    }


@router.post("/{project_id}/generate-cover")
def generate_cover(project_id: str, payload: CoverPayload):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    if not payload.title.strip():
        raise HTTPException(400, "Cover title is required")

    cover_path = project_dir(project_id) / "cover" / "cover.png"
    try:
        # When two-line title exists, skip AI text rendering and use Pillow overlay instead
        title_line1 = project.get("title_line1", "")
        title_line2 = project.get("title_line2", "")
        has_two_line_title = bool(title_line1 and title_line2)

        result = generate_seedream_cover(
            cover_path,
            project,
            payload.title,
            payload.subtitle,
            skip_text=has_two_line_title,
        )
        _normalize_image_format(cover_path)

        # Overlay title text on the cover image if two-line title exists
        if has_two_line_title:
            overlay_title_on_cover(cover_path, title_line1, title_line2)

    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc

    now = datetime.now().isoformat(timespec="seconds")
    project.update({
        "cover_url": public_url(cover_path),
        "cover_title": result["title"],
        "cover_subtitle": result["subtitle"],
        "cover_prompt": result["prompt"],
        "cover_provider": result["provider"],
        "cover_model": result["model"],
        "cover_updated_at": now,
        "updated_at": now,
    })
    save_db(db)
    return {
        "status": "success",
        "cover_url": project["cover_url"],
        "title": project["cover_title"],
        "subtitle": project["cover_subtitle"],
        "provider": result["provider"],
        "model": result["model"],
        "image_size": result["image_size"],
    }


@router.post("/{project_id}/generate-subtitles")
def generate_subtitles(project_id: str):
    db = load_db()
    shots = sorted([s for s in db["shots"] if s["project_id"] == project_id], key=lambda s: s["shot_index"])
    if not shots:
        raise HTTPException(400, "No shots")
    srt_path = project_dir(project_id) / "subtitles" / "subtitles.srt"
    srt_path.write_text(generate_export_srt(shots), encoding="utf-8")
    timeline_path = project_dir(project_id) / "subtitles" / "timeline.json"
    write_timeline(timeline_path, shots)
    return {"subtitle_url": public_url(srt_path), "timeline_url": public_url(timeline_path)}
