from __future__ import annotations

import json
import math
import os
import random
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4


def media_command(args: list[str], timeout: int = 180) -> str:
    result = subprocess.run(args, check=True, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout,
                            creationflags=0x08000000 if os.name == "nt" else 0)
    return result.stdout


AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma"}



def probe_audio(path: Path) -> float:
    info = json.loads(media_command([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
    ]))
    stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not stream:
        raise ValueError("文件没有可用的音频轨道")
    duration = float(stream.get("duration") or info.get("format", {}).get("duration") or 0)
    if not math.isfinite(duration) or duration < 0.2:
        raise ValueError("无法读取有效的音频时长")
    return duration


def normalize_audio(source: Path, target: Path) -> None:
    """Decode any accepted audio file into a clean MP3 the Jianying export expects."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".mp3" and source.resolve() != target.resolve():
        shutil.copy2(source, target)
        return
    media_command(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vn",
                   "-c:a", "libmp3lame", "-q:a", "2", str(target)], timeout=1800)


def probe_video(path: Path) -> dict:
    info = json.loads(media_command([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
    ]))
    stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"
                   and not s.get("disposition", {}).get("attached_pic")), None)
    if not stream:
        raise ValueError("文件没有可用的视频画面")
    duration = float(stream.get("duration") or info.get("format", {}).get("duration") or 0)
    if not math.isfinite(duration) or duration < 0.04:
        raise ValueError("无法读取有效的视频时长")
    return {"duration_sec": duration, "width": int(stream["width"]), "height": int(stream["height"])}


def assemble_clips(assets: list[dict], duration: float, project_id: str) -> list[dict]:
    """Shuffle in rounds; reuse only after exhausting a round; trim exactly in microseconds."""
    target = round(duration * 1_000_000) if math.isfinite(duration) else 0
    usable = [a for a in assets if math.isfinite(float(a.get("duration_sec") or 0))
              and round(float(a.get("duration_sec") or 0) * 1_000_000) > 0]
    if target <= 0 or not usable:
        raise ValueError("需要有效配音和至少一个视频素材")
    shots, pool, cursor = [], [], 0
    while cursor < target:
        if not pool:
            pool = random.sample(usable, len(usable))
            if len(pool) > 1 and shots and pool[-1]["id"] == shots[-1]["selected_asset_id"]:
                pool[0], pool[-1] = pool[-1], pool[0]
        asset = pool.pop()
        source_us = round(asset["duration_sec"] * 1_000_000)
        used = min(source_us, target - cursor)
        shots.append({
            "id": str(uuid4()), "project_id": project_id, "shot_index": len(shots) + 1,
            "selected_asset_id": asset["id"], "media_type": "video", "status": "video_ready",
            "file_url": asset["file_url"], "local_path": asset["local_path"], "file_name": asset["file_name"],
            "source_duration_sec": asset["duration_sec"], "source_start_sec": 0,
            "start_time": cursor / 1_000_000, "end_time": (cursor + used) / 1_000_000,
            "duration_sec": used / 1_000_000, "trimmed_sec": (source_us - used) / 1_000_000,
            "voice_text": "",
        })
        cursor += used
    return shots
