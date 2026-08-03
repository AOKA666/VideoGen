from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import os
import threading
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from services.generation_service import build_image_prompt, generate_doubao_image
from services.material_library_service import apply_material_intent
from services.store import load_db, project_dir, public_url, save_db
from services.text_service import ai_generate_shot_visuals, generate_shots

router = APIRouter(prefix="/api/projects", tags=["shots"])
REANALYZE_SAVE_LOCK = threading.Lock()
DEFAULT_AI_IMAGE_CONCURRENCY = 3
MAX_AI_IMAGE_CONCURRENCY = 8


def ai_image_concurrency(total: int) -> int:
    try:
        configured = int(os.getenv("AI_IMAGE_CONCURRENCY", str(DEFAULT_AI_IMAGE_CONCURRENCY)))
    except ValueError:
        configured = DEFAULT_AI_IMAGE_CONCURRENCY
    return max(1, min(configured, MAX_AI_IMAGE_CONCURRENCY, max(1, total)))


def _generate_one_ai_image(project_id: str, generated_shot: dict) -> tuple[dict, object, dict]:
    out = project_dir(project_id) / "images" / f"shot_{generated_shot['shot_index']:03d}.png"
    result = generate_doubao_image(out, generated_shot, "9:16")
    return generated_shot, out, result


