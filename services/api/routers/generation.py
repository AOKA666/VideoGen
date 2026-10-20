from __future__ import annotations

import json
import re
import shutil
import threading
from io import BytesIO
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from services.generation_service import (
    VIDEO_LIBRARY_SUBTITLE_MAX_HANZI,
    audio_duration,
    compose_uploaded_cover,
    generate_export_srt,
    marker_start_sec,
    narration_shot,
    strip_product_marker,
    synthesize_volcengine_tts,
    synthesize_project_voice,
    normalize_cover_title_positions,
)
from services.media_service import assemble_clips, normalize_audio
from services.store import assets_dir, db_write_transaction, load_db, project_dir, public_url, save_db
from services.text_service import generate_viral_title

router = APIRouter(prefix="/api/projects", tags=["generation"])
VIDEO_LIBRARY_VOICE_LOCK = threading.Lock()
VIDEO_LIBRARY_VOICE_ACTIVE: set[str] = set()


class VoicePayload(BaseModel):
    voice_type: str | None = None
    speech_rate: int | None = None
    audio_source: str | None = None


class VoiceSettingsPayload(BaseModel):
    volume: float = 1.0


def _first_script_sentence(text: str) -> str:
    compact = str(text or "").strip()
    if not compact:
        return ""
    match = re.search(r".+?[。！？!?；;](?:[”’\"']*)", compact, flags=re.S)
    return (match.group(0) if match else compact.splitlines()[0]).strip()[:160]


def require_video_library(project: dict) -> None:
    if project.get("workflow_type") != "video_library":
        raise HTTPException(400, "旧项目已停用，仅支持归档或删除")


class MusicSettingsPayload(BaseModel):
    music_id: str | None = None
    start_sec: float = 0
    volume: float = 0.2



def _generate_video_library_voice(project: dict, payload: VoicePayload | None) -> dict:
    project_id = project["id"]
    with VIDEO_LIBRARY_VOICE_LOCK:
        if project_id in VIDEO_LIBRARY_VOICE_ACTIVE:
            raise HTTPException(409, "配音正在生成中，请稍候")
        VIDEO_LIBRARY_VOICE_ACTIVE.add(project_id)
    try:
        return _generate_video_library_voice_unlocked(project, payload)
    finally:
        with VIDEO_LIBRARY_VOICE_LOCK:
            VIDEO_LIBRARY_VOICE_ACTIVE.discard(project_id)


