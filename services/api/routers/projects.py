from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.store import PROJECTS_DIR, load_db, project_dir, save_db
from services.text_service import RewriteGenerationError, RewriteQualityError, compare_scripts, extract_opening_hook, generate_guozhijiliang_script, infer_title, merge_short_script_paragraphs, rewrite_script
from services.web_image_pipeline import DONE_STATUSES, recover_interrupted_searches

router = APIRouter(prefix="/api/projects", tags=["projects"])
DEFAULT_PROMOTION_BOOK_TITLE = "国之脊梁"


def normalize_promotion_book_title(value: object) -> str:
    title = str(value or "").strip().strip("《》").strip()
    if not title:
        raise HTTPException(400, "Promotion book title must not be empty")
    if len(title) > 60:
        raise HTTPException(400, "Promotion book title must not exceed 60 characters")
    return title


def promotion_book_titles(db: dict) -> list[str]:
    titles = [DEFAULT_PROMOTION_BOOK_TITLE]
    titles.extend(db.get("promotion_books") or [])
    titles.extend(project.get("promotion_book_title") for project in db.get("projects", []))
    normalized = []
    seen = set()
    for value in titles:
        try:
            title = normalize_promotion_book_title(value)
        except HTTPException:
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(title)
    return normalized


def register_promotion_book_title(db: dict, value: object) -> tuple[str, bool]:
    title = normalize_promotion_book_title(value)
    existing = next(
        (item for item in promotion_book_titles(db) if item.casefold() == title.casefold()),
        None,
    )
    if existing is not None:
        return existing, False
    db.setdefault("promotion_books", []).append(title)
    return title, True


class ProjectCreate(BaseModel):
    name: str | None = None
    raw_script: str
    content_type: str = "国之脊梁"
    script_style: str = "纪实故事型"
    voice_style: str = "沉稳男声"
    video_ratio: str = "9:16"
    promotion_book_title: str = DEFAULT_PROMOTION_BOOK_TITLE


class ScriptUpdate(BaseModel):
    name: str | None = None
    rewritten_script: str | None = None
    title_line1: str | None = None
    title_line2: str | None = None
    archived: bool | None = None


class AiScriptPayload(BaseModel):
    person_name: str | None = None
    event_angle: str | None = None


class MergeParagraphsPayload(BaseModel):
    rewritten_script: str


class RewritePayload(BaseModel):
    opening_preserve_rule: str = "auto"
    opening_preserve_chars: int | None = None
    append_book_promotion: bool = False
    promotion_book_title: str = DEFAULT_PROMOTION_BOOK_TITLE


class PromotionBookCreate(BaseModel):
    title: str


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
            PROJECTS_DIR / str(project.get("id")) / "exports" / "package" / "export_verification.json"
        ).exists()
        projects.append(item)
    return {"projects": projects}


@router.post("")
def create_project(payload: ProjectCreate):
    db = load_db()
    now = datetime.now().isoformat(timespec="seconds")
    project_id = str(uuid4())
    promotion_book_title = normalize_promotion_book_title(payload.promotion_book_title)
    project = {
        "id": project_id,
        "name": payload.name or infer_title(payload.raw_script),
        "raw_script": payload.raw_script,
        "rewritten_script": "",
        "content_type": payload.content_type,
        "script_style": payload.script_style,
        "voice_style": payload.voice_style,
        "video_ratio": payload.video_ratio,
        "promotion_book_title": promotion_book_title,
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


@router.post("/generate-guozhijiliang-script")
def generate_ai_script(payload: AiScriptPayload | None = None):
    try:
        result = generate_guozhijiliang_script(
            (payload.person_name if payload else "") or "",
            (payload.event_angle if payload else "") or "",
        )
    except Exception as exc:
        raise HTTPException(502, f"AI script generation failed: {exc}") from exc
    return {"status": "success", **result}


@router.get("/promotion-books")
def list_promotion_books():
    db = load_db(copy_data=False)
    return {"books": [f"《{title}》" for title in promotion_book_titles(db)]}


@router.post("/promotion-books")
def create_promotion_book(payload: PromotionBookCreate):
    db = load_db()
    title, created = register_promotion_book_title(db, payload.title)
    if created:
        save_db(db)
    return {"title": f"《{title}》", "books": [f"《{item}》" for item in promotion_book_titles(db)]}


@router.get("/{project_id}")
def get_project(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    if recover_interrupted_searches(db, project_id):
        save_db(db)

    if project.get("status") != "searching_images" and (
        project.get("current_shot_index") is not None
        or project.get("current_search_keyword")
    ):
        project["current_shot_index"] = None
        project["current_search_keyword"] = ""
        save_db(db)

    # Fix orphaned shot statuses: if project is not searching, shots stuck in
    # active search statuses should be reset to failed so the UI doesn't hang
    if project.get("status") not in ("searching_images",):
        active_statuses = {"searching", "pending_search", "analyzing_intent"}
        now = datetime.now().isoformat(timespec="seconds")
        fixed = False
        for shot in db["shots"]:
            if shot.get("project_id") == project_id and shot.get("status") in active_statuses:
                shot["status"] = "no_image"
                shot["current_search_keyword"] = ""
                shot["updated_at"] = now
                fixed = True
        if fixed:
            save_db(db)

    shots = [s for s in db["shots"] if s["project_id"] == project_id]
    if project.get("status") == "shots_ready":
        completed = sum(1 for shot in shots if shot.get("status") in DONE_STATUSES)
        if (
            project.get("search_total") != len(shots)
            or project.get("search_completed") != completed
            or (completed == len(shots) and project.get("search_stage") != "done")
        ):
            project["search_total"] = len(shots)
            project["search_completed"] = completed
            if completed == len(shots):
                project["search_stage"] = "done"
            save_db(db)
    generated_assets = [
        a for a in db.get("generated_assets", [])
        if a.get("project_id") == project_id
        and (not a.get("local_path") or Path(str(a.get("local_path"))).exists())
    ]
    web_image_diagnostics = [
        item for item in db.get("web_image_diagnostics", [])
        if item.get("project_id") == project_id
    ]
    return {
        "project": project,
        "shots": sorted(shots, key=lambda s: s["shot_index"]),
        "generated_assets": generated_assets,
        "web_image_diagnostics": web_image_diagnostics,
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
        if len(payload.title_line1.strip()) > 9:
            raise HTTPException(400, "Title line 1 must not exceed 9 characters")
        project["title_line1"] = payload.title_line1
    if payload.title_line2 is not None:
        if len(payload.title_line2.strip()) > 9:
            raise HTTPException(400, "Title line 2 must not exceed 9 characters")
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
    db["project_assets"] = [pa for pa in db.get("project_assets", []) if pa.get("project_id") != project_id]
    db["generated_assets"] = [ga for ga in db.get("generated_assets", []) if ga.get("project_id") != project_id]
    save_db(db)

    target = (PROJECTS_DIR / project_id).resolve()
    if target.exists() and PROJECTS_DIR.resolve() in target.parents:
        shutil.rmtree(target)

    return {"status": "deleted", "project_id": project_id}
