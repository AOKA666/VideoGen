from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from services.store import load_db, save_db
from services.text_service import generate_shots
from services.web_image_pipeline import mark_project_searching, reset_project_web_images, run_project_web_image_search

router = APIRouter(prefix="/api/projects", tags=["shots"])


@router.post("/{project_id}/shots")
def create_shots(
    project_id: str,
    background_tasks: BackgroundTasks,
    image_search_provider: str = Query("so", pattern="^(so|tencent)$"),
):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    db["shots"] = [s for s in db["shots"] if s["project_id"] != project_id]
    reset_project_web_images(db, project_id)
    now = datetime.now().isoformat(timespec="seconds")
    shots = []
    script = project.get("rewritten_script") or project["raw_script"]
    for shot in generate_shots(script):
        shot.update({"id": str(uuid4()), "project_id": project_id, "created_at": now, "updated_at": now})
        shots.append(shot)
    db["shots"].extend(shots)
    mark_project_searching(db, project_id)
    project["status"] = "searching_images"
    project["image_search_provider"] = image_search_provider
    project["updated_at"] = now
    save_db(db)
    background_tasks.add_task(run_project_web_image_search, project_id)
    return {"shots": sorted(shots, key=lambda s: s["shot_index"]), "status": "searching_images"}


@router.patch("/{project_id}/shots/{shot_id}")
def update_shot(project_id: str, shot_id: str, patch: dict):
    db = load_db()
    shot = next((s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id), None)
    if not shot:
        raise HTTPException(404, "Shot not found")
    allowed = {"voice_text", "duration_sec", "visual_need", "required_object", "required_scene", "search_keywords"}
    for key, value in patch.items():
        if key in allowed:
            shot[key] = value
    shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return {"shot": shot}
