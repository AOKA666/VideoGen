from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from services.asset_service import analyze_asset, detect_file_type, new_id, safe_storage_name
from services.store import ASSETS_DIR, load_db, public_url, save_db

router = APIRouter(prefix="/api/assets", tags=["assets"])


class LibraryConfig(BaseModel):
    name: str
    path_hint: str | None = None


def is_valid_library(library: dict | None) -> bool:
    if not library:
        return False
    name = str(library.get("name") or "").strip()
    return bool(name) and set(name) != {"?"}


def merge_manual_tags(tags: dict, manual_tags: dict | None) -> dict:
    if not manual_tags:
        return tags
    merged = dict(tags)
    for key in ["object", "scene", "keywords"]:
        value = manual_tags.get(key)
        if isinstance(value, list) and value:
            merged[key] = value
    return merged


def asset_objects(asset: dict) -> list[str]:
    return asset.get("object") or asset.get("people") or []


def rename_analyzed_asset(asset: dict) -> None:
    local_path = Path(asset.get("local_path", ""))
    if not local_path.exists() or ASSETS_DIR.resolve() not in local_path.resolve().parents:
        return
    subject = (asset_objects(asset) or ["object"])[0]
    safe_subject = safe_storage_name(subject).replace(" ", "_") or "object"
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    ext = local_path.suffix or Path(asset.get("original_path", "")).suffix or ".asset"
    target = ASSETS_DIR / f"{safe_subject}-{timestamp}{ext}"
    counter = 1
    while target.exists():
        target = ASSETS_DIR / f"{safe_subject}-{timestamp}-{counter}{ext}"
        counter += 1
    local_path.rename(target)
    asset["file_name"] = target.name
    asset["file_url"] = public_url(target)
    asset["thumbnail_url"] = public_url(target)
    asset["local_path"] = str(target)


@router.get("/library")
def get_library():
    library = load_db().get("asset_library")
    return {"library": library if is_valid_library(library) else None}


@router.post("/library")
def set_library(payload: LibraryConfig):
    db = load_db()
    now = datetime.now().isoformat(timespec="seconds")
    existing = db.get("asset_library") if is_valid_library(db.get("asset_library")) else None
    name = payload.name.strip() or "默认素材库"
    library = {
        "id": existing.get("id") if existing else str(uuid4()),
        "name": name,
        "path_hint": payload.path_hint or name,
        "storage_dir": str(ASSETS_DIR),
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
    }
    db["asset_library"] = library
    save_db(db)
    return {"library": library}


@router.get("")
def list_assets(q: str = ""):
    assets = load_db()["assets"]
    if q:
        needle = q.lower()
        assets = [
            a for a in assets
            if needle in a["file_name"].lower()
            or needle in (a.get("original_path") or "").lower()
            or needle in " ".join(a.get("keywords", []) + asset_objects(a) + a.get("scene", [])).lower()
        ]
    return {"assets": assets}


def require_library(db: dict) -> dict:
    library = db.get("asset_library")
    if not is_valid_library(library):
        raise HTTPException(400, "Please choose an asset library folder first")
    return library


def build_asset(file: UploadFile, source_note: str, copyright_note: str, library: dict) -> dict:
    original_path = file.filename or ""
    file_type = detect_file_type(original_path)
    if file_type == "unknown":
        raise HTTPException(400, f"Unsupported file type: {original_path}")

    asset_id = new_id()
    stored_name = f"{asset_id}_{safe_storage_name(original_path)}"
    target = ASSETS_DIR / stored_name
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    now = datetime.now().isoformat(timespec="seconds")
    return {
        "id": asset_id,
        "library_id": library["id"],
        "file_name": safe_storage_name(original_path),
        "original_path": original_path,
        "file_type": file_type,
        "file_url": public_url(target),
        "thumbnail_url": public_url(target),
        "local_path": str(target),
        "object": [],
        "scene": [],
        "keywords": [],
        "analysis_status": "analyzing",
        "analysis_provider": "pending",
        "analysis_error": "",
        "source_note": source_note,
        "copyright_note": copyright_note,
        "is_available": True,
        "created_at": now,
        "updated_at": now,
    }


