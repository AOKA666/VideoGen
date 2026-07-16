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


def _escape_ffmpeg_filter_path(path: str | Path) -> str:
    """Escape a filesystem path embedded in an FFmpeg filter graph."""
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _find_preferred_font() -> tuple[str, str]:
    """Find the preferred font file and name.

    Prioritizes 文源圆体 (WenYuan Rounded), falling back to
    Microsoft YaHei / SimHei if not available.

    Returns:
        (font_file_path, font_name) — font_file_path uses forward slashes
        for FFmpeg compatibility; font_name is for the subtitles force_style
        FontName parameter.
    """
    # 1. Look for 文源圆体 bundled in the project root
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    wenyuan_candidates = [
        project_root / "WenYuanRoundedSCVF.ttf",
        Path("C:/Windows/Fonts/WenYuanRoundedSCVF.ttf"),
    ]
    for candidate in wenyuan_candidates:
        if candidate.exists():
            return (str(candidate).replace("\\", "/"), "文源圆体")

    # 2. Fall back to Microsoft YaHei / SimHei
    fallback_candidates = [
        ("C:/Windows/Fonts/msyhbd.ttc", "Microsoft YaHei"),
        ("C:/Windows/Fonts/msyh.ttc", "Microsoft YaHei"),
        ("C:/Windows/Fonts/simhei.ttf", "SimHei"),
    ]
    for font_path, font_name in fallback_candidates:
        if os.path.exists(font_path):
            return (font_path.replace("\\", "/"), font_name)

    # 3. Last resort
    return ("C:/Windows/Fonts/msyhbd.ttc", "Microsoft YaHei")


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    platform_options = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **platform_options,
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
        "format=duration:stream=index,codec_type,codec_name,width,height,duration",
        "-of",
        "json",
        str(path),
    ])
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    format_duration = float((data.get("format") or {}).get("duration") or 0)
    return {
        "path": path.name,
        "duration_sec": round(format_duration, 3),
        "video_duration_sec": round(float(video.get("duration") or 0), 3) if video else None,
        "audio_duration_sec": round(float(audio.get("duration") or 0), 3) if audio else None,
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "has_video": bool(video),
        "has_audio": bool(audio),
    }


def render_looped_background_music(
    music_path: Path,
    output_path: Path,
    target_duration_sec: float,
    start_sec: float = 0,
    volume: float = 0.2,
    crossfade_sec: float = 1.0,
) -> dict[str, Any]:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and ffprobe are required to prepare background music")
    music_probe = probe_media(music_path)
    source_duration = float(music_probe.get("duration_sec") or 0)
    if source_duration <= 0:
        raise RuntimeError("Background music has no readable duration")

    start_sec = max(0.0, min(float(start_sec or 0), max(source_duration - 0.1, 0)))
    target_duration_sec = max(float(target_duration_sec), 0.1)
    volume = max(0.0, min(float(volume), 1.0))
    first_duration = source_duration - start_sec
    fade = min(max(float(crossfade_sec), 0), source_duration / 3, first_duration / 3)
    if first_duration < 1:
        start_sec = 0
        first_duration = source_duration
        fade = min(max(float(crossfade_sec), 0), source_duration / 3)

    segment_count = 1
    combined_duration = first_duration
    added_duration = max(source_duration - fade, 0.1)
    while combined_duration < target_duration_sec:
        segment_count += 1
        combined_duration += added_duration
    segment_count = min(segment_count, 200)

    split_labels = "".join(f"[s{index}]" for index in range(segment_count))
    filters = [f"[0:a]asplit={segment_count}{split_labels}"]
    for index in range(segment_count):
        trim_start = start_sec if index == 0 else 0
        filters.append(
            f"[s{index}]atrim=start={trim_start:.6f}:end={source_duration:.6f},"
            f"asetpts=PTS-STARTPTS[p{index}]"
        )
    current = "p0"
    for index in range(1, segment_count):
        output_label = f"x{index}"
        if fade > 0:
            filters.append(
                f"[{current}][p{index}]acrossfade=d={fade:.6f}:c1=tri:c2=tri[{output_label}]"
            )
        else:
            filters.append(f"[{current}][p{index}]concat=n=2:v=0:a=1[{output_label}]")
        current = output_label
    filters.append(
        f"[{current}]atrim=duration={target_duration_sec:.6f},"
        f"volume={volume:.6f},asetpts=PTS-STARTPTS[aout]"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(music_path.resolve()),
        "-filter_complex", ";\n".join(filters),
        "-map", "[aout]",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_path.resolve()),
    ])
    report = probe_media(output_path)
    report.update({
        "source": music_path.name,
        "source_start_sec": round(start_sec, 3),
        "volume": round(volume, 3),
        "loop_segments": segment_count,
        "crossfade_sec": round(fade, 3),
    })
    return report