def _generate_ai_images(project_id: str, run_id: str, shots: list[dict]) -> None:
    """Generate and select one AI image for every newly created shot."""
    total = len(shots)
    failed = 0
    completed = 0
    executor = ThreadPoolExecutor(
        max_workers=ai_image_concurrency(total),
        thread_name_prefix=f"ai-image-{project_id[:8]}",
    )
    futures = {
        executor.submit(_generate_one_ai_image, project_id, generated_shot): generated_shot
        for generated_shot in shots
    }
    try:
        completed_futures = as_completed(futures)
        for future in completed_futures:
            generated_shot = futures[future]
            completed += 1
            shot_id = generated_shot["id"]
            result = None
            out = None
            generation_error = None
            try:
                _, out, result = future.result()
            except Exception as exc:
                generation_error = exc

            # Network/image work runs concurrently. Database mutations stay on
            # this coordinator thread so completed workers cannot overwrite one
            # another's project progress or generated-assets records.
            db = load_db()
            project = next((p for p in db["projects"] if p["id"] == project_id), None)
            shot = next((s for s in db["shots"] if s.get("id") == shot_id), None)
            if not project or project.get("shot_generation_run_id") != run_id:
                for pending in futures:
                    pending.cancel()
                return
            if generation_error is not None:
                failed += 1
                if shot:
                    shot["status"] = "no_image"
                    shot["image_generation_error"] = str(generation_error)[:500]
                    shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
                project["search_error"] = f"{failed} 个分镜 AI 图片生成失败"
            elif shot and result is not None and out is not None:
                generated_id = str(uuid4())
                db.setdefault("generated_assets", []).append({
                    "id": generated_id,
                    "project_id": project_id,
                    "shot_id": shot_id,
                    "type": "image",
                    "asset_source": "ai_generated",
                    "prompt": result["prompt"],
                    "provider": result.get("provider"),
                    "model": result.get("model"),
                    "image_size": result.get("image_size"),
                    "remote_url": result.get("remote_url"),
                    "seed": result.get("seed"),
                    "file_url": public_url(out),
                    "local_path": str(out),
                    "status": "success",
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                })
                shot["selected_asset_id"] = generated_id
                shot["asset_source"] = "ai_generated"
                shot["image_prompt"] = result["prompt"]
                shot["status"] = "ai_generated"
                shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
            project["generation_stage"] = "generating_ai_images"
            project["generation_completed"] = completed
            project["generation_total"] = total
            project["current_shot_index"] = generated_shot.get("shot_index")
            project["current_generation_message"] = f"正在并发生成 AI 图片：{completed}/{total}"
            project["ai_image_concurrency"] = ai_image_concurrency(total)
            project["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_db(db)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if project and project.get("shot_generation_run_id") == run_id:
        project["status"] = "shots_ready"
        project["generation_stage"] = "done"
        project["generation_completed"] = total
        project["generation_total"] = total
        project["current_shot_index"] = None
        project["current_generation_message"] = ""
        project["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_db(db)


def _save_image_prompts(project: dict, shots: list[dict], now: str) -> None:
    """Persist image prompts without invoking an image-generation provider."""
    for shot in shots:
        shot["image_prompt"] = build_image_prompt(shot)
        shot["status"] = "prompt_ready"
        shot["updated_at"] = now
    project["status"] = "shots_ready"
    project["generation_stage"] = "done"
    project["generation_total"] = len(shots)
    project["generation_completed"] = len(shots)
    project["current_shot_index"] = None
    project["current_generation_message"] = ""


def _generate_project_shots(
    project_id: str,
    run_id: str,
    material_source_strategy: str,
    storyboard_model_provider: str = "deepseek",
) -> None:
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project or project.get("shot_generation_run_id") != run_id:
        return
    script = project.get("rewritten_script") or project["raw_script"]
    try:
        generated = generate_shots(script, model_provider=storyboard_model_provider)
    except Exception as exc:
        db = load_db()
        project = next((p for p in db["projects"] if p["id"] == project_id), None)
        if project and project.get("shot_generation_run_id") == run_id:
            project.update({
                "status": "shot_generation_failed",
                "generation_stage": "shot_generation_failed",
                "generation_error": str(exc)[:500],
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
    project["material_source_strategy"] = material_source_strategy
    project["updated_at"] = now
    if material_source_strategy == "prompt_only":
        _save_image_prompts(project, generated, now)
    else:
        project["status"] = "generating_images"
        project["generation_stage"] = "generating_ai_images"
        project["generation_total"] = len(generated)
        project["generation_completed"] = 0
        project["current_generation_message"] = "准备生成 AI 图片..."
    save_db(db)
    if material_source_strategy == "ai_only":
        _generate_ai_images(project_id, run_id, generated)


@router.post("/{project_id}/shots")
def create_shots(
    project_id: str,
    background_tasks: BackgroundTasks,
    material_source_strategy: str = Query(
        "ai_only",
        pattern="^(ai_only|prompt_only)$",
    ),
    storyboard_model_provider: str = Query(
        "deepseek",
        pattern="^(minimax|deepseek|openai)$",
    ),
):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    db["shots"] = [s for s in db["shots"] if s["project_id"] != project_id]
    db["project_assets"] = [
        item for item in db.get("project_assets", [])
        if item.get("project_id") != project_id
    ]
    db["generated_assets"] = [
        item for item in db.get("generated_assets", [])
        if item.get("project_id") != project_id
    ]
    now = datetime.now().isoformat(timespec="seconds")
    run_id = str(uuid4())
    project["shot_generation_run_id"] = run_id
    project["status"] = "generating_shots"
    project["generation_stage"] = "generating_shots"
    project["generation_error"] = ""
    project["generation_completed"] = 0
    project["generation_total"] = 0
    project["current_generation_message"] = "正在生成分镜提示词..."
    project["material_source_strategy"] = material_source_strategy
    project["storyboard_model_provider"] = storyboard_model_provider
    project["updated_at"] = now
    save_db(db)
    background_tasks.add_task(
        _generate_project_shots,
        project_id,
        run_id,
        material_source_strategy,
        storyboard_model_provider,
    )
    return {
        "shots": [],
        "status": "generating_shots",
        "run_id": run_id,
        "storyboard_model_provider": storyboard_model_provider,
    }


@router.patch("/{project_id}/shots/{shot_id}")
def update_shot(project_id: str, shot_id: str, patch: dict):
    db = load_db()
    shot = next((s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id), None)
    if not shot:
        raise HTTPException(404, "Shot not found")
    allowed = {
        "voice_text", "duration_sec", "visual_need",
        "required_object", "required_scene",
    }
    for key, value in patch.items():
        if key in allowed:
            shot[key] = value
    shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return {"shot": shot}


@router.post("/{project_id}/shots/{shot_id}/regenerate-image-prompt")
def regenerate_shot_image_prompt(
    project_id: str,
    shot_id: str,
    storyboard_model_provider: str | None = Query(
        None,
        pattern="^(minimax|deepseek|openai)$",
    ),
):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    shot = next((s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    if not shot:
        raise HTTPException(404, "Shot not found")

    full_script = project.get("rewritten_script") or project.get("raw_script") or ""
    model_provider = str(
        storyboard_model_provider
        or project.get("storyboard_model_provider")
        or "deepseek"
    )
    project["storyboard_model_provider"] = model_provider
    result = ai_generate_shot_visuals(
        [shot],
        full_script,
        model_provider=model_provider,
    ).get(str(shot.get("shot_index")))
    if not result:
        raise HTTPException(502, f"{model_provider} did not return an image prompt")

    # Prompt generation calls can run concurrently. Serialize only the short
    # read-modify-write section so completed requests cannot overwrite each other.
    with REANALYZE_SAVE_LOCK:
        db = load_db()
        current_project = next((p for p in db["projects"] if p["id"] == project_id), None)
        current_shot = next(
            (s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id),
            None,
        )
        if current_project:
            current_project["storyboard_model_provider"] = model_provider
        if not current_shot:
            raise HTTPException(404, "Shot not found")
        for key in (
            "visual_need", "object_tags", "scene_tags", "keywords",
        ):
            if key in result:
                current_shot[key] = result[key]
        current_shot["required_object"] = list(current_shot.get("object_tags") or [])
        current_shot["required_scene"] = list(current_shot.get("scene_tags") or [])
        apply_material_intent(current_shot)
        current_shot["image_prompt"] = build_image_prompt(current_shot)
        if not current_shot.get("selected_asset_id"):
            current_shot["status"] = "prompt_ready"
        current_shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_db(db)
        return {"shot": current_shot}
