from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.store import db_write_transaction, load_db, project_dir, projects_dir, save_db
from services.history_workflow_service import (
    STEP_LABELS,
    generate_history_step,
    normalize_history_model_provider,
    revise_history_step,
)
from services.text_service import RewriteGenerationError, RewriteQualityError, compare_scripts, extract_opening_hook, infer_title, merge_short_script_paragraphs, rewrite_script

router = APIRouter(prefix="/api/projects", tags=["projects"])
DEFAULT_PROMOTION_BOOK_TITLE = "国之脊梁"
DEFAULT_PROMOTION_BOOK_TITLES = ("女性人物传记", "历史深处的民国", "国之脊梁")


def normalize_promotion_book_title(value: object) -> str:
    title = str(value or "").strip().strip("《》").strip()
    if not title:
        raise HTTPException(400, "Promotion book title must not be empty")
    if len(title) > 80:
        raise HTTPException(400, "Promotion book title must not exceed 80 characters")
    return title


def promotion_book_titles(db: dict) -> list[str]:
    titles = []
    seen = set()
    for value in db.get("promotion_books", []):
        title = str(value or "").strip().strip("《》").strip()
        key = title.casefold()
        if title and key not in seen:
            seen.add(key)
            titles.append(title)
    return titles


def register_promotion_book_title(db: dict, value: object) -> tuple[str, bool]:
    title = normalize_promotion_book_title(value)
    titles = promotion_book_titles(db)
    existing = next((item for item in titles if item.casefold() == title.casefold()), None)
    if existing:
        return existing, False
    db["promotion_books"] = [*titles, title]
    return title, True


class ProjectCreate(BaseModel):
    name: str | None = None
    raw_script: str
    content_type: str = "历史向视频"
    script_style: str = "历史口播"
    voice_style: str = "沉稳男声"
    video_ratio: str = "9:16"
    promotion_book_title: str | None = None


class ScriptUpdate(BaseModel):
    name: str | None = None
    rewritten_script: str | None = None
    title_line1: str | None = None
    title_line2: str | None = None
    archived: bool | None = None


class MergeParagraphsPayload(BaseModel):
    rewritten_script: str


class RewritePayload(BaseModel):
    opening_preserve_rule: str = "auto"
    opening_preserve_chars: int | None = None
    append_book_promotion: bool = False
    promotion_book_title: str = DEFAULT_PROMOTION_BOOK_TITLE


class PromotionBookCreate(BaseModel):
    title: str


class HistoryChatPayload(BaseModel):
    message: str


class HistoryBookPayload(BaseModel):
    title: str


class HistoryModelPayload(BaseModel):
    provider: str


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
        workflow = project.get("history_workflow") or {}
        if workflow:
            item["history_workflow"] = {
                "active_step": workflow.get("active_step", 0),
                "status": workflow.get("status", ""),
                "updated_at": workflow.get("updated_at", ""),
            }
        item["shot_count"] = shot_counts.get(str(project.get("id")), 0)
        item["archived"] = bool(project.get("archived", False))
        item["has_export"] = (
            projects_dir() / str(project.get("id")) / "exports" / "package" / "export_verification.json"
        ).exists()
        projects.append(item)
    return {"projects": projects}


@router.post("")
def create_project(payload: ProjectCreate):
    db = load_db()
    now = datetime.now().isoformat(timespec="seconds")
    project_id = str(uuid4())
    available_books = promotion_book_titles(db)
    promotion_book_title = normalize_promotion_book_title(
        payload.promotion_book_title
        or (available_books[0] if available_books else DEFAULT_PROMOTION_BOOK_TITLE)
    )
    project = {
        "id": project_id,
        "name": payload.name or infer_title(payload.raw_script),
        "raw_script": payload.raw_script,
        "rewritten_script": "",
        "history_workflow": {},
        "content_type": payload.content_type,
        "script_style": payload.script_style,
        "voice_style": payload.voice_style,
        "video_ratio": payload.video_ratio,
        "promotion_book_title": promotion_book_title,
        "history_model_provider": "minimax",
        "storyboard_model_provider": "deepseek",
        "image_generation_provider": "seedream",
        "status": "created",
        "archived": False,
        "created_at": now,
        "updated_at": now,
    }
    register_promotion_book_title(db, promotion_book_title)
    db["projects"].append(project)
    save_db(db)
    project_dir(project_id)
    return {"project_id": project_id, "status": "created", "project": project}


@router.get("/promotion-books")
def list_promotion_books():
    db = load_db()
    if not db.get("promotion_books_catalog_initialized"):
        existing = promotion_book_titles(db)
        for title in DEFAULT_PROMOTION_BOOK_TITLES:
            if not any(item.casefold() == title.casefold() for item in existing):
                existing.append(title)
        db["promotion_books"] = existing
        db["promotion_books_catalog_initialized"] = True
        save_db(db)
    return {"books": [f"《{title}》" for title in promotion_book_titles(db)]}