def analyze_asset_background(asset_id: str, manual_tags: dict | None) -> None:
    db = load_db()
    asset = next((a for a in db["assets"] if a["id"] == asset_id), None)
    if not asset:
        return
    local_path = asset.get("local_path")
    tags = analyze_asset(
        asset.get("original_path") or asset["file_name"],
        Path(local_path) if local_path else None,
        asset.get("file_type"),
    )
    tags = merge_manual_tags(tags, manual_tags)
    for key in ["era", "emotion", "visual_style"]:
        asset.pop(key, None)
    asset.pop("people", None)
    asset.update(tags)
    rename_analyzed_asset(asset)
    asset["analysis_status"] = "ready" if not tags.get("analysis_error") else "failed"
    asset["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)


@router.post("/upload")
def upload_assets(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    source_note: str = Form("用户上传"),
    copyright_note: str = Form("自用素材"),
    manual_tags: str = Form("{}"),
):
    db = load_db()
    library = require_library(db)
    try:
        parsed_manual_tags = json.loads(manual_tags or "{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "manual_tags must be a JSON object")

    uploaded = []
    skipped = []
    for file in files:
        if detect_file_type(file.filename or "") == "unknown":
            skipped.append({"file_name": file.filename, "reason": "unsupported"})
            continue
        asset = build_asset(file, source_note, copyright_note, library)
        db["assets"].append(asset)
        uploaded.append(asset)
        background_tasks.add_task(analyze_asset_background, asset["id"], parsed_manual_tags)
    save_db(db)
    return {"status": "analyzing", "count": len(uploaded), "skipped": skipped, "assets": uploaded}


@router.post("/{asset_id}/analyze")
def analyze(asset_id: str):
    db = load_db()
    asset = next((a for a in db["assets"] if a["id"] == asset_id), None)
    if not asset:
        raise HTTPException(404, "Asset not found")
    local_path = asset.get("local_path")
    tags = analyze_asset(asset.get("original_path") or asset["file_name"], Path(local_path) if local_path else None, asset.get("file_type"))
    for key in ["era", "emotion", "visual_style"]:
        asset.pop(key, None)
    asset.pop("people", None)
    asset.update(tags)
    rename_analyzed_asset(asset)
    asset["analysis_status"] = "ready" if not tags.get("analysis_error") else "failed"
    asset["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return {"asset_id": asset_id, **tags}


@router.patch("/{asset_id}")
def update_asset(asset_id: str, patch: dict):
    db = load_db()
    asset = next((a for a in db["assets"] if a["id"] == asset_id), None)
    if not asset:
        raise HTTPException(404, "Asset not found")
    if "people" in patch and "object" not in patch:
        patch["object"] = patch["people"]
    for key in ["object", "scene", "keywords", "source_note", "copyright_note", "is_available"]:
        if key in patch:
            asset[key] = patch[key]
    asset["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return {"asset": asset}


@router.delete("/{asset_id}")
def delete_asset(asset_id: str):
    db = load_db()
    asset = next((a for a in db["assets"] if a["id"] == asset_id), None)
    if not asset:
        raise HTTPException(404, "Asset not found")

    db["assets"] = [a for a in db["assets"] if a["id"] != asset_id]
    db["project_assets"] = [pa for pa in db.get("project_assets", []) if pa.get("asset_id") != asset_id]
    for shot in db.get("shots", []):
        if shot.get("selected_asset_id") == asset_id:
            shot["selected_asset_id"] = None
            shot["asset_source"] = None
            shot["match_score"] = 0
            shot["status"] = "no_match"
            shot["updated_at"] = datetime.now().isoformat(timespec="seconds")

    deleted_file = False
    local_path = Path(asset.get("local_path", ""))
    if local_path.exists() and ASSETS_DIR.resolve() in local_path.resolve().parents:
        local_path.unlink()
        deleted_file = True

    save_db(db)
    return {"status": "deleted", "asset_id": asset_id, "deleted_file": deleted_file}
