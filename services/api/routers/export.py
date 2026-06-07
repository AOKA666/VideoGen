from __future__ import annotations

import csv
import json
import os
import re
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from PIL import Image, ImageOps

from services.generation_service import (
    build_export_subtitles,
    generate_export_srt,
    write_timeline,
)
from services.store import load_db, project_dir, public_url
from services.video_export_service import (
    create_jianying_native_draft,
    jianying_drafts_root,
    render_project_video,
)

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


@router.post("/{project_id}/export/assets")
def export_assets(
    project_id: str,
    output: str = Query("mp4", pattern="^(mp4|draft)$"),
):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
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
            "asset_source",
            "match_score",
        ])
        writer.writeheader()
        for shot in shots:
            writer.writerow({k: shot.get(k) for k in writer.fieldnames})
    export_subtitles = build_export_subtitles(shots)
    (export_dir / "subtitles.srt").write_text(generate_export_srt(shots), encoding="utf-8")
    (export_dir / "export_subtitles.json").write_text(
        json.dumps(export_subtitles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_timeline(export_dir / "timeline.json", shots)
    cover_source = base / "cover" / "cover.png"
    if cover_source.exists():
        shutil.copy2(cover_source, export_dir / "cover.png")
    report = []
    for shot in shots:
        candidates = [pa for pa in db["project_assets"] if pa["project_id"] == project_id and pa["shot_id"] == shot["id"]]
        report.append({"shot_id": shot["id"], "shot_index": shot["shot_index"], "status": shot["status"], "candidates": candidates})
    (export_dir / "asset_match_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    media_dir = export_dir / "media"
    media_dir.mkdir(exist_ok=True)
    exported_scenes = []
    scene_paths = []
    for shot in shots:
        selected_id = shot.get("selected_asset_id")
        asset = next((a for a in db["assets"] if a["id"] == selected_id), None) if selected_id else None
        generated = next((a for a in db["generated_assets"] if a["id"] == selected_id), None) if selected_id else None
        source_value = (asset or generated or {}).get("local_path", "")
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

    try:
        if output == "mp4":
            video_report = render_project_video(
                shots,
                scene_paths,
                copied_audio,
                export_dir / "subtitles.srt",
                export_dir / "final_video.mp4",
            )
            draft_report = None
        else:
            video_report = None
            draft_report = create_jianying_native_draft(
                export_dir / "jianying_draft",
                project["name"],
                shots,
                scene_paths,
                copied_audio,
                export_dir / "subtitles.srt",
                cover_source if cover_source.exists() else None,
            )
    except RuntimeError as exc:
        raise HTTPException(500, f"Failed to generate export deliverables: {exc}") from exc
    verification = {
        "output": output,
        "mp4": video_report,
        "jianying": draft_report,
    }
    (export_dir / "export_verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    download_url = None
    zip_file_name = None
    video_url = None
    if output == "draft":
        zip_path = base / "exports" / f"{safe_filename(project['name'])}_jianying_draft.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in export_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(export_dir))
        download_url = public_url(zip_path)
        zip_file_name = zip_path.name
    else:
        video_url = public_url(export_dir / "final_video.mp4")
    return {
        "output": output,
        "download_url": download_url,
        "video_url": video_url,
        "zip_file_name": zip_file_name,
        "export_folder": str((base / "exports").resolve()),
        "verification": verification,
    }