def render_project_video(
    shots: list[dict[str, Any]],
    scene_paths: list[Path],
    audio_path: Path,
    subtitles_path: Path,
    output_path: Path,
    transition_sec: float = 0.5,
    title_line1: str = "",
    title_line2: str = "",
    background_music_path: Path | None = None,
) -> dict[str, Any]:
    if len(shots) != len(scene_paths) or not shots:
        raise RuntimeError("Every shot must have one exported scene")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and ffprobe are required to export MP4")

    audio_probe = probe_media(audio_path)
    total_duration = audio_probe["duration_sec"]
    display_ranges = _shot_display_ranges(shots, total_duration)
    durations = [duration for _, duration in display_ranges]
    effective_transition_sec = 0.0
    if len(scene_paths) > 1 and transition_sec > 0:
        effective_transition_sec = min(float(transition_sec), min(durations) * 0.45)

    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
    ]
    input_durations = [
        duration + (effective_transition_sec if index > 0 else 0)
        for index, duration in enumerate(durations)
    ]
    for scene_path, input_duration in zip(scene_paths, input_durations):
        command.extend([
            "-loop", "1",
            "-framerate", "30",
            "-t", f"{input_duration:.6f}",
            "-i", str(scene_path.resolve()),
        ])
    audio_input_index = len(scene_paths)
    command.extend(["-i", str(audio_path.resolve())])
    music_input_index = audio_input_index + 1
    if background_music_path:
        command.extend(["-i", str(background_music_path.resolve())])

    filters = []
    for index, input_duration in enumerate(input_durations):
        filters.append(
            f"[{index}:v]scale=1080:1080:force_original_aspect_ratio=increase,"
            "crop=1080:1080,pad=1080:1920:0:420:color=black,"
            f"fps=30,format=yuv420p,setsar=1,trim=duration={input_duration:.6f},"
            f"setpts=PTS-STARTPTS[vscene{index}]"
        )
    if effective_transition_sec > 0:
        current_label = "vscene0"
        elapsed_duration = durations[0]
        for index in range(1, len(scene_paths)):
            output_label = "vbase" if index == len(scene_paths) - 1 else f"vfade{index}"
            offset = elapsed_duration - effective_transition_sec
            filters.append(
                f"[{current_label}][vscene{index}]"
                f"xfade=transition=fade:duration={effective_transition_sec:.6f}:"
                f"offset={offset:.6f}[{output_label}]"
            )
            current_label = output_label
            elapsed_duration += durations[index]
    elif len(scene_paths) == 1:
        filters.append("[vscene0]null[vbase]")
    else:
        scene_labels = "".join(f"[vscene{index}]" for index in range(len(scene_paths)))
        filters.append(f"{scene_labels}concat=n={len(scene_paths)}:v=1:a=0[vbase]")
    video_label = "vbase"

    preferred_font_path, preferred_font_name = _find_preferred_font()
    preferred_font_dir = _escape_ffmpeg_filter_path(Path(preferred_font_path).parent)
    filters.append(
        f"[{video_label}]subtitles=filename='{_escape_ffmpeg_filter_path(subtitles_path.resolve())}':"
        f"fontsdir='{preferred_font_dir}':"
        f"force_style='FontName={preferred_font_name},FontSize=15,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "BorderStyle=1,Outline=0.8,Shadow=0,MarginV=55,Alignment=2'[vsub]"
    )

    # Overlay title text in top blank area if title lines are provided
    if title_line1 or title_line2:
        # Escape special characters for FFmpeg drawtext filter
        def _escape_drawtext(text: str) -> str:
            return (text
                    .replace("\\", "\\\\\\\\")
                    .replace("'", "'\\''")
                    .replace(":", "\\\\:")
                    .replace("%", "\\\\%"))

        # Use the preferred font (文源圆体 with fallback) for title text
        font_path = _escape_ffmpeg_filter_path(preferred_font_path)

        # Large two-line title in the top safe area of the 1080x1920 canvas.
        if title_line1:
            filters.append(
                f"[vsub]drawtext=text='{_escape_drawtext(title_line1)}':"
                f"fontsize=56:fontcolor=white:borderw=4:bordercolor=black:"
                f"x=(w-text_w)/2:y=50:"
                f"fontfile='{font_path}'[vt1]"
            )
            current_label = "vt1"
        else:
            current_label = "vsub"

        # Title line 2 - centered, below line 1
        if title_line2:
            filters.append(
                f"[{current_label}]drawtext=text='{_escape_drawtext(title_line2)}':"
                f"fontsize=56:fontcolor=yellow:borderw=4:bordercolor=black:"
                f"x=(w-text_w)/2:y=130:"
                f"fontfile='{font_path}'[vout]"
            )
        else:
            # Rename current label to vout
            filters.append(f"[{current_label}]null[vout]")
    else:
        # No title - rename vsub to vout
        filters.append("[vsub]null[vout]")
    filters.append(
        f"[{audio_input_index}:a]apad,atrim=duration={total_duration:.3f},"
        "asetpts=PTS-STARTPTS[voice]"
    )
    if background_music_path:
        filters.append(
            f"[{music_input_index}:a]apad,atrim=duration={total_duration:.3f},"
            "asetpts=PTS-STARTPTS[music]"
        )
        filters.append(
            "[voice][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.95[aout]"
        )
    else:
        filters.append("[voice]anull[aout]")
    filter_script = output_path.parent / "ffmpeg_filter.txt"
    filter_script.write_text(";\n".join(filters), encoding="utf-8")

    command.extend([
        "-filter_complex_threads",
        "1",
        "-filter_complex_script",
        str(filter_script.resolve()),
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-threads",
        "2",
        "-preset",
        "fast",
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
        str(output_path.resolve()),
    ])
    try:
        _run(command, cwd=output_path.parent)
    finally:
        filter_script.unlink(missing_ok=True)

    probe = probe_media(output_path)
    duration_delta = abs(probe["duration_sec"] - total_duration)
    video_duration_delta = abs((probe.get("video_duration_sec") or 0) - total_duration)
    probe.update({
        "expected_duration_sec": round(total_duration, 3),
        "duration_delta_sec": round(duration_delta, 3),
        "video_duration_delta_sec": round(video_duration_delta, 3),
        "transition": "fade" if effective_transition_sec > 0 else "cut",
        "transition_sec": round(effective_transition_sec, 3),
        "subtitles_burned_in": True,
        "passed": (
            probe["width"] == 1080
            and probe["height"] == 1920
            and probe["video_codec"] == "h264"
            and probe["audio_codec"] == "aac"
            and duration_delta <= 0.2
            and video_duration_delta <= 0.2
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
    title_line1: str = "",
    title_line2: str = "",
    background_music_path: Path | None = None,
    background_music_start_sec: float = 0,
    background_music_volume: float = 0.2,
    music_crossfade_sec: float = 1.0,
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
    local_music = None
    if background_music_path:
        local_music = audio_dir / f"background_music{background_music_path.suffix.lower()}"
        shutil.copy2(background_music_path, local_music)
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
    if local_music:
        music_material = draft.AudioMaterial(str(local_music))
        source_duration_us = music_material.duration
        start_us = max(
            0,
            min(round(float(background_music_start_sec or 0) * 1_000_000), max(source_duration_us - 100_000, 0)),
        )
        first_duration_us = source_duration_us - start_us
        if first_duration_us < 1_000_000:
            start_us = 0
            first_duration_us = source_duration_us
        fade_us = min(
            round(max(float(music_crossfade_sec), 0) * 1_000_000),
            source_duration_us // 3,
            first_duration_us // 3,
        )
        script.add_track(draft.TrackType.audio, "music_a")
        script.add_track(draft.TrackType.audio, "music_b")
        target_start_us = 0
        segment_index = 0
        while target_start_us < audio_duration_us and segment_index < 200:
            source_start_us = start_us if segment_index == 0 else 0
            available_us = source_duration_us - source_start_us
            target_duration_us = min(available_us, audio_duration_us - target_start_us)
            segment = draft.AudioSegment(
                music_material,
                draft.Timerange(target_start_us, target_duration_us),
                source_timerange=draft.Timerange(source_start_us, target_duration_us),
                volume=max(0.0, min(float(background_music_volume), 1.0)),
            )
            segment_fade_us = min(fade_us, target_duration_us // 3)
            if segment_fade_us > 0:
                segment.add_fade(segment_fade_us, segment_fade_us)
            script.add_segment(segment, "music_a" if segment_index % 2 == 0 else "music_b")
            if target_start_us + target_duration_us >= audio_duration_us:
                break
            target_start_us += max(target_duration_us - fade_us, 1)
            segment_index += 1
    # Use 思源圆体 (ResourceHanRounded) — the closest built-in rounded font
    # to 文源圆体 in Jianying. Falls back gracefully if unavailable.
    try:
        jianying_font = draft.FontType.ResourceHanRoundedCN_Bold
    except AttributeError:
        jianying_font = None

    subtitle_reference = draft.TextSegment(
        "字幕",
        draft.Timerange(0, 1_000_000),
        font=jianying_font,
        style=draft.TextStyle(
            size=15,
            bold=True,
            color=(1.0, 1.0, 1.0),
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

    # Keep both title lines centered at the very top for the full video.
    title_duration_us = max(audio_duration_us, 1)
    title_border = draft.TextBorder(
        color=(0.0, 0.0, 0.0),
        width=20.0,
    )
    if title_line1:
        script.add_track(draft.TrackType.text, "title_line1")
        script.add_segment(
            draft.TextSegment(
                title_line1,
                draft.Timerange(0, title_duration_us),
                font=jianying_font,
                style=draft.TextStyle(
                    size=16,
                    bold=True,
                    color=(1.0, 1.0, 1.0),
                    align=1,
                    auto_wrapping=False,
                ),
                border=title_border,
                clip_settings=draft.ClipSettings(transform_y=0.86),
            ),
            "title_line1",
        )
    if title_line2:
        script.add_track(draft.TrackType.text, "title_line2")
        script.add_segment(
            draft.TextSegment(
                title_line2,
                draft.Timerange(0, title_duration_us),
                font=jianying_font,
                style=draft.TextStyle(
                    size=16,
                    bold=True,
                    underline=True,
                    color=(1.0, 0.86, 0.0),
                    align=1,
                    auto_wrapping=False,
                ),
                border=title_border,
                clip_settings=draft.ClipSettings(transform_y=0.72),
            ),
            "title_line2",
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
        "background_music": bool(local_music),
    }
