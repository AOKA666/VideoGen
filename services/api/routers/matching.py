from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException

from services.store import load_db, save_db
from services.web_image_pipeline import mark_project_searching, reset_project_web_images, run_project_web_image_search

router = APIRouter(prefix="/api/projects", tags=["matching"])


@router.post("/{project_id}/match-assets")
def match_assets(project_id: str, background_tasks: BackgroundTasks):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    shots = [s for s in db["shots"] if s["project_id"] == project_id]
    reset_project_web_images(db, project_id)
    mark_project_searching(db, project_id)
    project["status"] = "searching_images"
    save_db(db)
    background_tasks.add_task(run_project_web_image_search, project_id)
    return {"project_id": project_id, "status": "searching_images", "count": len(shots)}


@router.patch("/{project_id}/shots/{shot_id}/asset")
def select_asset(project_id: str, shot_id: str, payload: dict):
    db = load_db()
    shot = next((s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id), None)
    if not shot:
        raise HTTPException(404, "Shot not found")
    asset_id = payload.get("asset_id")
    if asset_id and not any(a["id"] == asset_id for a in db["assets"]) and not any(a["id"] == asset_id for a in db.get("generated_assets", [])):
        raise HTTPException(404, "Asset not found")
    shot["selected_asset_id"] = asset_id
    shot["asset_source"] = payload.get("asset_source", "web_search")
    if not asset_id:
        shot["status"] = "no_match"
    for candidate in db.get("project_assets", []):
        if candidate.get("project_id") == project_id and candidate.get("shot_id") == shot_id:
            candidate["is_selected"] = candidate.get("asset_id") == asset_id
    save_db(db)
    return {"shot": shot}
