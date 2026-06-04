from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.store import PROJECTS_DIR, load_db, project_dir, save_db
from services.text_service import RewriteQualityError, infer_title, rewrite_script

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str | None = None
    raw_script: str
    content_type: str = "国之脊梁"
    script_style: str = "纪实故事型"
    voice_style: str = "沉稳男声"
    video_ratio: str = "9:16"


class ScriptUpdate(BaseModel):
    rewritten_script: str


@router.get("")
def list_projects():
    return {"projects": load_db()["projects"]}


@router.post("")
def create_project(payload: ProjectCreate):
    db = load_db()
    now = datetime.now().isoformat(timespec="seconds")
    project_id = str(uuid4())
    project = {
        "id": project_id,
        "name": payload.name or infer_title(payload.raw_script),
        "raw_script": payload.raw_script,
        "rewritten_script": "",
        "content_type": payload.content_type,
        "script_style": payload.script_style,
        "voice_style": payload.voice_style,
        "video_ratio": payload.video_ratio,
        "status": "created",
        "created_at": now,
        "updated_at": now,
    }
    db["projects"].append(project)
    save_db(db)
    project_dir(project_id)
    return {"project_id": project_id, "status": "created", "project": project}


@router.get("/{project_id}")
def get_project(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    shots = [s for s in db["shots"] if s["project_id"] == project_id]
    generated_assets = [
        a for a in db.get("generated_assets", [])
        if a.get("project_id") == project_id
        and (not a.get("local_path") or Path(str(a.get("local_path"))).exists())
    ]
    return {
        "project": project,
        "shots": sorted(shots, key=lambda s: s["shot_index"]),
        "generated_assets": generated_assets,
    }


@router.post("/{project_id}/rewrite")
def rewrite(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    try:
        result = rewrite_script(project["raw_script"], project.get("script_style", "纪实故事型"))
    except RewriteQualityError as exc:
        detail = exc.result.get("rewrite_error") or str(exc)
        raise HTTPException(422, detail) from exc
    project["rewritten_script"] = result["rewritten_script"]
    project["rewrite_provider"] = result.get("rewrite_provider", "")
    project["rewrite_error"] = result.get("rewrite_error", "")
    project["rewrite_comparison"] = result.get("rewrite_comparison", {})
    project["rewrite_difference"] = result.get("rewrite_difference", 0)
    project["rewrite_attempts"] = result.get("rewrite_attempts", 1)
    project["status"] = "script_ready"
    project["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return result


@router.patch("/{project_id}/script")
def update_script(project_id: str, payload: ScriptUpdate):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    project["rewritten_script"] = payload.rewritten_script
    project["status"] = "script_ready"
    project["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return {"status": "saved"}


@router.delete("/{project_id}")
def delete_project(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")

    db["projects"] = [p for p in db["projects"] if p["id"] != project_id]
    db["shots"] = [s for s in db["shots"] if s.get("project_id") != project_id]
    db["project_assets"] = [pa for pa in db.get("project_assets", []) if pa.get("project_id") != project_id]
    db["generated_assets"] = [ga for ga in db.get("generated_assets", []) if ga.get("project_id") != project_id]
    save_db(db)

    target = (PROJECTS_DIR / project_id).resolve()
    if target.exists() and PROJECTS_DIR.resolve() in target.parents:
        shutil.rmtree(target)

    return {"status": "deleted", "project_id": project_id}
