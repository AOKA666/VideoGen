from __future__ import annotations

import csv
import json
import os
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from PIL import Image, ImageOps

from services.generation_service import (
    VIDEO_LIBRARY_SUBTITLE_MAX_HANZI,
    build_export_subtitles,
    generate_export_srt,
    generate_export_subtitle_txt,
    narration_shot,
    write_timeline,
)
from services.media_service import assemble_clips
from services.product_placement_service import resolve_product_overlays
from services.store import db_write_transaction, load_db, project_dir, public_url, save_db
from services.video_export_service import (
    _transform_y_for_visual_y,
    create_jianying_native_draft,
    jianying_drafts_root,
)

VIDEO_LIBRARY_CLIP_WINDOW = (0, 589, 1080, 742)
VIDEO_LIBRARY_SUBTITLE_VISUAL_Y = 1180

router = APIRouter(prefix="/api/projects", tags=["export"])


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned[:80] or "project"


def get_project(project_id: str) -> dict:
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.post("/{project_id}/export/open-folder")
def open_export_folder(project_id: str):
    get_project(project_id)
    export_root = project_dir(project_id) / "exports"
    export_root.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(str(export_root.resolve()))
    except (AttributeError, OSError) as exc:
        raise HTTPException(500, f"Failed to open export folder: {exc}") from exc
    return {"path": str(export_root.resolve())}


@router.post("/{project_id}/export/open-draft-folder")
def open_draft_folder(project_id: str):
    project = get_project(project_id)
    draft_path = jianying_drafts_root() / safe_filename(f"VideoGen_{project['name']}")
    target = draft_path if draft_path.exists() else jianying_drafts_root()
    try:
        os.startfile(str(target.resolve()))
    except (AttributeError, OSError) as exc:
        raise HTTPException(500, f"Failed to open Jianying draft folder: {exc}") from exc
    return {"path": str(target.resolve())}