@router.post("/promotion-books")
def create_promotion_book(payload: PromotionBookCreate):
    db = load_db()
    title, _ = register_promotion_book_title(db, payload.title)
    save_db(db)
    return {"title": f"《{title}》", "books": [f"《{item}》" for item in promotion_book_titles(db)]}


@router.delete("/promotion-books/{title}")
def delete_promotion_book(title: str):
    db = load_db()
    normalized = normalize_promotion_book_title(title)
    books = promotion_book_titles(db)
    remaining = [item for item in books if item.casefold() != normalized.casefold()]
    if len(remaining) == len(books):
        raise HTTPException(404, "Promotion book not found")
    if not remaining:
        raise HTTPException(409, "At least one promotion book must remain")
    db["promotion_books"] = remaining
    save_db(db)
    return {"status": "deleted", "books": [f"《{item}》" for item in remaining]}


@router.get("/{project_id}")
def get_project(project_id: str):
    with db_write_transaction():
        db = load_db()
        project = next((p for p in db["projects"] if p["id"] == project_id), None)
        if not project:
            raise HTTPException(404, "Project not found")
        shots = [s for s in db["shots"] if s["project_id"] == project_id]
        completed = sum(1 for shot in shots if shot.get("status") in {
            "ai_generated", "prompt_ready", "no_image",
        })
        generation_finished = bool(shots) and completed == len(shots)
        if project.get("status") in {"shots_ready", "generating_images"} and (
            project.get("generation_total") != len(shots)
            or project.get("generation_completed") != completed
            or (generation_finished and project.get("generation_stage") != "done")
        ):
            project["generation_total"] = len(shots)
            project["generation_completed"] = completed
            if generation_finished:
                project["status"] = "shots_ready"
                project["generation_stage"] = "done"
                project["current_shot_index"] = None
                project["current_generation_message"] = ""
            save_db(db)
    generated_assets = [
        a for a in db.get("generated_assets", [])
        if a.get("project_id") == project_id
        and a.get("asset_source") in {"ai_generated", "uploaded"}
        and (not a.get("local_path") or Path(str(a.get("local_path"))).exists())
    ]
    return {
        "project": project,
        "shots": sorted(shots, key=lambda s: s["shot_index"]),
        "generated_assets": generated_assets,
    }


