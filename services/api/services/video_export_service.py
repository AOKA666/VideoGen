from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageOps

SUBTITLE_RGB_COLOR = (1.0, 1.0, 1.0)
SUBTITLE_JIANYING_BORDER_WIDTH = 70.0
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SUBTITLE_FONT_FILE = PROJECT_ROOT / "assets" / "DouyinSansBold.otf"
SUBTITLE_FONT_NAME = "Douyin Sans"
TITLE_FONT_FILE = PROJECT_ROOT / "assets" / "龚帆怒放体.ttf"
TITLE_FONT_NAME = "gongfannufangti"


def _font_result(path: str | Path, name: str) -> tuple[str, str]:
    return (str(path).replace("\\", "/"), name)


def _find_subtitle_font() -> tuple[str, str]:
    """Use the bundled Douyin Sans Bold font for burned-in subtitles."""
    configured_font = os.getenv("VIDEOGEN_SUBTITLE_FONT_FILE", "").strip()
    configured_name = os.getenv("VIDEOGEN_SUBTITLE_FONT_NAME", "").strip()
    if configured_font and Path(configured_font).is_file():
        return _font_result(configured_font, configured_name or SUBTITLE_FONT_NAME)

    if SUBTITLE_FONT_FILE.is_file():
        return _font_result(SUBTITLE_FONT_FILE, SUBTITLE_FONT_NAME)

    legacy_font = os.getenv("VIDEOGEN_FONT_FILE", "").strip()
    if legacy_font and Path(legacy_font).is_file():
        return _font_result(legacy_font, "Noto Sans CJK SC")

    fallback_candidates = [
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "Noto Sans CJK SC"),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK SC"),
        ("C:/Windows/Fonts/msyhbd.ttc", "Microsoft YaHei"),
        ("C:/Windows/Fonts/msyh.ttc", "Microsoft YaHei"),
        ("C:/Windows/Fonts/simhei.ttf", "SimHei"),
    ]
    for font_path, font_name in fallback_candidates:
        if os.path.exists(font_path):
            return _font_result(font_path, font_name)

    return _font_result("C:/Windows/Fonts/msyhbd.ttc", "Microsoft YaHei")


def _find_title_font() -> tuple[str, str]:
    """Use the bundled Gongfan Nufang font for cover titles."""
    configured_font = os.getenv("VIDEOGEN_TITLE_FONT_FILE", "").strip()
    configured_name = os.getenv("VIDEOGEN_TITLE_FONT_NAME", "").strip()
    if configured_font and Path(configured_font).is_file():
        return _font_result(configured_font, configured_name or TITLE_FONT_NAME)

    if TITLE_FONT_FILE.is_file():
        return _font_result(TITLE_FONT_FILE, TITLE_FONT_NAME)

    return _find_subtitle_font()


def _find_preferred_font() -> tuple[str, str]:
    """Backward-compatible alias for the configured subtitle font."""
    return _find_subtitle_font()


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


def _jianying_title_geometry(line1: str, line2: str) -> dict[str, float]:
    """Keep calibrated Jianying title geometry independent of cover font changes."""
    return {
        "font_size": 20.0,
        "line1_transform_y": 0.645,
        "line2_transform_y": 0.485,
    }


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
    voice_volume: float = 1.0,
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
                volume=max(0.0, min(float(voice_volume), 2.0)),
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
    # Keep subtitles and both title lines as editable Jianying text layers.
    try:
        jianying_subtitle_font = draft.FontType.抖音美好体
    except AttributeError:
        jianying_subtitle_font = None
    try:
        jianying_title_font = draft.FontType.飞扬行书
    except AttributeError:
        jianying_title_font = None

    subtitle_reference = draft.TextSegment(
        "字幕",
        draft.Timerange(0, 1_000_000),
        font=jianying_subtitle_font,
        style=draft.TextStyle(
            size=15,
            bold=True,
            color=SUBTITLE_RGB_COLOR,
            align=1,
            auto_wrapping=False,
        ),
        border=draft.TextBorder(
            color=(0.0, 0.0, 0.0),
            width=SUBTITLE_JIANYING_BORDER_WIDTH,
        ),
        clip_settings=draft.ClipSettings(transform_y=-0.43),
    )
    script.import_srt(
        str(local_subtitles),
        "subtitle",
        style_reference=subtitle_reference,
        clip_settings=None,
    )

    title_duration_us = max(audio_duration_us, 1)
    title_geometry = _jianying_title_geometry(title_line1, title_line2)
    title_border = draft.TextBorder(
        color=(0.0, 0.0, 0.0),
        width=40.0,
    )
    if title_line1:
        script.add_track(draft.TrackType.text, "title_line1")
        script.add_segment(
            draft.TextSegment(
                title_line1,
                draft.Timerange(0, title_duration_us),
                font=jianying_title_font,
                style=draft.TextStyle(
                    size=title_geometry["font_size"],
                    bold=True,
                    color=(1.0, 1.0, 1.0),
                    align=1,
                    auto_wrapping=False,
                ),
                border=title_border,
                clip_settings=draft.ClipSettings(transform_y=title_geometry["line1_transform_y"]),
            ),
            "title_line1",
        )
    if title_line2:
        script.add_track(draft.TrackType.text, "title_line2")
        script.add_segment(
            draft.TextSegment(
                title_line2,
                draft.Timerange(0, title_duration_us),
                font=jianying_title_font,
                style=draft.TextStyle(
                    size=title_geometry["font_size"],
                    bold=True,
                    underline=True,
                    color=(1.0, 0.86, 0.0),
                    align=1,
                    auto_wrapping=False,
                ),
                border=title_border,
                clip_settings=draft.ClipSettings(transform_y=title_geometry["line2_transform_y"]),
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