def _generate_video_library_voice_unlocked(project: dict, payload: VoicePayload | None) -> dict:
    """Narrate the whole script in one voice track and match clips to its duration."""
    project_id = project["id"]
    raw_script = str(project.get("raw_script") or "").strip()
    script, marker_present, marker_offset = strip_product_marker(raw_script)
    script = script.strip()
    if not script:
        raise HTTPException(400, "请先输入文案")
    audio_source = (
        payload.audio_source if payload and payload.audio_source else project.get("audio_source")
    ) or "tts"
    if audio_source not in {"tts", "upload"}:
        raise HTTPException(400, "音频来源无效")
    voice_type = (
        payload.voice_type if payload and payload.voice_type else project.get("voice_type")
    ) or None
    speech_rate = (
        payload.speech_rate if payload and payload.speech_rate is not None
        else project.get("speech_rate")
    )
    assets = [
        asset for asset in load_db().get("video_library", [])
        if Path(asset.get("local_path") or "").is_file()
    ]
    if not assets:
        raise HTTPException(400, "请先到素材库上传视频")
    base = project_dir(project_id)
    out = base / "audio" / "main_voice.mp3"
    narration = {"id": "narration", "shot_index": 1, "voice_text": script}
    if audio_source == "upload":
        uploaded = Path(project.get("uploaded_audio_path") or "")
        if not uploaded.is_file():
            raise HTTPException(400, "请先上传音频")
        try:
            normalize_audio(uploaded, out)
            duration = audio_duration(out)
        except Exception as exc:
            raise HTTPException(502, f"音频处理失败：{exc}") from exc
        result = {
            "duration_sec": duration, "subtitle_timings": [], "audio_format": "mp3",
            "provider": "uploaded_audio", "voice_type": project.get("voice_type"),
            "timestamp_provider": "duration_weighted",
        }
    else:
        try:
            result = synthesize_project_voice(
                out, [narration], voice_type, speech_rate,
                max_hanzi=VIDEO_LIBRARY_SUBTITLE_MAX_HANZI,
            )
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc
    narration.update({
        "start_time": 0.0,
        "end_time": result["duration_sec"],
        "subtitle_timings": result.get("subtitle_timings") or [],
    })
    start_sec = marker_start_sec(
        script, marker_offset, result["duration_sec"], result.get("subtitle_timings") or [],
    )
    shots = assemble_clips(assets, result["duration_sec"], project_id)
    srt_path = base / "subtitles" / "subtitles.srt"
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(
        generate_export_srt([narration], max_hanzi=VIDEO_LIBRARY_SUBTITLE_MAX_HANZI),
        encoding="utf-8",
    )
    timeline_path = base / "audio" / "voice_timeline.json"
    timeline_path.write_text(json.dumps(narration, ensure_ascii=False, indent=2), encoding="utf-8")
    now = datetime.now().isoformat(timespec="seconds")
    with db_write_transaction():
        db = load_db()
        current = next((p for p in db["projects"] if p["id"] == project_id), None)
        if not current:
            raise HTTPException(404, "Project not found")
        db["shots"] = [s for s in db["shots"] if s["project_id"] != project_id] + shots
        placement = dict(current.get("product_placement") or {})
        placement.setdefault("items", [])
        placement.setdefault("transition_sec", 0.5)
        placement.setdefault("fade_sec", 0.3)
        placement.setdefault("pip_box", {"x": 0.62, "y": 0.31, "w": 0.34, "h": 0.22})
        placement.update({
            "marker_present": marker_present,
            "marker_offset": marker_offset,
            "start_sec": start_sec,
            "start_sec_stale": False,
            "enabled": bool(marker_present and start_sec is not None and placement["items"]),
            "updated_at": now,
        })
        current.update({
            "audio_url": public_url(out),
            "audio_path": str(out),
            "audio_duration_sec": round(float(result["duration_sec"]), 3),
            "audio_format": "mp3",
            "audio_source": audio_source,
            "voice_type": result.get("voice_type") or voice_type or current.get("voice_type"),
            "speech_rate": int(speech_rate) if speech_rate is not None else int(current.get("speech_rate") or 0),
            "voice_style": result.get("voice_type") or current.get("voice_type"),
            "voice_volume": float(current.get("voice_volume") or 1.0),
            "narration_script": script,
            "narration_subtitle_timings": narration["subtitle_timings"],
            "product_placement": placement,
            "voice_timeline_url": public_url(timeline_path),
            "subtitle_url": public_url(srt_path),
            "status": "voice_ready",
            "render_url": None,
            "generation_error": "",
            "updated_at": now,
        })
        save_db(db)
    return {
        "audio_url": public_url(out),
        "audio_format": "mp3",
        "voice_type": result.get("voice_type"),
        "provider": result.get("provider"),
        "duration_sec": result["duration_sec"],
        "voice_timeline_url": public_url(timeline_path),
        "subtitle_url": public_url(srt_path),
        "alignment_method": result.get("alignment_method"),
        "timestamp_provider": result.get("timestamp_provider"),
        "shot_count": len(shots),
    }


@router.post("/{project_id}/generate-voice")
def generate_voice(project_id: str, payload: VoicePayload | None = None):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    require_video_library(project)
    return _generate_video_library_voice(project, payload)


