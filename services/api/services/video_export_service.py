from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageOps


CREATE_NO_WINDOW = 0x08000000


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required executable is not installed: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"{command[0]} failed: {detail[-3000:]}") from exc


def _shot_duration(shot: dict[str, Any]) -> float:
    start = float(shot.get("start_time") or 0)
    end = float(shot.get("end_time") or 0)
    duration = end - start
    if duration <= 0:
        duration = float(shot.get("duration_sec") or 3)
    return max(duration, 0.5)


def _shot_display_ranges(
    shots: list[dict[str, Any]],
    audio_duration_sec: float,
) -> list[tuple[float, float]]:
    if not shots:
        return []
    starts = []
    fallback_start = 0.0
    for index, shot in enumerate(shots):
        raw_start = shot.get("start_time")
        start = float(raw_start) if raw_start is not None else fallback_start
        if index == 0:
            start = 0.0
        else:
            start = max(start, starts[-1] + 0.001)
        starts.append(start)
        fallback_start = start + _shot_duration(shot)

    total_duration = max(audio_duration_sec, starts[-1] + _shot_duration(shots[-1]))
    ranges = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else total_duration
        ranges.append((start, max(end - start, 0.001)))
    return ranges


def probe_media(path: Path) -> dict[str, Any]:
    result = _run([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height",
        "-of",
        "json",
        str(path),
    ])
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    return {
        "path": path.name,
        "duration_sec": round(float((data.get("format") or {}).get("duration") or 0), 3),
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "has_video": bool(video),
        "has_audio": bool(audio),
    }


def render_project_video(
    shots: list[dict[str, Any]],
    scene_paths: list[Path],
    audio_path: Path,
    subtitles_path: Path,
    output_path: Path,
    transition_sec: float = 0.5,
) -> dict[str, Any]:
    if len(shots) != len(scene_paths) or not shots:
        raise RuntimeError("Every shot must have one exported scene")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and ffprobe are required to export MP4")

    audio_probe = probe_media(audio_path)
    total_duration = audio_probe["duration_sec"]
    display_ranges = _shot_display_ranges(shots, total_duration)
    durations = [duration for _, duration in display_ranges]
    transitions = [
        min(transition_sec, durations[index] / 2, durations[index + 1] / 2)
        for index in range(len(shots) - 1)
    ]
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for index, scene_path in enumerate(scene_paths):
        input_duration = durations[index] + (transitions[index] if index < len(transitions) else 0)
        command.extend([
            "-loop",
            "1",
            "-framerate",
            "30",
            "-t",
            f"{input_duration:.3f}",
            "-i",
            str(scene_path),
        ])
    command.extend(["-i", str(audio_path)])

    filters = []
    for index, input_duration in enumerate(durations):
        extended = input_duration + (transitions[index] if index < len(transitions) else 0)
        filters.append(
            f"[{index}:v]scale=1080:1080:force_original_aspect_ratio=increase,"
            f"crop=1080:1080,pad=1080:1920:0:420:color=black,"
            f"fps=30,format=yuv420p,setsar=1,"
            f"trim=duration={extended:.3f},setpts=PTS-STARTPTS[v{index}]"
        )

    video_label = "v0"
    elapsed = durations[0]
    for index, transition in enumerate(transitions, 1):
        next_label = f"x{index}"
        filters.append(
            f"[{video_label}][v{index}]xfade=transition=fade:"
            f"duration={transition:.3f}:offset={elapsed:.3f}[{next_label}]"
        )
        video_label = next_label
        elapsed += durations[index]

    filters.append(
        f"[{video_label}]subtitles=filename='subtitles.srt':"
        "force_style='FontName=Microsoft YaHei,FontSize=15,"
        "PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,"
        "BorderStyle=1,Outline=0.8,Shadow=0,MarginV=500,Alignment=2'[vout]"
    )
    filters.append(
        f"[{len(scene_paths)}:a]apad,atrim=duration={total_duration:.3f},"
        "asetpts=PTS-STARTPTS[aout]"
    )
    filter_script = output_path.parent / "ffmpeg_filter.txt"
    filter_script.write_text(";\n".join(filters), encoding="utf-8")

    command.extend([
        "-filter_complex_script",
        str(filter_script),
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-t",
        f"{total_duration:.3f}",
        str(output_path),
    ])
    _run(command, cwd=output_path.parent)
    filter_script.unlink(missing_ok=True)

    probe = probe_media(output_path)
    duration_delta = abs(probe["duration_sec"] - total_duration)
    probe.update({
        "expected_duration_sec": round(total_duration, 3),
        "duration_delta_sec": round(duration_delta, 3),
        "transition": "fade",
        "transition_sec": transition_sec,
        "subtitles_burned_in": True,
        "passed": (
            probe["width"] == 1080
            and probe["height"] == 1920
            and probe["video_codec"] == "h264"
            and probe["audio_codec"] == "aac"
            and duration_delta <= 0.2
        ),
    })
    if not probe["passed"]:
        raise RuntimeError(f"Generated MP4 failed validation: {probe}")
    return probe


def _safe_draft_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned[:80] or "VideoGen_draft"


