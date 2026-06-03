from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from services.asset_service import new_id
from services.search_intent_service import ai_search_intents, apply_intent_to_shot
from services.store import load_db, project_dir, public_url, save_db
from services.web_image_service import download_images_for_shot


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


DONE_STATUSES = {"web_downloaded", "no_image"}


def _completed_count(shots: list[dict], project_id: str) -> int:
    return sum(
        1 for item in shots
        if item.get("project_id") == project_id and item.get("status") in DONE_STATUSES
    )


def reset_project_web_images(db: dict, project_id: str) -> None:
    db["project_assets"] = [
        item for item in db.get("project_assets", [])
        if not (item.get("project_id") == project_id and item.get("asset_source") == "web_search")
    ]
    db["generated_assets"] = [
        item for item in db.get("generated_assets", [])
        if not (item.get("project_id") == project_id and item.get("asset_source") == "web_search")
    ]
    db["web_image_failures"] = [
        item for item in db.get("web_image_failures", [])
        if item.get("project_id") != project_id
    ]
    images_dir = project_dir(project_id) / "images"
    for folder in images_dir.glob("shot_*"):
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)


def mark_project_searching(db: dict, project_id: str) -> None:
    now = _now()
    total = len([shot for shot in db.get("shots", []) if shot.get("project_id") == project_id])
    project = next((item for item in db.get("projects", []) if item.get("id") == project_id), None)
    if project:
        project["status"] = "searching_images"
        project["search_total"] = total
        project["search_completed"] = 0
        project["current_shot_index"] = None
        project["current_search_keyword"] = ""
        project["updated_at"] = now
    for shot in db.get("shots", []):
        if shot.get("project_id") != project_id:
            continue
        shot["selected_asset_id"] = None
        shot["asset_source"] = None
        shot["match_score"] = 0
        shot["status"] = "pending_search"
        shot["downloaded_image_count"] = 0
        shot["search_keywords"] = []
        shot["visual_intent"] = ""
        shot["visual_need"] = ""
        shot["search_started_at"] = now
        shot["updated_at"] = now


def _register_downloaded_image(db: dict, project_id: str, shot: dict, item: dict, index: int) -> None:
    now = _now()
    asset_id = new_id()
    path = Path(item["local_path"])
    item["file_url"] = public_url(path)
    if not shot.get("selected_asset_id"):
        shot["selected_asset_id"] = asset_id
        shot["asset_source"] = "web_search"
    shot["downloaded_image_count"] = index
    shot["status"] = "searching" if index < 3 else "web_downloaded"
    shot["updated_at"] = now

    db.setdefault("generated_assets", []).append({
        "id": asset_id,
        "project_id": project_id,
        "shot_id": shot["id"],
        "type": "image",
        "file_type": "image",
        "file_name": item["file_name"],
        "asset_source": "web_search",
        "provider": item["source"],
        "prompt": item["keyword"],
        "remote_url": item["image_url"],
        "source_page": item["source_page"],
        "title": item["title"],
        "width": item["width"],
        "height": item["height"],
        "file_size": item["file_size"],
        "hash": item["hash"],
        "file_url": item["file_url"],
        "local_path": item["local_path"],
        "status": "success",
        "created_at": now,
    })
    db.setdefault("project_assets", []).append({
        "project_id": project_id,
        "shot_id": shot["id"],
        "asset_id": asset_id,
        "asset_source": "web_search",
        "match_score": max(100 - (index - 1) * 5, 80),
        "match_reason": item["keyword"],
        "source_page": item["source_page"],
        "image_url": item["image_url"],
        "is_selected": shot.get("selected_asset_id") == asset_id,
        "created_at": now,
    })


