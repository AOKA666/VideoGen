from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from PIL import Image

from services.asset_service import new_id
from services.material_library_service import analyze_archived_assets, archive_project_images
from services.store import load_db, project_dir, public_url, save_db

router = APIRouter(prefix="/api/projects", tags=["matching"])
MANUAL_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@router.post("/{project_id}/archive-selected-images")
def archive_selected_images(project_id: str, background_tasks: BackgroundTasks):
    db = load_db()
    if not any(item.get("id") == project_id for item in db.get("projects", [])):
        raise HTTPException(404, "Project not found")
    if not db.get("asset_library"):
        raise HTTPException(400, "Please choose an asset library folder first")
    result = archive_project_images(db, project_id)
    save_db(db)
    if result["analyze_ids"]:
        background_tasks.add_task(analyze_archived_assets, result["analyze_ids"])
    return {
        "status": "analyzing" if result["analyzing"] else "completed",
        **result,
    }


@router.patch("/{project_id}/shots/{shot_id}/asset")
def select_asset(project_id: str, shot_id: str, payload: dict):
    db = load_db()
    shot = next((s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id), None)
    if not shot:
        raise HTTPException(404, "Shot not found")
    asset_id = payload.get("asset_id")
    library_asset = next((a for a in db["assets"] if a["id"] == asset_id), None)
    generated_asset = next((a for a in db.get("generated_assets", []) if a["id"] == asset_id), None)
    if asset_id and not library_asset and not generated_asset:
        raise HTTPException(404, "Asset not found")
    asset_source = payload.get("asset_source", "ai_generated")
    if asset_source == "library_upload" and (
        not library_asset
        or library_asset.get("file_type") != "image"
        or library_asset.get("is_available") is False
    ):
        raise HTTPException(400, "Please choose an available image from the asset library")
    shot["selected_asset_id"] = asset_id
    shot["asset_source"] = asset_source
    if not asset_id:
        shot["status"] = "no_match"
    elif asset_source == "library_upload":
        shot["status"] = "uploaded"
    elif library_asset:
        shot["status"] = "matched"
    for candidate in db.get("project_assets", []):
        if candidate.get("project_id") == project_id and candidate.get("shot_id") == shot_id:
            candidate["is_selected"] = candidate.get("asset_id") == asset_id
    save_db(db)
    return {"shot": shot}


@router.post("/{project_id}/shots/{shot_id}/manual-image")
def upload_manual_shot_image(project_id: str, shot_id: str, file: UploadFile = File(...)):
    db = load_db()
    shot = next(
        (item for item in db["shots"] if item["project_id"] == project_id and item["id"] == shot_id),
        None,
    )
    if not shot:
        raise HTTPException(404, "Shot not found")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in MANUAL_IMAGE_EXTS:
        raise HTTPException(400, "Only JPG, PNG and WebP images are supported")

    asset_id = new_id()
    output_dir = project_dir(project_id) / "images" / f"shot_{shot['shot_index']:03d}"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"manual_{asset_id}{suffix}"
    try:
        with path.open("wb") as target:
            shutil.copyfileobj(file.file, target)
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"Invalid image file: {exc}") from exc

    now = datetime.now().isoformat(timespec="seconds")
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    asset = {
        "id": asset_id,
        "project_id": project_id,
        "shot_id": shot_id,
        "type": "image",
        "file_type": "image",
        "file_name": file.filename or path.name,
        "asset_source": "manual_upload",
        "provider": "baidu_manual_download",
        "width": width,
        "height": height,
        "file_size": path.stat().st_size,
        "hash": content_hash,
        "file_url": public_url(path),
        "local_path": str(path),
        "status": "success",
        "created_at": now,
        "updated_at": now,
    }
    db.setdefault("generated_assets", []).append(asset)
    db.setdefault("project_assets", []).append({
        "project_id": project_id,
        "shot_id": shot_id,
        "asset_id": asset_id,
        "asset_source": "manual_upload",
        "match_score": 100,
        "match_reason": "用户手动上传图片",
        "is_selected": True,
        "created_at": now,
    })
    for candidate in db.get("project_assets", []):
        if candidate.get("project_id") == project_id and candidate.get("shot_id") == shot_id:
            candidate["is_selected"] = candidate.get("asset_id") == asset_id
    shot["selected_asset_id"] = asset_id
    shot["asset_source"] = "manual_upload"
    shot["status"] = "uploaded"
    shot["downloaded_image_count"] = len([
        item for item in db.get("generated_assets", []) if item.get("shot_id") == shot_id
    ])
    shot["updated_at"] = now
    save_db(db)
    return {"status": "success", "asset": asset, "shot": shot}
