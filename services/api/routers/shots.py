from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from services.store import load_db, save_db
from services.text_service import generate_shots

router = APIRouter(prefix="/api/projects", tags=["shots"])


@router.post("/{project_id}/shots")
def create_shots(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    db["shots"] = [s for s in db["shots"] if s["project_id"] != project_id]
    now = datetime.now().isoformat(timespec="seconds")
    shots = []
    for shot in generate_shots(project.get("rewritten_script") or project["raw_script"]):
        shot.update({"id": str(uuid4()), "project_id": project_id, "created_at": now, "updated_at": now})
        shots.append(shot)
    db["shots"].extend(shots)
    project["status"] = "shots_ready"
    project["updated_at"] = now
    save_db(db)
    return {"shots": shots}


@router.patch("/{project_id}/shots/{shot_id}")
def update_shot(project_id: str, shot_id: str, patch: dict):
    db = load_db()
    shot = next((s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id), None)
    if not shot:
        raise HTTPException(404, "Shot not found")
    allowed = {"voice_text", "duration_sec", "visual_need", "exact_keywords", "alternative_keywords", "atmosphere_keywords"}
    for key, value in patch.items():
        if key in allowed:
            shot[key] = value
    shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return {"shot": shot}
