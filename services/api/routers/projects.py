from __future__ import annotations

import shutil
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.store import db_write_transaction, load_db, project_dir, projects_dir, save_db
from services.text_service import infer_title

router = APIRouter(prefix="/api/projects", tags=["projects"])
PRODUCT_MARKER = "【带货开始】"


class ProjectCreate(BaseModel):
    name: str | None = None
    raw_script: str
    workflow_type: str = "video_library"
    background_template_id: str = "daodejing"
    voice_type: str = "S_6Sd6jOE42"
    speech_rate: int = 0
    video_ratio: str = "9:16"


class BulkDeletePayload(BaseModel):
    project_ids: list[str] = []


class ScriptUpdate(BaseModel):
    name: str | None = None
    title_line1: str | None = None
    title_line2: str | None = None
    archived: bool | None = None


@router.get("")
def list_projects():
    db = load_db(copy_data=False)
    shot_counts: dict[str, int] = {}
    for shot in db.get("shots", []):
        project_id = str(shot.get("project_id") or "")
        shot_counts[project_id] = shot_counts.get(project_id, 0) + 1
    projects = []
    for project in db["projects"]:
        item = dict(project)
        item["shot_count"] = shot_counts.get(str(project.get("id")), 0)
        item["archived"] = bool(project.get("archived", False))
        item["has_export"] = (
            projects_dir() / str(project.get("id")) / "exports" / "package" / "export_verification.json"
        ).exists()
        projects.append(item)
    return {"projects": projects}


@router.post("")
def create_project(payload: ProjectCreate):
    if payload.workflow_type != "video_library":
        raise HTTPException(400, "仅支持新建 video_library 项目")
    if PRODUCT_MARKER not in payload.raw_script:
        raise HTTPException(400, f"文案中缺少{PRODUCT_MARKER}标记，请先插入后再生成视频草稿")
    db = load_db()
    now = datetime.now().isoformat(timespec="seconds")
    project_id = str(uuid4())
    project = {
        "id": project_id,
        "name": payload.name or infer_title(payload.raw_script),
        "raw_script": payload.raw_script,
        "workflow_type": "video_library",
        "background_template_id": payload.background_template_id,
        "voice_type": payload.voice_type,
        "speech_rate": payload.speech_rate,
        "video_ratio": payload.video_ratio,
        "status": "created",
        "archived": False,
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
    return {
        "project": project,
        "shots": sorted(shots, key=lambda s: s["shot_index"]),
    }


@router.patch("/{project_id}/script")
def update_script(project_id: str, payload: ScriptUpdate):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    if project.get("workflow_type") != "video_library":
        if payload.name is not None or payload.title_line1 is not None or payload.title_line2 is not None:
            raise HTTPException(400, "旧项目已停用，仅支持归档或删除")
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(400, "Project name must not be empty")
        if len(name) > 80:
            raise HTTPException(400, "Project name must not exceed 80 characters")
        project["name"] = name
    if payload.title_line1 is not None:
        project["title_line1"] = payload.title_line1
    if payload.title_line2 is not None:
        project["title_line2"] = payload.title_line2
    if payload.title_line1 is not None or payload.title_line2 is not None:
        project["title_full"] = f"{payload.title_line1 or ''} {payload.title_line2 or ''}"
    if payload.archived is not None:
        project["archived"] = payload.archived
    project["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return {"status": "saved"}


@router.post("/bulk-delete")
def bulk_delete_projects(payload: BulkDeletePayload):
    ids = list(dict.fromkeys(
        str(item).strip() for item in payload.project_ids if str(item).strip()
    ))
    if not ids:
        raise HTTPException(400, "请选择要删除的项目")

    skipped: list[dict[str, str]] = []
    deleted_ids: list[str] = []
    with db_write_transaction():
        db = load_db()
        by_id = {project["id"]: project for project in db["projects"]}
        for project_id in ids:
            project = by_id.get(project_id)
            if not project:
                skipped.append({"project_id": project_id, "reason": "项目不存在"})
            elif (
                project.get("workflow_type") == "video_library"
                and project.get("status") in {"video_generating", "video_rendering"}
            ):
                skipped.append({"project_id": project_id, "reason": "视频正在处理中"})
            else:
                deleted_ids.append(project_id)
        if deleted_ids:
            deleted_set = set(deleted_ids)
            db["projects"] = [p for p in db["projects"] if p["id"] not in deleted_set]
            db["shots"] = [s for s in db["shots"] if s.get("project_id") not in deleted_set]
            save_db(db)

    project_root = projects_dir().resolve()
    for project_id in deleted_ids:
        target = (project_root / project_id).resolve()
        if target.exists() and project_root in target.parents:
            shutil.rmtree(target)
    return {"status": "deleted", "deleted_ids": deleted_ids, "skipped": skipped}


@router.delete("/{project_id}")
def delete_project(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")

    if (
        project.get("workflow_type") == "video_library"
        and project.get("status") in {"video_generating", "video_rendering"}
    ):
        raise HTTPException(409, "视频正在处理中，请完成后再删除项目")

    db["projects"] = [p for p in db["projects"] if p["id"] != project_id]
    db["shots"] = [s for s in db["shots"] if s["project_id"] != project_id]
    save_db(db)

    project_root = projects_dir().resolve()
    target = (project_root / project_id).resolve()
    if target.exists() and project_root in target.parents:
        shutil.rmtree(target)

    return {"status": "deleted", "project_id": project_id}
