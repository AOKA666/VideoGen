from __future__ import annotations

import json
import math
import os
import re
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageOps

from services.settings_service import configured_jianying_directory
from services.title_style import TITLE_JIANYING_FONT_SIZE

SUBTITLE_RGB_COLOR = (1.0, 1.0, 1.0)
SUBTITLE_JIANYING_BORDER_WIDTH = 40.0
SUBTITLE_JIANYING_FONT_SIZE = 15.0
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SUBTITLE_FONT_FILE = PROJECT_ROOT / "assets" / "DouyinSansBold.otf"
SUBTITLE_FONT_NAME = "Douyin Sans"
TITLE_FONT_FILE = PROJECT_ROOT / "assets" / "龚帆怒放体.ttf"
TITLE_FONT_NAME = "gongfannufangti"
# Editable covers validated in Jianying 11.6 use schema 189. The generator's
# main-timeline template is still 110; do not inherit that for nested covers.
JIANYING_COVER_SCHEMA_VERSION = "189.0.0"


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


JIANYING_CANVAS_WIDTH = 1080
JIANYING_CANVAS_HEIGHT = 1920
# Jianying contains oversized material inside the canvas before applying clip.scale.
JIANYING_SCALE_RELATIVE_TO_CANVAS_FIT = True


def _clip_window_scale(width: int, height: int, window_width: int, window_height: int) -> float:
    """Uniform scale that fits a material into a centered window of the 1080x1920 canvas."""
    if width <= 0 or height <= 0 or window_width <= 0 or window_height <= 0:
        return 1.0
    base = (
        min(JIANYING_CANVAS_WIDTH / width, JIANYING_CANVAS_HEIGHT / height)
        if JIANYING_SCALE_RELATIVE_TO_CANVAS_FIT else 1.0
    )
    return min(window_width / (width * base), window_height / (height * base))


def _transform_y_for_visual_y(visual_y: float) -> float:
    """Convert a target canvas y coordinate into Jianying's half-canvas transform unit."""
    half = JIANYING_CANVAS_HEIGHT / 2
    return (half - visual_y) / half


def _pip_geometry(width: int, height: int, box: dict) -> tuple[float, float, float]:
    """Return (uniform scale, transform_x, transform_y) for a normalized PIP box."""
    def number(key: str, fallback: float) -> float:
        try:
            value = float(box.get(key, fallback))
        except (TypeError, ValueError):
            return fallback
        return value if math.isfinite(value) else fallback

    x = max(0.0, min(number("x", 0.0), 1.0))
    y = max(0.0, min(number("y", 0.0), 1.0))
    w = max(0.01, min(number("w", 0.4), 1.0 - x))
    h = max(0.01, min(number("h", 0.3), 1.0 - y))
    box_width = max(1, round(w * JIANYING_CANVAS_WIDTH))
    box_height = max(1, round(h * JIANYING_CANVAS_HEIGHT))
    center_x = (x + w / 2) * JIANYING_CANVAS_WIDTH
    center_y = (y + h / 2) * JIANYING_CANVAS_HEIGHT
    scale = _clip_window_scale(width, height, box_width, box_height)
    transform_x = (center_x - JIANYING_CANVAS_WIDTH / 2) / (JIANYING_CANVAS_WIDTH / 2)
    transform_y = (JIANYING_CANVAS_HEIGHT / 2 - center_y) / (JIANYING_CANVAS_HEIGHT / 2)
    return scale, transform_x, transform_y


