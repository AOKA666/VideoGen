from __future__ import annotations

import hashlib
import json
import shutil
from io import BytesIO
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, ImageOps
from pydantic import BaseModel

from services.asset_service import new_id
from services.r2_storage import ensure_asset_local
from services.generation_service import (
    align_lyrics,
    align_lyrics_to_shots,
    build_image_prompt,
    compose_uploaded_cover,
    convert_music_to_main_voice,
    expected_lyrics_from_shots,
    generate_doubao_image,
    generate_export_srt,
    lrc_from_lines,
    remove_watermark_with_seedream,
    synthesize_project_voice,
    weighted_music_timeline_from_shots,
    write_timeline,
)
from services.store import load_db, project_dir, public_url, save_db
from services.text_service import generate_publish_assistant, generate_viral_title

router = APIRouter(prefix="/api/projects", tags=["generation"])


class VoicePayload(BaseModel):
    voice_type: str | None = None
    speech_rate: int | None = None


class ImageGenerationPayload(BaseModel):
    prompt: str | None = None


class SquareCropPayload(BaseModel):
    x: float
    y: float
    size: float


class MusicSettingsPayload(BaseModel):
    music_id: str | None = None
    start_sec: float = 0
    volume: float = 0.2


class MusicVoicePayload(BaseModel):
    music_id: str
    start_sec: float | None = None


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
    if operation in {"crop_square", "crop_square_region"}:
        asset.pop("crop_region", None)
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


def _crop_square_region(path: Path, x: float, y: float, size: float) -> None:
    with Image.open(path) as source:
        suffix = path.suffix.lower()
        image = ImageOps.exif_transpose(source).convert("RGBA" if suffix == ".png" else "RGB")
        width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError("Image has invalid dimensions")
        side = max(1, min(int(round(size)), width, height))
        left = max(0, min(int(round(x)), width - side))
        top = max(0, min(int(round(y)), height - side))
        cropped = image.crop((left, top, left + side, top + side))
        save_format = {
            ".png": "PNG",
            ".webp": "WEBP",
        }.get(suffix, "JPEG")
        save_options = {"quality": 95} if save_format in {"JPEG", "WEBP"} else {}
        cropped.save(path, format=save_format, **save_options)


def _read_crop_region(path: Path, x: float, y: float, size: float) -> dict:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("Image has invalid dimensions")
    side = max(1, min(float(size), float(width), float(height)))
    left = max(0.0, min(float(x), width - side))
    top = max(0.0, min(float(y), height - side))
    return {
        "x": round(left, 3),
        "y": round(top, 3),
        "size": round(side, 3),
        "image_width": width,
        "image_height": height,
    }