def run_project_web_image_search(project_id: str) -> None:
    db = load_db()
    project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
    if not project:
        return
    shots = sorted(
        [s for s in db.get("shots", []) if s.get("project_id") == project_id],
        key=lambda s: s.get("shot_index", 0),
    )
    full_text = f"{project.get('raw_script', '')}\n{project.get('rewritten_script', '')}"
    total = len(shots)

    now = _now()
    for shot in shots:
        shot["status"] = "analyzing_intent"
        shot["current_search_keyword"] = ""
        shot["downloaded_image_count"] = 0
        shot["updated_at"] = now
    project["search_total"] = total
    project["search_completed"] = 0
    project["current_shot_index"] = None
    project["current_search_keyword"] = "正在分析全部分镜关键词"
    project["updated_at"] = now
    save_db(db)

    intents = ai_search_intents(shots, full_text)

    db = load_db()
    project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
    shots = sorted(
        [s for s in db.get("shots", []) if s.get("project_id") == project_id],
        key=lambda s: s.get("shot_index", 0),
    )
    for shot in shots:
        intent = intents.get(str(shot.get("id"))) or intents.get(str(shot.get("shot_index")))
        if not intent:
            continue
        apply_intent_to_shot(shot, intent)
        shot["status"] = "pending_search"
        shot["updated_at"] = _now()
    if project:
        project["search_completed"] = 0
        project["search_total"] = total
        project["current_shot_index"] = None
        project["current_search_keyword"] = "分镜关键词已生成，开始下载图片"
        project["updated_at"] = _now()
    save_db(db)

    for shot in shots:
        db = load_db()
        shot = next((s for s in db.get("shots", []) if s.get("id") == shot["id"]), shot)
        shot["status"] = "searching"
        shot["current_search_keyword"] = ""
        shot["downloaded_image_count"] = 0
        shot["updated_at"] = _now()
        project = next((p for p in db.get("projects", []) if p.get("id") == project_id), project)
        if project:
            current_shots = [s for s in db.get("shots", []) if s.get("project_id") == project_id]
            project["search_total"] = total
            project["search_completed"] = _completed_count(current_shots, project_id)
            project["current_shot_index"] = shot.get("shot_index")
            project["current_search_keyword"] = " / ".join(shot.get("search_keywords", [])[:2])
        save_db(db)

        output_dir = project_dir(project_id) / "images" / f"shot_{shot['shot_index']:03d}"

        def on_download(item: dict, index: int) -> None:
            latest = load_db()
            latest_shot = next((s for s in latest.get("shots", []) if s.get("id") == shot["id"]), None)
            if not latest_shot:
                return
            latest_shot["current_search_keyword"] = item.get("keyword", "")
            _register_downloaded_image(latest, project_id, latest_shot, item, index)
            save_db(latest)

        _, downloaded, failures = download_images_for_shot(
            shot,
            output_dir,
            images_per_shot=3,
            on_download=on_download,
        )
        db = load_db()
        current = next((s for s in db.get("shots", []) if s.get("id") == shot["id"]), None)
        if current:
            current["status"] = "web_downloaded" if downloaded else "no_image"
            current["downloaded_image_count"] = len(downloaded)
            current["current_search_keyword"] = ""
            current["search_finished_at"] = _now()
            current["updated_at"] = current["search_finished_at"]
        project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
        if project:
            project["search_completed"] = _completed_count(db.get("shots", []), project_id)
            project["search_total"] = total
            project["current_shot_index"] = shot.get("shot_index")
            project["updated_at"] = _now()
        if failures:
            db.setdefault("web_image_failures", []).extend({
                "project_id": project_id,
                "shot_id": shot["id"],
                "shot_index": shot["shot_index"],
                "created_at": _now(),
                **failure,
            } for failure in failures)
        save_db(db)

    db = load_db()
    project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
    if project:
        project["status"] = "shots_ready"
        project["search_completed"] = len(shots)
        project["search_total"] = len(shots)
        project["current_shot_index"] = None
        project["current_search_keyword"] = ""
        project["updated_at"] = _now()
        save_db(db)
