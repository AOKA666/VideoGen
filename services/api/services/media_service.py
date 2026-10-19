from __future__ import annotations

import json
import math
import os
import random
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4


def media_command(args: list[str], timeout: int = 180) -> str:
    result = subprocess.run(args, check=True, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout,
                            creationflags=0x08000000 if os.name == "nt" else 0)
    return result.stdout


AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma"}

SUBTITLE_FONT_NAME = "Douyin Sans"
SUBTITLE_FONT_SIZE = 72
SUBTITLE_OUTLINE = 5
SUBTITLE_MARGIN_V = 610  # Text bottom sits at y=1310, near the bottom of the 1080x742 window.
SUBTITLE_FONT_DIR = Path(__file__).resolve().parents[3] / "assets"
SUBTITLE_ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{SUBTITLE_FONT_NAME},{SUBTITLE_FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,{SUBTITLE_OUTLINE},0,2,60,60,{SUBTITLE_MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, centiseconds = divmod(centiseconds, 360_000)
    minutes, centiseconds = divmod(centiseconds, 6_000)
    secs, centiseconds = divmod(centiseconds, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def build_segment_ass(subtitles: list[dict], start: float, end: float) -> str | None:
    """Render subtitles overlapping this shot with timestamps relative to the shot start."""
    lines = []
    for subtitle in subtitles:
        subtitle_start = max(float(subtitle.get("start_time") or 0), start)
        subtitle_end = min(float(subtitle.get("end_time") or 0), end)
        text = str(subtitle.get("text") or "").strip()
        if not text or subtitle_end - subtitle_start < 0.02:
            continue
        lines.append(
            f"Dialogue: 0,{_ass_time(subtitle_start - start)},{_ass_time(subtitle_end - start)},"
            f"Default,,0,0,0,,{text}"
        )
    return SUBTITLE_ASS_HEADER + "\n".join(lines) + "\n" if lines else None


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


def render_video(shots: list[dict], audio: Path, background: Path, target: Path, duration: float,
                 progress=None, subtitles: list[dict] | None = None) -> None:
    """Center an aspect-preserving video inside the template's middle white window."""
    work = target.parent / ("render_" + uuid4().hex)
    work.mkdir(parents=True)
    try:
        # The reference window is y=626..1418 on a 1152x2048 canvas.
        base_graph = (
            "[0:v]scale=1080:1920,setsar=1[bg];"
            "[1:v]setpts=PTS-STARTPTS,scale=1080:742:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            "setsar=1,tpad=stop_mode=clone:stop_duration=1[clip];[bg][clip]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p"
        )
        jobs = []
        for shot in shots:
            frame_count = round(shot["end_time"] * 30) - round(shot["start_time"] * 30)
            if frame_count > 0:
                jobs.append((len(jobs), shot, frame_count))
        if not jobs:
            raise ValueError("没有可合成的视频片段")

        completed = {"count": 0}
        progress_lock = threading.Lock()

        def encode(job):
            index, shot, frame_count = job
            segment = work / f"clip_{index:05d}.mp4"
            # Pad the last shot by one second so the final audio-length trim always has frames to cut.
            frames = frame_count + 30 if index == len(jobs) - 1 else frame_count
            graph = base_graph
            ass_file = None
            if subtitles:
                document = build_segment_ass(subtitles, shot["start_time"], shot["end_time"])
                if document:
                    ass_file = work / f"subs_{index:05d}.ass"
                    ass_file.write_text(document, encoding="utf-8")
                    graph += (f",ass=filename='{_filter_path(ass_file)}'"
                              f":fontsdir='{_filter_path(SUBTITLE_FONT_DIR)}'")
            graph += "[v]"
            media_command(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(background),
                           "-ss", str(shot.get("source_start_sec", 0)), "-i", shot["local_path"],
                           "-filter_complex", graph, "-map", "[v]", "-an", "-frames:v", str(frames),
                           "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-bf", "0",
                           str(segment)], timeout=3600)
            if progress:
                with progress_lock:
                    completed["count"] += 1
                    done = completed["count"]
                progress(done, len(jobs))
            return segment

        # Encode shots in parallel: clips are independent, so this is the main speedup on longer scripts.
        workers = max(1, min(4, (os.cpu_count() or 2) // 2))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            segments = list(pool.map(encode, jobs))
        concat = work / "clips.txt"
        concat.write_text("\n".join(f"file '{p.name}'" for p in segments), encoding="utf-8")
        # Stream-copy the join (all segments share encoder settings) and add audio in one pass.
        media_command(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
                       "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
                       "-t", str(duration), "-movflags", "+faststart", str(target)], timeout=7200)
    finally:
        shutil.rmtree(work, ignore_errors=True)