def jianying_drafts_root() -> Path:
    configured = os.getenv("JIANYING_DRAFTS_DIR", "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path(r"E:\JianyingPro Drafts"),
        Path(os.getenv("LOCALAPPDATA", "")) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    raise RuntimeError("Jianying draft directory was not found. Configure JIANYING_DRAFTS_DIR.")


def _install_jianying_cover(draft_path: Path, cover_path: Path) -> dict[str, str]:
    cover_id = str(uuid4()).upper()
    resource_dir = draft_path / "Resources" / "cover"
    resource_dir.mkdir(parents=True, exist_ok=True)
    resource_cover = resource_dir / f"{cover_id}.jpg"
    root_cover = draft_path / "draft_cover.jpg"

    with Image.open(cover_path) as source:
        cover = ImageOps.exif_transpose(source).convert("RGB")
        cover.save(resource_cover, format="JPEG", quality=95, optimize=True)
        cover.save(root_cover, format="JPEG", quality=95, optimize=True)

    content_path = draft_path / "draft_content.json"
    content = json.loads(content_path.read_text(encoding="utf-8"))
    content["cover"] = {
        "path": str(resource_cover.resolve()),
        "type": "image",
        "time": 0,
        "time_ms": 0,
        "custom_cover_id": cover_id,
    }
    content["static_cover_image_path"] = str(resource_cover.resolve())
    content_path.write_text(
        json.dumps(content, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return {
        "cover_path": str(resource_cover.resolve()),
        "draft_cover_path": str(root_cover.resolve()),
        "custom_cover_id": cover_id,
    }


def create_jianying_native_draft(
    package_destination: Path,
    project_name: str,
    shots: list[dict[str, Any]],
    scene_paths: list[Path],
    audio_path: Path,
    subtitles_path: Path,
    cover_path: Path | None = None,
) -> dict[str, Any]:
    try:
        import pyJianYingDraft as draft
    except ImportError as exc:
        raise RuntimeError("pyJianYingDraft is required to generate native Jianying drafts") from exc

    draft_root = jianying_drafts_root()
    draft_name = _safe_draft_name(f"VideoGen_{project_name}")
    draft_path = draft_root / draft_name
    script = draft.DraftFolder(str(draft_root)).create_draft(
        draft_name,
        1080,
        1920,
        fps=30,
        allow_replace=True,
    )

    materials_dir = draft_path / "VideoGen_Materials"
    image_dir = materials_dir / "images"
    audio_dir = materials_dir / "audio"
    image_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    local_scenes = []
    for path in scene_paths:
        target = image_dir / path.name
        shutil.copy2(path, target)
        local_scenes.append(target)
    local_audio = audio_dir / audio_path.name
    shutil.copy2(audio_path, local_audio)
    local_subtitles = materials_dir / "subtitles.srt"
    shutil.copy2(subtitles_path, local_subtitles)

    script.add_track(draft.TrackType.video, "video")
    script.add_track(draft.TrackType.audio, "voice")
    audio_material = draft.AudioMaterial(str(local_audio))
    audio_duration_us = audio_material.duration
    display_ranges = _shot_display_ranges(shots, audio_duration_us / 1_000_000)
    zoom_animation = getattr(draft.GroupAnimationType, "\u7f29\u653e")
    for (start_sec, duration_sec), scene_path in zip(display_ranges, local_scenes):
        start_us = round(start_sec * 1_000_000)
        duration_us = round(duration_sec * 1_000_000)
        segment = draft.VideoSegment(
            str(scene_path),
            draft.Timerange(start_us, duration_us),
        )
        segment.add_animation(zoom_animation, duration=duration_us)
        script.add_segment(segment, "video")

    if audio_duration_us > 0:
        script.add_segment(
            draft.AudioSegment(
                audio_material,
                draft.Timerange(0, audio_duration_us),
                source_timerange=draft.Timerange(0, audio_duration_us),
            ),
            "voice",
        )
    subtitle_reference = draft.TextSegment(
        "字幕",
        draft.Timerange(0, 1_000_000),
        style=draft.TextStyle(
            size=15,
            bold=True,
            color=(1.0, 0.86, 0.0),
            align=1,
            auto_wrapping=False,
        ),
        border=draft.TextBorder(
            color=(0.0, 0.0, 0.0),
            width=20.0,
        ),
        clip_settings=draft.ClipSettings(transform_y=-0.43),
    )
    script.import_srt(
        str(local_subtitles),
        "subtitle",
        style_reference=subtitle_reference,
        clip_settings=None,
    )
    script.save()
    cover_report = None
    if cover_path and cover_path.exists():
        cover_report = _install_jianying_cover(draft_path, cover_path)

    if package_destination.exists():
        shutil.rmtree(package_destination)
    shutil.copytree(draft_path, package_destination)
    return {
        "status": "ready",
        "native_draft": True,
        "target": "JianyingPro desktop 9.3+",
        "draft_name": draft_name,
        "draft_path": str(draft_path.resolve()),
        "file_count": sum(1 for item in draft_path.rglob("*") if item.is_file()),
        "cover": cover_report,
    }
