from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import os
import threading
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from services.generation_service import build_image_prompt, generate_ai_image, generated_image_extension
from services.store import db_write_transaction, load_db, project_dir, public_url, save_db
from services.text_service import ai_generate_shot_visuals, generate_shots

router = APIRouter(prefix="/api/projects", tags=["shots"])
REANALYZE_SAVE_LOCK = threading.Lock()
DEFAULT_AI_IMAGE_CONCURRENCY = 5
MAX_AI_IMAGE_CONCURRENCY = 8


def ai_image_concurrency(total: int) -> int:
    try:
        configured = int(os.getenv("AI_IMAGE_CONCURRENCY", str(DEFAULT_AI_IMAGE_CONCURRENCY)))
    except ValueError:
        configured = DEFAULT_AI_IMAGE_CONCURRENCY
    return max(1, min(configured, MAX_AI_IMAGE_CONCURRENCY, max(1, total)))


def _generate_one_ai_image(
    project_id: str,
    generated_shot: dict,
    image_generation_provider: str,
) -> tuple[dict, object, dict]:
    extension = generated_image_extension(image_generation_provider)
    out = project_dir(project_id) / "images" / f"shot_{generated_shot['shot_index']:03d}{extension}"
    result = generate_ai_image(out, generated_shot, "9:16", provider=image_generation_provider)
    return generated_shot, out, result


def _save_completed_ai_image(
    project_id: str,
    run_id: str,
    generated_shot: dict,
    result: dict | None,
    out: object | None,
    generation_error: Exception | None,
    completed: int,
    total: int,
    failed: int,
) -> bool:
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    shot_id = generated_shot["id"]
    shot = next((s for s in db["shots"] if s.get("id") == shot_id), None)
    if not project or project.get("shot_generation_run_id") != run_id:
        return False
    if generation_error is not None:
        if shot:
            shot["status"] = "no_image"
            shot["image_generation_error"] = str(generation_error)[:500]
            shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
        project["generation_error"] = f"{failed} 个分镜 AI 图片生成失败"
    elif shot and result is not None and out is not None:
        generated_id = str(uuid4())
        db.setdefault("generated_assets", []).append({
            "id": generated_id, "project_id": project_id, "shot_id": shot_id,
            "type": "image", "asset_source": "ai_generated",
            "prompt": result["prompt"], "provider": result.get("provider"),
            "model": result.get("model"), "image_size": result.get("image_size"),
            "remote_url": result.get("remote_url"), "seed": result.get("seed"),
            "file_url": public_url(out), "local_path": str(out), "status": "success",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        shot.update({
            "selected_asset_id": generated_id, "asset_source": "ai_generated",
            "image_prompt": result["prompt"], "status": "ai_generated",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
    project.update({
        "generation_stage": "generating_ai_images", "generation_completed": completed,
        "generation_total": total, "current_shot_index": generated_shot.get("shot_index"),
        "current_generation_message": f"正在并发生成 AI 图片：{completed}/{total}",
        "ai_image_concurrency": ai_image_concurrency(total),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    })
    save_db(db)
    return True


def _generate_ai_images(
    project_id: str,
    run_id: str,
    shots: list[dict],
    image_generation_provider: str = "seedream",
) -> None:
    """Generate concurrently while committing every result atomically."""
    total = len(shots)
    failed = 0
    executor = ThreadPoolExecutor(
        max_workers=ai_image_concurrency(total),
        thread_name_prefix=f"ai-image-{project_id[:8]}",
    )
    futures = {
        executor.submit(_generate_one_ai_image, project_id, shot, image_generation_provider): shot
        for shot in shots
    }
    try:
        for completed, future in enumerate(as_completed(futures), start=1):
            generated_shot = futures[future]
            result = out = generation_error = None
            try:
                _, out, result = future.result()
            except Exception as exc:
                generation_error = exc
                failed += 1
            with db_write_transaction():
                is_current_run = _save_completed_ai_image(
                    project_id, run_id, generated_shot, result, out,
                    generation_error, completed, total, failed,
                )
            if not is_current_run:
                for pending in futures:
                    pending.cancel()
                return
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    with db_write_transaction():
        db = load_db()
        project = next((p for p in db["projects"] if p["id"] == project_id), None)
        if project and project.get("shot_generation_run_id") == run_id:
            project.update({
                "status": "shots_ready", "generation_stage": "done",
                "generation_completed": total, "generation_total": total,
                "current_shot_index": None, "current_generation_message": "",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            })
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
    image_generation_provider: str = "seedream",
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
        db["shots"].append(shot)
    project["material_source_strategy"] = material_source_strategy
    project["image_generation_provider"] = image_generation_provider
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
        _generate_ai_images(project_id, run_id, generated, image_generation_provider)


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
    image_generation_provider: str = Query(
        "seedream",
        pattern="^(seedream|openai)$",
    ),
):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    db["shots"] = [s for s in db["shots"] if s["project_id"] != project_id]
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
    project["image_generation_provider"] = image_generation_provider
    project["updated_at"] = now
    save_db(db)
    background_tasks.add_task(
        _generate_project_shots,
        project_id,
        run_id,
        material_source_strategy,
        storyboard_model_provider,
        image_generation_provider,
    )
    return {
        "shots": [],
        "status": "generating_shots",
        "run_id": run_id,
        "storyboard_model_provider": storyboard_model_provider,
        "image_generation_provider": image_generation_provider,
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

    model_provider = str(
        storyboard_model_provider
        or project.get("storyboard_model_provider")
        or "deepseek"
    )
    project["storyboard_model_provider"] = model_provider
    result = ai_generate_shot_visuals(
        [shot],
        str(shot.get("voice_text") or ""),
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
        current_shot["image_prompt"] = build_image_prompt(current_shot)
        if not current_shot.get("selected_asset_id"):
            current_shot["status"] = "prompt_ready"
        current_shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_db(db)
        return {"shot": current_shot}


@router.patch("/{project_id}/shots/{shot_id}/asset")
def select_generated_image(project_id: str, shot_id: str, payload: dict):
    db = load_db()
    shot = next(
        (item for item in db["shots"] if item["project_id"] == project_id and item["id"] == shot_id),
        None,
    )
    if not shot:
        raise HTTPException(404, "Shot not found")

    asset_id = payload.get("asset_id")
    asset = next(
        (
            item for item in db.get("generated_assets", [])
            if item.get("id") == asset_id
            and item.get("project_id") == project_id
            and item.get("shot_id") == shot_id
        ),
        None,
    )
    if asset_id and not asset:
        raise HTTPException(404, "Storyboard image not found")

    shot["selected_asset_id"] = asset_id
    shot["asset_source"] = asset.get("asset_source") if asset else None
    shot["status"] = "ai_generated" if asset_id else "no_image"
    shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return {"shot": shot}