def _add_product_placement_track(
    script, draft, placement: dict | None, clip_relative_index: int,
    materials_dir: Path, audio_duration_us: int,
) -> dict | None:
    """Add a PIP track above the base clips, at original speed with dissolve transitions."""
    items = (placement or {}).get("items") or []
    if not items:
        return None
    try:
        start_us = round(float(placement.get("start_sec") or 0) * 1_000_000)
    except (TypeError, ValueError):
        start_us = 0
    start_us = min(max(start_us, 0), audio_duration_us)
    if audio_duration_us - start_us <= 0:
        return None

    track_name = "products"
    script.add_track(
        draft.TrackType.video, track_name,
        relative_index=clip_relative_index + 1,
    )
    product_dir = materials_dir / "products"
    product_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[Path, Path] = {}
    records = []
    cursor = start_us
    try:
        fade_us = round(float(placement.get("fade_sec") or 0.3) * 1_000_000)
        transition_us = round(float(placement.get("transition_sec") or 0.5) * 1_000_000)
    except (TypeError, ValueError):
        fade_us, transition_us = 300_000, 500_000
    fade_us = max(0, fade_us)

    for item in items:
        remaining = min(
            round(float(item.get("duration_sec") or 0) * 1_000_000),
            audio_duration_us - cursor,
        )
        if remaining <= 0:
            break
        source = Path(str(item.get("path") or "")).resolve()
        if not source.is_file():
            raise ValueError(f"带货素材文件不存在：{source.name}")
        if source not in copied:
            target = product_dir / f"{uuid4().hex}{source.suffix.lower()}"
            shutil.copy2(source, target)
            copied[source] = target
        material = draft.VideoMaterial(str(copied[source]))
        scale, transform_x, transform_y = _pip_geometry(
            material.width, material.height,
            item.get("pip_box") or placement.get("pip_box") or {},
        )
        clip_settings = draft.ClipSettings(
            scale_x=scale,
            scale_y=scale,
            transform_x=transform_x,
            transform_y=transform_y,
        )

        if material.material_type == "video":
            if material.duration <= 0:
                raise ValueError(f"无法读取带货视频时长：{source.name}")
            remaining = min(remaining, material.duration)
            source_duration_us = remaining
        else:
            source_duration_us = remaining
        if remaining <= 0:
            break
        segment = draft.VideoSegment(
            material,
            draft.Timerange(cursor, remaining),
            source_timerange=draft.Timerange(0, source_duration_us),
            volume=0.0,
            clip_settings=clip_settings,
        )
        records.append({"segments": [segment], "duration_us": remaining})
        cursor += remaining

    try:
        crossfade = getattr(draft.TransitionType, "叠化")
    except AttributeError:
        crossfade = None
    if crossfade is not None:
        for index in range(len(records) - 1):
            previous = records[index]
            following = records[index + 1]
            previous_segment = previous["segments"][-1]
            following_segment = following["segments"][0]
            duration_us = min(
                transition_us,
                previous["duration_us"] // 2,
                following["duration_us"] // 2,
                previous_segment.target_timerange.duration // 2,
                following_segment.target_timerange.duration // 2,
            )
            if duration_us >= 100_000:
                previous_segment.add_transition(crossfade, duration=duration_us)

    if records and fade_us > 0:
        first = records[0]["segments"][0]
        first_duration = first.target_timerange.duration
        fade_in = min(fade_us, first_duration // 2)
        if fade_in > 0:
            first.clip_settings = deepcopy(first.clip_settings)
            first.clip_settings.alpha = 0.0
            first.add_keyframe(draft.KeyframeProperty.alpha, 0, 0.0)
            first.add_keyframe(draft.KeyframeProperty.alpha, fade_in, 1.0)

        last = records[-1]["segments"][-1]
        last_duration = last.target_timerange.duration
        fade_out = min(fade_us, last_duration // 2)
        if fade_out > 0:
            last.add_keyframe(draft.KeyframeProperty.alpha, max(last_duration - fade_out, 0), 1.0)
            last.add_keyframe(draft.KeyframeProperty.alpha, max(last_duration - 1, 0), 0.0)

    # Add segments only after transitions/keyframes are attached, so the script
    # registers the transition material along with each segment.
    for record in records:
        for segment in record["segments"]:
            script.add_segment(segment, track_name)

    return {
        "track": track_name,
        "item_count": len(records),
        "segment_count": sum(len(record["segments"]) for record in records),
        "start_sec": round(start_us / 1_000_000, 3),
        "end_sec": round(cursor / 1_000_000, 3),
        "transition_sec": round(transition_us / 1_000_000, 3),
        "fade_sec": round(fade_us / 1_000_000, 3),
    }


def _jianying_title_geometry(line1: str, line2: str) -> dict[str, float]:
    """Keep calibrated Jianying title geometry independent of cover font changes."""
    return {
        "font_size": TITLE_JIANYING_FONT_SIZE,
        "line1_transform_y": 0.645,
        "line2_transform_y": 0.485,
    }


def _safe_draft_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned[:80] or "VideoGen_draft"


def jianying_drafts_root() -> Path:
    configured = os.getenv("JIANYING_DRAFTS_DIR", "").strip()
    candidates = [
        configured_jianying_directory(),
        Path(configured) if configured else None,
        Path(r"E:\JianyingPro Drafts"),
        Path(os.getenv("LOCALAPPDATA", "")) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    raise RuntimeError("Jianying draft directory was not found. Configure JIANYING_DRAFTS_DIR.")


def _install_jianying_cover(draft_path: Path, cover_path: Path) -> dict[str, str]:
    import pyJianYingDraft as draft

    content_path = draft_path / "draft_content.json"
    content = json.loads(content_path.read_text(encoding="utf-8-sig"))
    cover_id = str(uuid4()).upper()
    resource_dir = draft_path / "Resources" / "cover"
    resource_dir.mkdir(parents=True, exist_ok=True)
    resource_cover = resource_dir / f"{cover_id}.jpg"
    root_cover = draft_path / "draft_cover.jpg"
    local_cover = draft_path / "draft_local_cover.jpg"

    with Image.open(cover_path) as source:
        cover = ImageOps.exif_transpose(source).convert("RGB")
        cover.save(resource_cover, format="JPEG", quality=95, optimize=True)
        cover.save(root_cover, format="JPEG", quality=95, optimize=True)
        thumbnail = cover.copy()
        thumbnail.thumbnail((320, 320), Image.Resampling.LANCZOS)
        thumbnail.save(local_cover, format="JPEG", quality=95)
        width, height = cover.size

    # Jianying's editable cover is a separate composition referenced by
    # cover_draft_id, not an image path on the main timeline.
    # Verified against a native Jianying 11.6 draft and the editor's cover slot:
    # preserve the nested duration, use a draft-relative resource path and
    # render layer 101. The same installer serves both image and video exports.
    cover_script = draft.ScriptFile(width, height, 30, False)
    cover_script.add_track(draft.TrackType.video, "cover", relative_index=101)
    cover_script.add_segment(draft.VideoSegment(
        str(resource_cover.resolve()),
        draft.Timerange(int(content.get("duration") or 0) + 1_000_000, 3_000_000),
        source_timerange=draft.Timerange(0, 3_000_000),
        volume=0.0,
    ), "cover")
    composition = json.loads(cover_script.dumps())
    # Match a native 11.6 cover: the composition must include its image's
    # complete time range. A zero duration survives as an empty editor cover.
    composition["id"] = cover_id
    composition["new_version"] = JIANYING_COVER_SCHEMA_VERSION
    composition["canvas_config"] = {}
    composition["config"]["sticker_max_index"] = 14000
    composition["tracks"][0]["segments"][0]["track_render_index"] = 101
    composition["materials"]["videos"][0]["path"] = (
        "##_draftpath_placeholder_0E685133-18CE-45ED-8CB8-2904A212EC80_##"
        f"/Resources/cover/{resource_cover.name}"
    )
    composition_id = f"{cover_id}_material"
    old_cover_id = (content.get("cover") or {}).get("cover_draft_id")
    materials = content.setdefault("materials", {})
    materials["drafts"] = [
        item for item in materials.get("drafts", [])
        if not old_cover_id or item.get("id") != old_cover_id
    ] + [{
        "id": composition_id,
        "type": "composition",
        "draft": composition,
        "category_id": "",
        "category_name": "",
        "combination_id": "",
        "formula_id": "",
        "name": "",
        "precompile_combination": False,
    }]
    content["cover"] = {
        "cover_draft_id": composition_id,
        "cover_template": None,
        "sub_type": "local",
        "type": "image",
        "web_cover_info": None,
    }
    content["static_cover_image_path"] = ""
    content_path.write_text(
        json.dumps(content, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    meta_path = draft_path / "draft_meta_info.json"
    if meta_path.is_file():
        metadata = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        metadata["draft_cover"] = str(root_cover.resolve())
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    return {
        "cover_path": str(resource_cover.resolve()),
        "draft_cover_path": str(root_cover.resolve()),
        "local_cover_path": str(local_cover.resolve()),
        "cover_draft_id": composition_id,
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
    background_template_path: Path | None = None,
    scene_media: str = "image",
    copy_scenes: bool = True,
    animate_scenes: bool = True,
    clip_window: tuple[int, int, int, int] | None = None,
    subtitle_transform_y: float = -0.43,
    product_placement: dict[str, Any] | None = None,
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
        if copy_scenes:
            target = image_dir / path.name
            shutil.copy2(path, target)
        else:
            target = path
        local_scenes.append(target)
    local_audio = audio_dir / audio_path.name
    shutil.copy2(audio_path, local_audio)
    local_music = None
    if background_music_path:
        local_music = audio_dir / f"background_music{background_music_path.suffix.lower()}"
        shutil.copy2(background_music_path, local_music)
    local_subtitles = materials_dir / "subtitles.srt"
    shutil.copy2(subtitles_path, local_subtitles)

    script.add_track(draft.TrackType.audio, "voice")
    audio_material = draft.AudioMaterial(str(local_audio))
    audio_duration_us = audio_material.duration
    display_ranges = _shot_display_ranges(shots, audio_duration_us / 1_000_000)
    if background_template_path:
        script.add_track(draft.TrackType.video, "background")
        background_target = image_dir / f"background{background_template_path.suffix.lower()}"
        shutil.copy2(background_template_path, background_target)
        script.add_segment(
            draft.VideoSegment(
                str(background_target),
                draft.Timerange(0, max(audio_duration_us, 1)),
                volume=0,
            ),
            "background",
        )
    clip_track = "clips" if background_template_path else "video"
    clip_relative_index = 1 if background_template_path else 0
    script.add_track(
        draft.TrackType.video, clip_track,
        relative_index=clip_relative_index,
    )
    zoom_animation = getattr(draft.GroupAnimationType, "\u7f29\u653e")
    for (start_sec, duration_sec), scene_path in zip(display_ranges, local_scenes):
        start_us = round(start_sec * 1_000_000)
        material = draft.VideoMaterial(str(scene_path))
        duration_us = min(round(duration_sec * 1_000_000), material.duration)
        if duration_us <= 0:
            continue
        clip_settings = None
        if clip_window:
            scale = _clip_window_scale(material.width, material.height, clip_window[2], clip_window[3])
            clip_settings = draft.ClipSettings(scale_x=scale, scale_y=scale)
        segment = draft.VideoSegment(
            material,
            draft.Timerange(start_us, duration_us),
            source_timerange=draft.Timerange(0, duration_us),
            volume=0,
            clip_settings=clip_settings,
        )
        if animate_scenes:
            segment.add_animation(zoom_animation, duration=duration_us)
        script.add_segment(segment, clip_track)

    product_report = _add_product_placement_track(
        script, draft, product_placement, clip_relative_index,
        materials_dir, audio_duration_us,
    )

    if audio_duration_us > 0:
        script.add_segment(
            draft.AudioSegment(
                audio_material,
                draft.Timerange(0, audio_duration_us),
                source_timerange=draft.Timerange(0, audio_duration_us),
                volume=max(0.0, min(float(voice_volume), 3.0)),
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
    # 抖音美好体 is the Jianying name for the bundled Douyin Sans (Bold) family.
    try:
        jianying_text_font = draft.FontType.抖音美好体
    except AttributeError:
        jianying_text_font = None

    subtitle_reference = draft.TextSegment(
        "字幕",
        draft.Timerange(0, 1_000_000),
        font=jianying_text_font,
        style=draft.TextStyle(
            size=SUBTITLE_JIANYING_FONT_SIZE,
            bold=True,
            color=SUBTITLE_RGB_COLOR,
            align=1,
            auto_wrapping=True,
        ),
        border=draft.TextBorder(
            color=(0.0, 0.0, 0.0),
            width=SUBTITLE_JIANYING_BORDER_WIDTH,
        ),
        clip_settings=draft.ClipSettings(transform_y=subtitle_transform_y),
    )
    script.import_srt(
        str(local_subtitles),
        "subtitle",
        style_reference=subtitle_reference,
        clip_settings=None,
    )

    title_duration_us = max(audio_duration_us, 1)
    title_geometry = _jianying_title_geometry(title_line1, title_line2)
    if title_line1:
        script.add_track(draft.TrackType.text, "title_line1")
        script.add_segment(
            draft.TextSegment(
                title_line1,
                draft.Timerange(0, title_duration_us),
                font=jianying_text_font,
                style=draft.TextStyle(
                    size=title_geometry["font_size"],
                    bold=True,
                    color=(1.0, 1.0, 1.0),
                    align=1,
                    auto_wrapping=False,
                ),
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
                font=jianying_text_font,
                style=draft.TextStyle(
                    size=title_geometry["font_size"],
                    bold=True,
                    underline=True,
                    color=(1.0, 0.86, 0.0),
                    align=1,
                    auto_wrapping=False,
                ),
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
        "scene_media": scene_media,
        "clip_window": clip_window,
        "clip_scale_mode": (
            "canvas_fit" if JIANYING_SCALE_RELATIVE_TO_CANVAS_FIT else "native_pixels"
        ),
        "copied_scenes": copy_scenes,
        "animated_scenes": animate_scenes,
        "product_placement": product_report,
    }
