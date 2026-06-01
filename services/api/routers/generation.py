from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from services.asset_service import new_id
from services.generation_service import generate_doubao_image, generate_srt, write_silent_wav, write_timeline
from services.store import load_db, project_dir, public_url, save_db

router = APIRouter(prefix="/api/projects", tags=["generation"])


@router.post("/{project_id}/shots/{shot_id}/generate-image")
def generate_image(project_id: str, shot_id: str):
    db = load_db()
    shot = next((s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id), None)
    if not shot:
        raise HTTPException(404, "Shot not found")
    project = next((p for p in db["projects"] if p["id"] == project_id), {})
    out = project_dir(project_id) / "images" / f"shot_{shot['shot_index']:03d}.png"
    try:
        result = generate_doubao_image(out, shot, project.get("video_ratio", "9:16"))
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
def generate_voice(project_id: str):
    db = load_db()
    shots = [s for s in db["shots"] if s["project_id"] == project_id]
    if not shots:
        raise HTTPException(400, "No shots")
    duration = max(s.get("end_time", 0) for s in shots)
    out = project_dir(project_id) / "audio" / "main_voice.wav"
    write_silent_wav(out, duration)
    return {"audio_url": public_url(out), "duration_sec": duration}


@router.post("/{project_id}/generate-subtitles")
def generate_subtitles(project_id: str):
    db = load_db()
    shots = sorted([s for s in db["shots"] if s["project_id"] == project_id], key=lambda s: s["shot_index"])
    if not shots:
        raise HTTPException(400, "No shots")
    srt_path = project_dir(project_id) / "subtitles" / "subtitles.srt"
    srt_path.write_text(generate_srt(shots), encoding="utf-8")
    timeline_path = project_dir(project_id) / "subtitles" / "timeline.json"
    write_timeline(timeline_path, shots)
    return {"subtitle_url": public_url(srt_path), "timeline_url": public_url(timeline_path)}
