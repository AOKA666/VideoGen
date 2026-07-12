from __future__ import annotations

from datetime import datetime
import threading
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from services.generation_service import generate_doubao_image
from services.material_library_service import apply_material_intent
from services.store import load_db, project_dir, public_url, save_db
from services.text_service import ai_generate_shot_visuals, generate_shots
from services.web_image_pipeline import mark_project_searching, request_stop_project_search, reset_project_web_images, run_project_web_image_search

router = APIRouter(prefix="/api/projects", tags=["shots"])
REANALYZE_SAVE_LOCK = threading.Lock()


def _generate_ai_images(project_id: str, run_id: str, shots: list[dict]) -> None:
    """Generate and select one AI image for every newly created shot."""
    total = len(shots)
    failed = 0
    for completed, generated_shot in enumerate(shots, start=1):
        shot_id = generated_shot["id"]
        out = project_dir(project_id) / "images" / f"shot_{generated_shot['shot_index']:03d}.png"
        try:
            result = generate_doubao_image(out, generated_shot, "1:1")
        except Exception as exc:
            failed += 1
            db = load_db()
            project = next((p for p in db["projects"] if p["id"] == project_id), None)
            shot = next((s for s in db["shots"] if s.get("id") == shot_id), None)
            if not project or project.get("shot_generation_run_id") != run_id:
                return
            if shot:
                shot["status"] = "no_image"
                shot["image_generation_error"] = str(exc)[:500]
                shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
            project["search_error"] = f"{failed} 个分镜 AI 图片生成失败"
        else:
            db = load_db()
            project = next((p for p in db["projects"] if p["id"] == project_id), None)
            shot = next((s for s in db["shots"] if s.get("id") == shot_id), None)
            if not project or project.get("shot_generation_run_id") != run_id:
                return
            if shot:
                generated_id = str(uuid4())
                db.setdefault("generated_assets", []).append({
                    "id": generated_id,
                    "project_id": project_id,
                    "shot_id": shot_id,
                    "type": "image",
                    "prompt": result["prompt"],
                    "provider": result.get("provider"),
                    "model": result.get("model"),
                    "image_size": result.get("image_size"),
                    "person_gender": result.get("person_gender"),
                    "remote_url": result.get("remote_url"),
                    "seed": result.get("seed"),
                    "file_url": public_url(out),
                    "local_path": str(out),
                    "status": "success",
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                })
                shot["selected_asset_id"] = generated_id
                shot["asset_source"] = "ai_generated"
                shot["status"] = "ai_generated"
                shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
        project["search_stage"] = "generating_ai_images"
        project["search_completed"] = completed
        project["search_total"] = total
        project["current_shot_index"] = generated_shot.get("shot_index")
        project["current_search_keyword"] = f"正在生成 AI 图片：{completed}/{total}"
        project["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_db(db)

    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if project and project.get("shot_generation_run_id") == run_id:
        project["status"] = "shots_ready"
        project["search_stage"] = "done"
        project["search_completed"] = total
        project["search_total"] = total
        project["current_shot_index"] = None
        project["current_search_keyword"] = ""
        project["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_db(db)


def _generate_project_shots(project_id: str, run_id: str, image_search_provider: str, material_source_strategy: str) -> None:
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project or project.get("shot_generation_run_id") != run_id:
        return
    script = project.get("rewritten_script") or project["raw_script"]
    try:
        generated = generate_shots(script)
    except Exception as exc:
        db = load_db()
        project = next((p for p in db["projects"] if p["id"] == project_id), None)
        if project and project.get("shot_generation_run_id") == run_id:
            project.update({
                "status": "shot_generation_failed",
                "search_stage": "shot_generation_failed",
                "search_error": str(exc)[:500],
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            })
            save_db(db)
        return
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project or project.get("shot_generation_run_id") != run_id:
        return
    now = datetime.now().isoformat(timespec="seconds")
    for shot in generated:
        shot.update({"id": str(uuid4()), "project_id": project_id, "created_at": now, "updated_at": now})
        apply_material_intent(shot)
        db["shots"].append(shot)
    project["status"] = "searching_images"
    project["image_search_provider"] = image_search_provider
    project["material_source_strategy"] = material_source_strategy
    project["updated_at"] = now
    if material_source_strategy != "ai_only":
        mark_project_searching(db, project_id)
    else:
        project["search_stage"] = "generating_ai_images"
        project["search_total"] = len(generated)
        project["search_completed"] = 0
        project["current_search_keyword"] = "准备生成 AI 图片..."
    save_db(db)
    if material_source_strategy == "ai_only":
        _generate_ai_images(project_id, run_id, generated)
    else:
        run_project_web_image_search(project_id)


@router.post("/{project_id}/shots")
def create_shots(
    project_id: str,
    background_tasks: BackgroundTasks,
    image_search_provider: str = Query("so", pattern="^(so|tencent)$"),
    material_source_strategy: str = Query(
        "library_first",
        pattern="^(library_first|library_only|web_only|ai_only)$",
    ),
):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    request_stop_project_search(db, project_id)
    db["shots"] = [s for s in db["shots"] if s["project_id"] != project_id]
    db["project_assets"] = [
        item for item in db.get("project_assets", [])
        if item.get("project_id") != project_id
    ]
    reset_project_web_images(db, project_id)
    now = datetime.now().isoformat(timespec="seconds")
    run_id = str(uuid4())
    project["shot_generation_run_id"] = run_id
    project["status"] = "generating_shots"
    project["search_stage"] = "generating_shots"
    project["search_error"] = ""
    project["search_stop_requested"] = False
    project["image_search_provider"] = image_search_provider
    project["material_source_strategy"] = material_source_strategy
    project["updated_at"] = now
    save_db(db)
    background_tasks.add_task(_generate_project_shots, project_id, run_id, image_search_provider, material_source_strategy)
    return {"shots": [], "status": "generating_shots", "run_id": run_id}


@router.patch("/{project_id}/shots/{shot_id}")
def update_shot(project_id: str, shot_id: str, patch: dict):
    db = load_db()
    shot = next((s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id), None)
    if not shot:
        raise HTTPException(404, "Shot not found")
    allowed = {
        "voice_text", "duration_sec", "visual_need", "person_gender",
        "person_names", "person_description",
        "required_object", "required_scene", "search_keywords",
    }
    for key, value in patch.items():
        if key in allowed:
            shot[key] = value
    shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return {"shot": shot}


@router.post("/{project_id}/shots/{shot_id}/reanalyze-image")
def reanalyze_shot_image(project_id: str, shot_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    shot = next((s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    if not shot:
        raise HTTPException(404, "Shot not found")

    full_script = project.get("rewritten_script") or project.get("raw_script") or ""
    result = ai_generate_shot_visuals([shot], full_script).get(str(shot.get("shot_index")))
    if not result:
        raise HTTPException(502, "MiniMax did not return image analysis")

    # MiniMax calls run concurrently. Serialize only the short read-modify-write
    # section so one completed recognition cannot overwrite another one's result.
    with REANALYZE_SAVE_LOCK:
        db = load_db()
        current_shot = next(
            (s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id),
            None,
        )
        if not current_shot:
            raise HTTPException(404, "Shot not found")
        for key in (
            "visual_need", "person_gender", "person_names", "person_description",
            "search_keywords", "object_tags", "scene_tags", "keywords",
        ):
            if key in result:
                current_shot[key] = result[key]
        current_shot["required_object"] = list(current_shot.get("object_tags") or [])
        current_shot["required_scene"] = list(current_shot.get("scene_tags") or [])
        apply_material_intent(current_shot)
        current_shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_db(db)
        return {"shot": current_shot}