@router.post("/{project_id}/rewrite")
def rewrite(project_id: str, payload: RewritePayload | None = None):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    try:
        if payload and payload.opening_preserve_chars is not None:
            if not 1 <= payload.opening_preserve_chars <= 500:
                raise HTTPException(400, "Opening preserve range must be between 1 and 500 characters")
            preserve_rule = f"chars_{payload.opening_preserve_chars}"
        else:
            preserve_rule = (payload.opening_preserve_rule if payload else "auto")
        if preserve_rule not in {"auto", "first_sentence", "first_paragraph"} and not preserve_rule.startswith("chars_"):
            raise HTTPException(400, "Invalid opening preserve rule")
        promotion_book_title = normalize_promotion_book_title(
            payload.promotion_book_title if payload else DEFAULT_PROMOTION_BOOK_TITLE
        )
        append_book_promotion = bool(payload and payload.append_book_promotion)
        result = rewrite_script(
            project["raw_script"],
            project.get("script_style", "纪实故事型"),
            preserve_rule,
            append_book_promotion,
            promotion_book_title,
        )
    except RewriteQualityError as exc:
        detail = exc.result.get("rewrite_error") or str(exc)
        raise HTTPException(422, detail) from exc
    except RewriteGenerationError as exc:
        raise HTTPException(502, exc.detail) from exc
    project["rewritten_script"] = result["rewritten_script"]
    project["rewrite_provider"] = result.get("rewrite_provider", "")
    project["rewrite_error"] = result.get("rewrite_error", "")
    project["rewrite_warning"] = result.get("rewrite_warning", "")
    project["rewrite_analysis_warning"] = result.get("rewrite_analysis_warning", "")
    project["rewrite_quality_status"] = result.get("rewrite_quality_status", "passed")
    project["rewrite_comparison"] = result.get("rewrite_comparison", {})
    project["rewrite_difference"] = result.get("rewrite_difference", 0)
    project["rewrite_attempts"] = result.get("rewrite_attempts", 1)
    project["rewrite_candidates_generated"] = result.get("rewrite_candidates_generated", 1)
    project["rewrite_narrative_strategy"] = result.get("rewrite_narrative_strategy", {})
    fact_brief = result.get("rewrite_fact_brief") or {}
    project["rewrite_verified_quotes"] = (
        fact_brief.get("verified_quotes", []) if isinstance(fact_brief, dict) else []
    )
    project["opening_preserve_rule"] = preserve_rule
    project["opening_preserve_chars"] = payload.opening_preserve_chars if payload else None
    project["append_book_promotion"] = append_book_promotion
    register_promotion_book_title(db, promotion_book_title)
    project["promotion_book_title"] = promotion_book_title
    project["status"] = "script_ready"
    project["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return result


@router.post("/{project_id}/history-workflow/steps/{step}")
def run_history_workflow_step(project_id: str, step: int):
    if step not in STEP_LABELS:
        raise HTTPException(400, "History workflow step must be between 1 and 3")
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    workflow = dict(project.get("history_workflow") or {})
    outputs = dict(workflow.get("outputs") or {})
    messages = dict(workflow.get("messages") or {})
    active_step = int(workflow.get("active_step") or 0)
    is_regeneration = bool(outputs.get(str(step))) and step <= active_step
    can_advance = step > 1 and active_step == step - 1 and bool(outputs.get(str(step - 1)))
    if step > 1 and not (can_advance or is_regeneration):
        raise HTTPException(409, f"请先完成并确认 step{step - 1}")
    if step == 1:
        outputs = {}
        messages = {}
        workflow = {}
    elif is_regeneration:
        for invalidated_step in range(step, 4):
            outputs.pop(str(invalidated_step), None)
            messages.pop(str(invalidated_step), None)
    model_provider = normalize_history_model_provider(
        project.get("history_model_provider") or "minimax"
    )
    try:
        generated = generate_history_step(
            step,
            project.get("raw_script", ""),
            outputs,
            messages,
            project.get("promotion_book_title") or DEFAULT_PROMOTION_BOOK_TITLE,
            model_provider,
            return_details=step == 2,
        )
    except Exception as exc:
        raise HTTPException(502, f"历史创作 step{step} 生成失败：{exc}") from exc
    step_two_comparison = generated if step == 2 and isinstance(generated, dict) else None
    output = str(step_two_comparison.get("final_output") if step_two_comparison else generated)
    now = datetime.now().isoformat(timespec="seconds")
    outputs[str(step)] = output
    messages[str(step)] = []
    workflow.update({
        "active_step": step,
        "status": "awaiting_confirmation",
        "outputs": outputs,
        "messages": messages,
        "model_provider": model_provider,
        "updated_at": now,
    })
    if step_two_comparison is not None:
        workflow["step2_comparison"] = step_two_comparison
    elif step == 1:
        workflow.pop("step2_comparison", None)
    project["history_workflow"] = workflow
    if step == 1:
        project["rewritten_script"] = ""
        project["status"] = "created"
    else:
        project["rewritten_script"] = output
        project["status"] = "script_ready"
    project["updated_at"] = now
    save_db(db)
    return {
        "status": "success",
        "workflow": workflow,
        "output": output,
        "step2_comparison": step_two_comparison,
        "regenerated": is_regeneration,
    }


@router.post("/{project_id}/history-workflow/chat")
def chat_history_workflow(project_id: str, payload: HistoryChatPayload):
    message = payload.message.strip()
    if not message:
        raise HTTPException(400, "聊天内容不能为空")
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    workflow = dict(project.get("history_workflow") or {})
    step = int(workflow.get("active_step") or 0)
    outputs = dict(workflow.get("outputs") or {})
    current_output = str(outputs.get(str(step)) or "").strip()
    if step not in STEP_LABELS or not current_output:
        raise HTTPException(409, "请先点击“改写”执行 step1")
    messages = dict(workflow.get("messages") or {})
    step_messages = list(messages.get(str(step)) or [])
    try:
        model_provider = normalize_history_model_provider(
            project.get("history_model_provider") or "minimax"
        )
        assistant_reply = revise_history_step(
            step,
            project.get("raw_script", ""),
            current_output,
            message,
            step_messages,
            project.get("promotion_book_title") or DEFAULT_PROMOTION_BOOK_TITLE,
            model_provider,
        )
    except Exception as exc:
        raise HTTPException(502, f"AI 修改失败：{exc}") from exc
    step_messages.extend([
        {"role": "user", "content": message},
        {"role": "assistant", "content": assistant_reply},
    ])
    messages[str(step)] = step_messages
    now = datetime.now().isoformat(timespec="seconds")
    workflow.update({
        "outputs": outputs,
        "messages": messages,
        "model_provider": model_provider,
        "status": "awaiting_confirmation",
        "updated_at": now,
    })
    project["history_workflow"] = workflow
    project["updated_at"] = now
    save_db(db)
    return {
        "status": "success",
        "workflow": workflow,
        "output": current_output,
        "assistant_message": assistant_reply,
    }


@router.post("/{project_id}/history-workflow/model")
def select_history_workflow_model(project_id: str, payload: HistoryModelPayload):
    try:
        provider = normalize_history_model_provider(payload.provider)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    now = datetime.now().isoformat(timespec="seconds")
    workflow = dict(project.get("history_workflow") or {})
    if workflow:
        workflow["model_provider"] = provider
        workflow["updated_at"] = now
        project["history_workflow"] = workflow
    project["history_model_provider"] = provider
    project["updated_at"] = now
    save_db(db)
    return {"status": "success", "provider": provider, "workflow": workflow}


@router.post("/{project_id}/history-workflow/book")
def select_history_workflow_book(project_id: str, payload: HistoryBookPayload):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    title, _ = register_promotion_book_title(db, payload.title)
    now = datetime.now().isoformat(timespec="seconds")
    project["promotion_book_title"] = title
    project["history_workflow"] = {}
    project["rewritten_script"] = ""
    project["status"] = "created"
    project["updated_at"] = now
    save_db(db)
    return {
        "status": "success",
        "title": f"《{title}》",
        "workflow": {},
        "books": [f"《{item}》" for item in promotion_book_titles(db)],
    }


@router.post("/{project_id}/history-workflow/finalize")
def finalize_history_workflow(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    workflow = dict(project.get("history_workflow") or {})
    outputs = dict(workflow.get("outputs") or {})
    final_script = str(outputs.get("3") or "").strip()
    if int(workflow.get("active_step") or 0) != 3 or not final_script:
        raise HTTPException(409, "请先完成 step3")
    now = datetime.now().isoformat(timespec="seconds")
    workflow.update({"status": "completed", "updated_at": now})
    project["history_workflow"] = workflow
    project["rewritten_script"] = final_script
    project["status"] = "script_ready"
    project["updated_at"] = now
    save_db(db)
    return {"status": "success", "workflow": workflow, "rewritten_script": final_script}


@router.post("/{project_id}/merge-script-paragraphs")
def merge_script_paragraphs(project_id: str, payload: MergeParagraphsPayload):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    source = payload.rewritten_script.strip()
    if not source:
        raise HTTPException(400, "Rewritten script must not be empty")
    before_count = len([line for line in source.splitlines() if line.strip()])
    merged = merge_short_script_paragraphs(source)
    after_count = len([line for line in merged.splitlines() if line.strip()])
    if "".join(source.split()) != "".join(merged.split()):
        raise HTTPException(500, "Paragraph merge unexpectedly changed script content")
    raw_script = project.get("raw_script", "")
    protected_opening = extract_opening_hook(raw_script, project.get("opening_preserve_rule", "auto"))
    comparison = compare_scripts(
        raw_script,
        merged,
        protected_opening,
        project.get("rewrite_verified_quotes") or [],
    )
    project["rewritten_script"] = merged
    project["rewrite_comparison"] = comparison
    project["rewrite_difference"] = comparison["overall_difference"]
    project["status"] = "script_ready"
    project["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return {
        "rewritten_script": merged,
        "before_count": before_count,
        "after_count": after_count,
        "rewrite_comparison": comparison,
    }


@router.patch("/{project_id}/script")
def update_script(project_id: str, payload: ScriptUpdate):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(400, "Project name must not be empty")
        if len(name) > 80:
            raise HTTPException(400, "Project name must not exceed 80 characters")
        project["name"] = name
    if payload.rewritten_script is not None:
        project["rewritten_script"] = payload.rewritten_script
        raw_script = project.get("raw_script", "")
        protected_opening = extract_opening_hook(raw_script, project.get("opening_preserve_rule", "auto"))
        comparison = compare_scripts(
            raw_script,
            payload.rewritten_script,
            protected_opening,
            project.get("rewrite_verified_quotes") or [],
        )
        project["rewrite_comparison"] = comparison
        project["rewrite_difference"] = comparison["overall_difference"]
        project["status"] = "script_ready"
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
    return {
        "status": "saved",
        "rewritten_script": project.get("rewritten_script", ""),
        "rewrite_comparison": project.get("rewrite_comparison", {}),
        "rewrite_difference": project.get("rewrite_difference", 0),
    }


@router.delete("/{project_id}")
def delete_project(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")

    db["projects"] = [p for p in db["projects"] if p["id"] != project_id]
    db["shots"] = [s for s in db["shots"] if s.get("project_id") != project_id]
    db["generated_assets"] = [ga for ga in db.get("generated_assets", []) if ga.get("project_id") != project_id]
    save_db(db)

    project_root = projects_dir().resolve()
    target = (project_root / project_id).resolve()
    if target.exists() and project_root in target.parents:
        shutil.rmtree(target)

    return {"status": "deleted", "project_id": project_id}