@router.get("/{project_id}/generated-assets/{asset_id}/download-png")
def download_generated_image_png(project_id: str, asset_id: str):
    db = load_db()
    asset, path = _generated_image(db, project_id, asset_id)
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            image.load()
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            output = BytesIO()
            image.save(output, format="PNG", optimize=True)
            output.seek(0)
    except Exception as exc:
        raise HTTPException(500, f"PNG conversion failed: {exc}") from exc

    filename = f"{Path(str(asset.get('file_name') or path.name)).stem}.png"
    return StreamingResponse(
        output,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


@router.post("/{project_id}/generated-assets/{asset_id}/crop-square-region")
def crop_generated_image_square_region(project_id: str, asset_id: str, payload: SquareCropPayload):
    db = load_db()
    asset, path = _generated_image(db, project_id, asset_id)
    try:
        crop_region = _read_crop_region(path, payload.x, payload.y, payload.size)
    except Exception as exc:
        raise HTTPException(500, f"Save image display region failed: {exc}") from exc
    asset["crop_region"] = crop_region
    now = datetime.now().isoformat(timespec="seconds")
    asset["updated_at"] = now
    asset.setdefault("image_operations", []).append({
        "operation": "display_region",
        "created_at": now,
    })
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
            source = ensure_asset_local(library_asset) if library_asset else Path()
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


@router.get("/{project_id}/shots/{shot_id}/image-prompt")
def get_image_prompt(project_id: str, shot_id: str):
    db = load_db()
    shot = next((s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id), None)
    if not shot:
        raise HTTPException(404, "Shot not found")
    return {"shot_id": shot_id, "prompt": build_image_prompt(shot)}


@router.post("/{project_id}/shots/{shot_id}/generate-image")
def generate_image(project_id: str, shot_id: str, payload: ImageGenerationPayload | None = None):
    db = load_db()
    shot = next((s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id), None)
    if not shot:
        raise HTTPException(404, "Shot not found")
    out = project_dir(project_id) / "images" / f"shot_{shot['shot_index']:03d}.png"
    try:
        result = generate_doubao_image(out, shot, "1:1", payload.prompt if payload else None)
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
        "person_gender": result.get("person_gender"),
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
        "person_gender": result.get("person_gender"),
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


@router.post("/{project_id}/generate-publish-assistant")
def generate_publish(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")

    script = str(project.get("rewritten_script") or project.get("raw_script") or "").strip()
    if not script:
        raise HTTPException(400, "No script content available for publish assistant")

    result = generate_publish_assistant(script)
    if result.get("error"):
        raise HTTPException(502, f"Publish assistant generation failed: {result['error']}")

    now = datetime.now().isoformat(timespec="seconds")
    project["publish_short_title"] = result["short_title"]
    project["publish_description"] = result["description"]
    project["updated_at"] = now
    save_db(db)

    return {
        "status": "success",
        "short_title": result["short_title"],
        "description": result["description"],
    }


@router.post("/{project_id}/generate-cover")
def generate_cover(project_id: str, file: UploadFile = File(...)):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")

    title_line1 = str(project.get("title_line1") or "").strip()
    title_line2 = str(project.get("title_line2") or "").strip()
    if not title_line1 or not title_line2:
        raise HTTPException(400, "Please confirm the two-line title before generating the cover")
    if len(title_line1) > 9 or len(title_line2) > 9:
        raise HTTPException(400, "Each title line must not exceed 9 characters")
    if not str(file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Please upload an image file")

    cover_dir = project_dir(project_id) / "cover"
    cover_dir.mkdir(parents=True, exist_ok=True)
    source_path = cover_dir / "portrait-upload"
    cover_path = cover_dir / "cover.png"
    try:
        with source_path.open("wb") as target:
            shutil.copyfileobj(file.file, target)
        if source_path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("Uploaded image must not exceed 20 MB")
        compose_uploaded_cover(source_path, cover_path, title_line1, title_line2)
    except Exception as exc:
        source_path.unlink(missing_ok=True)
        raise HTTPException(400, f"Cover image processing failed: {exc}") from exc

    now = datetime.now().isoformat(timespec="seconds")
    project.update({
        "cover_url": public_url(cover_path),
        "cover_source_url": public_url(source_path),
        "cover_title": f"{title_line1} {title_line2}",
        "cover_subtitle": "",
        "cover_prompt": "",
        "cover_provider": "uploaded_image",
        "cover_model": "pillow_composite",
        "cover_updated_at": now,
        "updated_at": now,
    })
    save_db(db)
    return {
        "status": "success",
        "cover_url": project["cover_url"],
        "title": project["cover_title"],
        "subtitle": "",
        "provider": project["cover_provider"],
        "model": project["cover_model"],
        "image_size": "1080x1920",
    }


@router.get("/{project_id}/download-cover")
def download_cover(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    cover_path = project_dir(project_id) / "cover" / "cover.png"
    if not project.get("cover_url") or not cover_path.exists():
        raise HTTPException(404, "Cover image not found")
    return FileResponse(
        cover_path,
        media_type="image/png",
        filename="video-cover.png",
    )


@router.patch("/{project_id}/music-settings")
def update_music_settings(project_id: str, payload: MusicSettingsPayload):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    music = None
    if payload.music_id:
        music = next(
            (item for item in db.get("music_library", []) if item.get("id") == payload.music_id),
            None,
        )
        if not music or not Path(str(music.get("local_path") or "")).exists():
            raise HTTPException(404, "Music preset not found")

    duration = float((music or {}).get("duration_sec") or 0)
    start_sec = max(0.0, float(payload.start_sec or 0))
    if duration > 0:
        start_sec = min(start_sec, max(duration - 0.1, 0))
    volume = max(0.0, min(1.0, float(payload.volume)))
    now = datetime.now().isoformat(timespec="seconds")
    project.update({
        "background_music_id": music.get("id") if music else None,
        "background_music_name": music.get("name") if music else "",
        "background_music_url": music.get("file_url") if music else "",
        "background_music_start_sec": round(start_sec, 3),
        "background_music_volume": round(volume, 3),
        "updated_at": now,
    })
    save_db(db)
    return {
        "status": "success",
        "music": music,
        "start_sec": project["background_music_start_sec"],
        "volume": project["background_music_volume"],
    }


@router.post("/{project_id}/generate-music-voice")
def generate_music_voice(project_id: str, payload: MusicVoicePayload):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    shots = sorted([s for s in db["shots"] if s["project_id"] == project_id], key=lambda s: s["shot_index"])
    if not shots:
        raise HTTPException(400, "No shots")

    music = next(
        (item for item in db.get("music_library", []) if item.get("id") == payload.music_id),
        None,
    )
    if not music:
        raise HTTPException(404, "Music not found")
    source = Path(str(music.get("local_path") or ""))
    if not source.exists():
        raise HTTPException(404, "Music file not found")

    source_duration = float(music.get("duration_sec") or 0)
    start_sec = (
        float(payload.start_sec)
        if payload.start_sec is not None
        else float(project.get("background_music_start_sec") or 0)
    )
    start_sec = max(0.0, start_sec)
    if source_duration > 0:
        start_sec = min(start_sec, max(source_duration - 0.1, 0))

    audio_path = project_dir(project_id) / "audio" / "main_voice.mp3"
    lyrics_path = project_dir(project_id) / "subtitles" / "lyrics.lrc"
    timeline_path = project_dir(project_id) / "audio" / "voice_timeline.json"
    lyric_warning = None
    try:
        duration = convert_music_to_main_voice(source, audio_path, start_sec=start_sec)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    try:
        lyric_result = align_lyrics(audio_path, expected_lyrics_from_shots(shots), duration)
        shot_timings = align_lyrics_to_shots(
            lyric_result["lines"],
            shots,
            duration,
            source_start_sec=0,
        )
    except Exception as exc:
        lyric_warning = str(exc)
        shot_timings = weighted_music_timeline_from_shots(shots, duration)
        fallback_lines = []
        for timing in shot_timings:
            fallback_lines.extend(timing.get("subtitle_timings", []))
        lyric_result = {
            "provider": "duration_weighted_fallback",
            "model": "",
            "lines": fallback_lines,
            "lrc": lrc_from_lines(fallback_lines),
        }

    timing_by_shot = {
        timing["shot_id"]: timing
        for timing in shot_timings
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

    timeline_path.write_text(json.dumps(shot_timings, ensure_ascii=False, indent=2), encoding="utf-8")
    lyrics_path.write_text(lrc_from_lines(lyric_result["lines"]), encoding="utf-8")
    now = datetime.now().isoformat(timespec="seconds")
    project.update({
        "voice_style": "music",
        "audio_url": public_url(audio_path),
        "voice_timeline_url": public_url(timeline_path),
        "lyrics_lrc_url": public_url(lyrics_path),
        "audio_format": "mp3",
        "audio_provider": "music_upload",
        "music_voice_id": music.get("id"),
        "music_voice_name": music.get("name"),
        "music_voice_source_start_sec": round(start_sec, 3),
        "music_voice_lyric_provider": lyric_result.get("provider"),
        "music_voice_lyric_model": lyric_result.get("model"),
        "music_voice_lyric_warning": lyric_warning,
        "background_music_id": None,
        "background_music_name": "",
        "background_music_url": "",
        "background_music_start_sec": 0,
        "background_music_volume": 0.2,
        "updated_at": now,
    })
    save_db(db)
    return {
        "status": "success",
        "audio_url": project["audio_url"],
        "voice_timeline_url": project["voice_timeline_url"],
        "lyrics_lrc_url": project["lyrics_lrc_url"],
        "duration_sec": duration,
        "line_count": len(lyric_result["lines"]),
        "provider": lyric_result.get("provider"),
        "model": lyric_result.get("model"),
        "warning": lyric_warning,
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