@router.patch("/{project_id}/voice-settings")
def update_voice_settings(project_id: str, payload: VoiceSettingsPayload):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    require_video_library(project)
    if not project.get("audio_url"):
        raise HTTPException(400, "Voice audio has not been generated")
    volume = max(0.0, min(3.0, float(payload.volume)))
    project["voice_volume"] = round(volume, 3)
    project["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return {"status": "success", "volume": project["voice_volume"]}


@router.post("/{project_id}/voice-preview")
def preview_voice(project_id: str, payload: VoicePayload | None = None):
    db = load_db(copy_data=False)
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    require_video_library(project)
    preview_text, _, _ = strip_product_marker(
        project.get("raw_script") or ""
    )
    sentence = _first_script_sentence(preview_text)
    if not sentence:
        raise HTTPException(400, "No script content available for voice preview")
    try:
        result = synthesize_volcengine_tts(
            sentence,
            payload.voice_type if payload else None,
            payload.speech_rate if payload else None,
        )
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    return StreamingResponse(
        BytesIO(result["audio"]),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store", "X-Preview-Text-Length": str(len(sentence))},
    )


@router.post("/{project_id}/generate-title")
def generate_title(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    require_video_library(project)

    script, _, _ = strip_product_marker(
        project.get("raw_script") or ""
    )
    script = script.strip()
    if not script:
        raise HTTPException(400, "No script content available for title generation")

    result = generate_viral_title(script)
    if result.get("error"):
        raise HTTPException(502, f"Title generation failed: {result['error']}")

    now = datetime.now().isoformat(timespec="seconds")
    candidates = result.get("candidates") or []
    if not candidates:
        raise HTTPException(502, "Title generation returned no candidates")
    project["title_candidates"] = candidates
    project["updated_at"] = now
    save_db(db)

    return {
        "status": "success",
        "candidates": candidates,
    }


@router.get("/_fonts/cover-title")
def download_cover_title_font():
    font_path = Path(__file__).resolve().parents[3] / "assets" / "龚帆怒放体.ttf"
    if not font_path.is_file():
        raise HTTPException(404, "Cover title font not found")
    return FileResponse(font_path, media_type="font/ttf", filename="cover-title.ttf")


@router.post("/{project_id}/generate-cover")
def generate_cover(
    project_id: str,
    file: UploadFile | None = File(None),
    title_positions: str = Form(""),
    mask_opacity: float = Form(0.0),
    preset_id: str = Form(""),
):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    require_video_library(project)

    title_line1 = str(project.get("title_line1") or "").strip()
    title_line2 = str(project.get("title_line2") or "").strip()
    if not title_line1 or not title_line2:
        raise HTTPException(400, "Please confirm the two-line title before generating the cover")
    if file is not None and not str(file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Please upload an image file")

    cover_dir = project_dir(project_id) / "cover"
    cover_dir.mkdir(parents=True, exist_ok=True)
    source_path = cover_dir / "portrait-upload"
    cover_path = cover_dir / "cover.png"
    try:
        if file is not None:
            upload_path = cover_dir / "portrait-upload.tmp"
            with upload_path.open("wb") as target:
                shutil.copyfileobj(file.file, target)
            if upload_path.stat().st_size > 20 * 1024 * 1024:
                upload_path.unlink(missing_ok=True)
                raise ValueError("Uploaded image must not exceed 20 MB")
            upload_path.replace(source_path)
        elif preset_id:
            preset = next(
                (item for item in db.get("video_library_presets", []) if item.get("id") == preset_id),
                None,
            )
            preset_source = Path(str((preset or {}).get("cover_source_path") or ""))
            presets_root = (assets_dir() / "presets").resolve()
            if not preset_source.is_file() or not preset_source.resolve().is_relative_to(presets_root):
                raise ValueError("Preset cover image is unavailable")
            shutil.copy2(preset_source, source_path)
        if not source_path.is_file():
            raise ValueError("Please upload an image file")
        try:
            raw_positions = json.loads(title_positions) if title_positions else {}
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid cover title positions") from exc
        positions = normalize_cover_title_positions(raw_positions)
        normalized_mask_opacity = max(0.0, min(1.0, float(mask_opacity)))
        compose_uploaded_cover(
            source_path,
            cover_path,
            title_line1,
            title_line2,
            positions,
            normalized_mask_opacity,
        )
    except Exception as exc:
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
        "cover_title_positions": positions,
        "cover_mask_opacity": normalized_mask_opacity,
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
        "title_positions": positions,
        "mask_opacity": normalized_mask_opacity,
    }


@router.get("/{project_id}/download-cover")
def download_cover(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    require_video_library(project)
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
    require_video_library(project)
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


@router.post("/{project_id}/generate-subtitles")
def generate_subtitles(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    require_video_library(project)
    if float(project.get("audio_duration_sec") or 0) <= 0:
        raise HTTPException(400, "请先生成配音")
    srt_path = project_dir(project_id) / "subtitles" / "subtitles.srt"
    narration = narration_shot(project)
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(
        generate_export_srt([narration], max_hanzi=VIDEO_LIBRARY_SUBTITLE_MAX_HANZI),
        encoding="utf-8",
    )
    timeline_path = project_dir(project_id) / "subtitles" / "timeline.json"
    timeline_path.write_text(json.dumps(narration, ensure_ascii=False, indent=2), encoding="utf-8")
    with db_write_transaction():
        current_db = load_db()
        current = next((p for p in current_db["projects"] if p["id"] == project_id), None)
        if current:
            current["subtitle_url"] = public_url(srt_path)
            current["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_db(current_db)
    return {"subtitle_url": public_url(srt_path), "timeline_url": public_url(timeline_path)}