def _export_video_library_draft(db: dict, project: dict) -> dict:
    """Export the video-library workflow as a Jianying draft: template + clips + voice + music."""
    project_id = project["id"]
    base = project_dir(project_id)
    audio = Path(project.get("audio_path") or "")
    if not audio.is_file():
        audio = base / "audio" / "main_voice.mp3"
    if not audio.is_file():
        raise HTTPException(400, "请先生成配音")
    duration = float(project.get("audio_duration_sec") or 0)
    if duration <= 0:
        raise HTTPException(400, "配音时长无效，请重新生成配音")

    shots = sorted([s for s in db["shots"] if s["project_id"] == project_id], key=lambda s: s["shot_index"])
    if not shots or any(not Path(s.get("local_path") or "").is_file() for s in shots):
        assets = [
            asset for asset in db.get("video_library", [])
            if Path(asset.get("local_path") or "").is_file()
        ]
        if not assets:
            raise HTTPException(400, "请先到素材库上传视频")
        shots = assemble_clips(assets, duration, project_id)
        with db_write_transaction():
            current_db = load_db()
            current_db["shots"] = [
                s for s in current_db["shots"] if s["project_id"] != project_id
            ] + shots
            save_db(current_db)
    scene_paths = [Path(s["local_path"]) for s in shots]

    from routers.media import templates  # Local import avoids a router import cycle.
    template = next(
        (item for item in templates() if item["id"] == project.get("background_template_id")),
        None,
    )
    if not template or not Path(template["local_path"]).is_file():
        raise HTTPException(400, "背景模板不存在")

    export_dir = base / "exports" / "package"
    if export_dir.exists():
        shutil.rmtree(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "raw_script.txt").write_text(project.get("raw_script", ""), encoding="utf-8")
    (export_dir / "storyboard.json").write_text(
        json.dumps(shots, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    narration = narration_shot(project)
    srt_content = generate_export_srt(
        [narration], max_hanzi=VIDEO_LIBRARY_SUBTITLE_MAX_HANZI,
    )
    srt_source = base / "subtitles" / "subtitles.srt"
    srt_source.parent.mkdir(parents=True, exist_ok=True)
    srt_source.write_text(srt_content, encoding="utf-8")
    (export_dir / "subtitles.srt").write_text(srt_content, encoding="utf-8")
    (export_dir / "timeline.json").write_text(
        json.dumps(narration, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    cover_source = base / "cover" / "cover.png"
    if cover_source.exists():
        shutil.copy2(cover_source, export_dir / "cover.png")
    copied_audio = export_dir / "main_voice.mp3"
    shutil.copy2(audio, copied_audio)

    music_preset = next(
        (
            item for item in db.get("music_library", [])
            if item.get("id") == project.get("background_music_id")
        ),
        None,
    )
    source_music_value = str((music_preset or {}).get("local_path") or "").strip()
    source_music = Path(source_music_value) if source_music_value else None
    if source_music is not None and not source_music.is_file():
        source_music = None
    music_start_sec = float(project.get("background_music_start_sec") or 0)
    music_volume = float(project.get("background_music_volume") or 0.2)
    voice_volume = max(0.0, min(float(project.get("voice_volume", 1.0)), 3.0))
    if source_music:
        shutil.copy2(
            source_music,
            export_dir / f"background_music_source{source_music.suffix.lower()}",
        )

    try:
        product_items, product_report = resolve_product_overlays(db, project, duration)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    product_payload = {
        "items": product_items,
        "start_sec": product_report.get("start_sec"),
        "transition_sec": product_report.get("transition_sec", 0.5),
        "fade_sec": product_report.get("fade_sec", 0.3),
        "pip_box": product_report.get("pip_box"),
    } if product_items else None

    subtitle_transform_y = _transform_y_for_visual_y(VIDEO_LIBRARY_SUBTITLE_VISUAL_Y)
    try:
        draft_report = create_jianying_native_draft(
            export_dir / "jianying_draft",
            project["name"],
            shots,
            scene_paths,
            copied_audio,
            export_dir / "subtitles.srt",
            cover_source if cover_source.exists() else None,
            title_line1=project.get("title_line1", ""),
            title_line2=project.get("title_line2", ""),
            background_music_path=source_music,
            background_music_start_sec=music_start_sec,
            background_music_volume=music_volume,
            music_crossfade_sec=1.0,
            voice_volume=voice_volume,
            background_template_path=Path(template["local_path"]),
            scene_media="video",
            copy_scenes=False,
            animate_scenes=False,
            clip_window=VIDEO_LIBRARY_CLIP_WINDOW,
            subtitle_transform_y=subtitle_transform_y,
            product_placement=product_payload,
        )
    except Exception as exc:
        raise HTTPException(500, f"Failed to generate export deliverables: {exc}") from exc

    verification = {
        "output": "draft",
        "jianying": draft_report,
        "background_music": ({
            "source": source_music.name,
            "source_start_sec": music_start_sec,
            "volume": music_volume,
            "crossfade_sec": 1.0,
        } if source_music else None),
        "voice_volume": voice_volume,
        "scene_media": "video",
        "clip_window": VIDEO_LIBRARY_CLIP_WINDOW,
        "subtitle_transform_y": round(subtitle_transform_y, 4),
        "product_placement": {
            "enabled": bool(product_items),
            "marker_present": bool(product_report.get("marker_present")),
            "start_sec": product_report.get("start_sec"),
            "start_sec_stale": bool(product_report.get("start_sec_stale")),
            "items": [
                {
                    "asset_id": item["asset_id"],
                    "media_type": item["media_type"],
                    "requested_duration_sec": item["requested_duration_sec"],
                    "effective_duration_sec": item["duration_sec"],
                    "source_path": item["path"],
                    "fill_duration": item.get("fill_duration", True),
                    "source_duration_sec": item.get("source_duration_sec"),
                    "pip_box": item.get("pip_box"),
                }
                for item in product_items
            ],
            "transition_sec": product_report.get("transition_sec"),
            "fade_sec": product_report.get("fade_sec"),
            "pip_box": product_report.get("pip_box"),
            "truncated": bool(product_report.get("truncated")),
        },
        "external_media_references": (
            [str(path.resolve()) for path in scene_paths]
            + [item["path"] for item in product_items]
        ),
        "portable_zip": False,
    }
    (export_dir / "export_verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    now = datetime.now().isoformat(timespec="seconds")
    with db_write_transaction():
        current_db = load_db()
        current = next((p for p in current_db["projects"] if p["id"] == project_id), None)
        if current:
            current.update({"status": "draft_ready", "last_export_at": now, "updated_at": now})
            save_db(current_db)
    return {
        "output": "draft",
        "download_url": None,
        "zip_file_name": "",
        "export_folder": str((base / "exports").resolve()),
        "verification": verification,
    }


@router.post("/{project_id}/export/package")
def export_package(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    if project.get("workflow_type") == "video_library":
        return _export_video_library_draft(db, project)
    shots = sorted([s for s in db["shots"] if s["project_id"] == project_id], key=lambda s: s["shot_index"])
    if not shots:
        raise HTTPException(400, "No shots")
    base = project_dir(project_id)
    export_dir = base / "exports" / "package"
    if export_dir.exists():
        shutil.rmtree(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "raw_script.txt").write_text(project.get("raw_script", ""), encoding="utf-8")
    (export_dir / "rewritten_script.txt").write_text(project.get("rewritten_script", ""), encoding="utf-8")
    (export_dir / "storyboard.json").write_text(json.dumps(shots, ensure_ascii=False, indent=2), encoding="utf-8")
    with (export_dir / "storyboard.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "shot_index",
            "voice_text",
            "duration_sec",
            "visual_need",
            "required_object",
            "required_scene",
            "status",
            "image_prompt",
        ])
        writer.writeheader()
        for shot in shots:
            writer.writerow({k: shot.get(k) for k in writer.fieldnames})
    export_subtitles = build_export_subtitles(shots)
    (export_dir / "subtitles.srt").write_text(generate_export_srt(shots), encoding="utf-8")
    (export_dir / "subtitles.txt").write_text(generate_export_subtitle_txt(shots), encoding="utf-8")
    (export_dir / "export_subtitles.json").write_text(
        json.dumps(export_subtitles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_timeline(export_dir / "timeline.json", shots)
    cover_source = base / "cover" / "cover.png"
    if cover_source.exists():
        shutil.copy2(cover_source, export_dir / "cover.png")
    media_dir = export_dir / "media"
    media_dir.mkdir(exist_ok=True)
    exported_scenes = []
    scene_paths = []
    for shot in shots:
        selected_id = shot.get("selected_asset_id")
        generated = next(
            (
                item for item in db["generated_assets"]
                if item["id"] == selected_id
                and item.get("project_id") == project_id
                and item.get("shot_id") == shot.get("id")
            ),
            None,
        ) if selected_id else None
        source_value = (generated or {}).get("local_path", "")
        source = Path(source_value) if source_value else None
        scene_name = f"scene_{int(shot['shot_index']):02d}.png"
        target = media_dir / scene_name
        placeholder = not source or not source.exists()
        if placeholder:
            Image.new("RGB", (1080, 1080), color=(30, 30, 34)).save(target, format="PNG")
        else:
            try:
                with Image.open(source) as image:
                    converted = ImageOps.exif_transpose(image).convert("RGBA")
                    crop = (generated or {}).get("crop_region") or {}
                    width, height = converted.size
                    crop_width = int(crop.get("image_width") or 0)
                    crop_height = int(crop.get("image_height") or 0)
                    if crop.get("size") and (not crop_width or crop_width == width) and (not crop_height or crop_height == height):
                        side = max(1, min(int(round(float(crop.get("size") or 0))), width, height))
                        left = max(0, min(int(round(float(crop.get("x") or 0))), width - side))
                        top = max(0, min(int(round(float(crop.get("y") or 0))), height - side))
                        converted = converted.crop((left, top, left + side, top + side))
                    converted.save(target, format="PNG", optimize=True)
            except Exception as exc:
                raise HTTPException(
                    500,
                    f"Failed to export shot {shot['shot_index']} image as PNG: {exc}",
                ) from exc
        scene_paths.append(target)
        exported_scenes.append({
            "shot_index": shot["shot_index"],
            "asset_id": selected_id,
            "file_name": scene_name,
            "placeholder": placeholder,
        })
    (export_dir / "scene_manifest.json").write_text(
        json.dumps(exported_scenes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    copied_audio = None
    for audio_name in ["main_voice.mp3", "main_voice.wav"]:
        audio = base / "audio" / audio_name
        if audio.exists():
            shutil.copy2(audio, export_dir / audio_name)
            copied_audio = export_dir / audio_name
            break
    if not copied_audio:
        raise HTTPException(400, "No voice audio found. Please generate voice and subtitles before export.")

    music_preset = next(
        (
            item for item in db.get("music_library", [])
            if item.get("id") == project.get("background_music_id")
        ),
        None,
    )
    source_music_value = str((music_preset or {}).get("local_path") or "").strip()
    source_music = Path(source_music_value) if source_music_value else None
    if source_music is not None and not source_music.is_file():
        source_music = None
    music_start_sec = float(project.get("background_music_start_sec") or 0)
    music_volume = float(project.get("background_music_volume") or 0.2)
    voice_volume = max(0.0, min(float(project.get("voice_volume", 1.0)), 3.0))
    if source_music:
        music_copy = export_dir / f"background_music_source{source_music.suffix.lower()}"
        shutil.copy2(source_music, music_copy)

    try:
        draft_report = create_jianying_native_draft(
            export_dir / "jianying_draft",
            project["name"],
            shots,
            scene_paths,
            copied_audio,
            export_dir / "subtitles.srt",
            cover_source if cover_source.exists() else None,
            title_line1=project.get("title_line1", ""),
            title_line2=project.get("title_line2", ""),
            background_music_path=source_music,
            background_music_start_sec=music_start_sec,
            background_music_volume=music_volume,
            music_crossfade_sec=1.0,
            voice_volume=voice_volume,
        )
    except Exception as exc:
        raise HTTPException(500, f"Failed to generate export deliverables: {exc}") from exc
    verification = {
        "output": "draft",
        "jianying": draft_report,
        "background_music": ({
            "source": source_music.name,
            "source_start_sec": music_start_sec,
            "volume": music_volume,
            "crossfade_sec": 1.0,
        } if source_music else None),
        "voice_volume": voice_volume,
    }
    (export_dir / "export_verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    zip_path = base / "exports" / f"{safe_filename(project['name'])}_jianying_draft.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in export_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(export_dir))
    return {
        "output": "draft",
        "download_url": public_url(zip_path),
        "zip_file_name": zip_path.name,
        "export_folder": str((base / "exports").resolve()),
        "verification": verification,
    }
