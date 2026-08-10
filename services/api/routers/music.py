from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile

from services.store import assets_dir, load_db, public_url, save_db


router = APIRouter(prefix="/api/music", tags=["music"])
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def _audio_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        return round(float(result.stdout.strip()), 3)
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError) as exc:
        raise HTTPException(400, f"无法读取音乐文件：{exc}") from exc


@router.get("")
def list_music():
    db = load_db(copy_data=False)
    music = [
        item for item in db.get("music_library", [])
        if Path(str(item.get("local_path") or "")).exists()
    ]
    return {"music": music}


@router.post("")
def upload_music(file: UploadFile = File(...)):
    original_name = Path(file.filename or "background-music").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in AUDIO_EXTS:
        raise HTTPException(400, "仅支持 MP3、WAV、M4A、AAC、OGG、FLAC 音乐文件")

    music_id = str(uuid4())
    music_assets_dir = assets_dir()
    music_assets_dir.mkdir(parents=True, exist_ok=True)
    target = music_assets_dir / f"music_{music_id}{suffix}"
    try:
        with target.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        if target.stat().st_size > 100 * 1024 * 1024:
            raise ValueError("音乐文件不能超过 100 MB")
        duration = _audio_duration(target)
        if duration <= 0:
            raise ValueError("音乐时长无效")
    except Exception as exc:
        target.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(400, f"音乐上传失败：{exc}") from exc

    now = datetime.now().isoformat(timespec="seconds")
    preset = {
        "id": music_id,
        "name": Path(original_name).stem,
        "file_name": original_name,
        "file_url": public_url(target),
        "local_path": str(target),
        "duration_sec": duration,
        "file_size": target.stat().st_size,
        "created_at": now,
    }
    db = load_db()
    db.setdefault("music_library", []).append(preset)
    save_db(db)
    return {"status": "success", "music": preset}


@router.delete("/{music_id}")
def delete_music(music_id: str):
    db = load_db()
    music = next((item for item in db.get("music_library", []) if item.get("id") == music_id), None)
    if not music:
        raise HTTPException(404, "Music not found")

    local_path = Path(str(music.get("local_path") or ""))
    deleted_file = False
    if local_path.exists() and assets_dir().resolve() in local_path.resolve().parents:
        local_path.unlink()
        deleted_file = True

    db["music_library"] = [
        item for item in db.get("music_library", [])
        if item.get("id") != music_id
    ]
    now = datetime.now().isoformat(timespec="seconds")
    cleared_projects = 0
    for project in db.get("projects", []):
        if project.get("background_music_id") != music_id:
            continue
        project["background_music_id"] = None
        project["background_music_name"] = ""
        project["background_music_url"] = ""
        project["background_music_start_sec"] = 0
        project["background_music_volume"] = 0.2
        project["updated_at"] = now
        cleared_projects += 1
    save_db(db)
    return {
        "status": "deleted",
        "music_id": music_id,
        "deleted_file": deleted_file,
        "cleared_projects": cleared_projects,
    }
