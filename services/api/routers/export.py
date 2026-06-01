from __future__ import annotations

import csv
import json
import re
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException

from services.generation_service import generate_srt, write_timeline
from services.store import load_db, project_dir, public_url

router = APIRouter(prefix="/api/projects", tags=["export"])


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned[:80] or "project"


@router.post("/{project_id}/export/assets")
def export_assets(project_id: str):
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
        writer = csv.DictWriter(f, fieldnames=["shot_index", "voice_text", "duration_sec", "visual_need", "status", "asset_source", "match_score"])
        writer.writeheader()
        for shot in shots:
            writer.writerow({k: shot.get(k) for k in writer.fieldnames})
    (export_dir / "subtitles.srt").write_text(generate_srt(shots), encoding="utf-8")
    write_timeline(export_dir / "timeline.json", shots)
    report = []
    for shot in shots:
        candidates = [pa for pa in db["project_assets"] if pa["project_id"] == project_id and pa["shot_id"] == shot["id"]]
        report.append({"shot_id": shot["id"], "shot_index": shot["shot_index"], "status": shot["status"], "candidates": candidates})
    (export_dir / "asset_match_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    media_dir = export_dir / "media"
    media_dir.mkdir(exist_ok=True)
    for shot in shots:
        selected_id = shot.get("selected_asset_id")
        if not selected_id:
            continue
        asset = next((a for a in db["assets"] if a["id"] == selected_id), None)
        generated = next((a for a in db["generated_assets"] if a["id"] == selected_id), None)
        source = Path((asset or generated or {}).get("local_path", ""))
        if source.exists():
            shutil.copy2(source, media_dir / source.name)
    audio = base / "audio" / "main_voice.wav"
    if audio.exists():
        shutil.copy2(audio, export_dir / "main_voice.wav")
    zip_path = base / "exports" / f"{safe_filename(project['name'])}_assets.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in export_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(export_dir))
    return {"download_url": public_url(zip_path)}
