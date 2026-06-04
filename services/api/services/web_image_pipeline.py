from __future__ import annotations

import shutil
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from services.asset_service import new_id
from services.generation_service import generate_doubao_image, generate_svg_placeholder
from services.image_postprocess_service import remove_watermark_if_present
from services.image_scoring_service import rank_images_for_shot
from services.search_intent_service import SearchIntentBatchError, ai_search_intents, apply_intent_to_shot
from services.store import load_db, project_dir, public_url, save_db
from services.web_image_service import download_images_for_shot, search_query_for_shot


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


DONE_STATUSES = {"web_downloaded", "no_image", "ai_generated"}
ACTIVE_SEARCH_STATUSES = {"pending_search", "analyzing_intent", "searching"}
MIN_ACCEPT_SCORE = 30
MAX_SEARCH_ROUNDS = 2
INTENT_BATCH_SIZE = 10
DEFAULT_CANDIDATE_LIMIT = 2
FAST_IMAGES_PER_SHOT = 2
FAST_IMAGES_PER_KEYWORD = 2
FULL_IMAGES_PER_SHOT = 4
FULL_IMAGES_PER_KEYWORD = 2
FAST_VISUAL_SCORE_LIMIT = 0
FULL_VISUAL_SCORE_LIMIT = 0


def web_image_concurrency() -> int:
    try:
        return max(1, min(8, int(os.getenv("WEB_IMAGE_CONCURRENCY", "4"))))
    except ValueError:
        return 4


def _intent_batches(shots: list[dict], batch_size: int = INTENT_BATCH_SIZE) -> list[list[dict]]:
    size = max(1, batch_size)
    return [shots[index:index + size] for index in range(0, len(shots), size)]


def _completed_count(shots: list[dict], project_id: str) -> int:
    return sum(
        1 for item in shots
        if item.get("project_id") == project_id and item.get("status") in DONE_STATUSES
    )


def _project_stop_requested(project_id: str) -> bool:
    db = load_db()
    project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
    return bool(project and project.get("search_stop_requested"))


def _mark_project_search_stopped(db: dict, project_id: str) -> None:
    now = _now()
    project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
    if project:
        project["status"] = "search_stopped"
        project["search_stage"] = "stopped"
        project["search_stop_requested"] = True
        project["current_shot_index"] = None
        project["current_search_keyword"] = "图片搜索已停止"
        project["search_completed"] = _completed_count(db.get("shots", []), project_id)
        project["updated_at"] = now
    for shot in db.get("shots", []):
        if shot.get("project_id") == project_id and shot.get("status") in ACTIVE_SEARCH_STATUSES:
            shot["status"] = "search_stopped"
            shot["current_search_keyword"] = ""
            shot["updated_at"] = now


def request_stop_project_search(db: dict, project_id: str) -> None:
    project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
    if not project:
        return
    project["search_stop_requested"] = True
    _mark_project_search_stopped(db, project_id)
    project["search_stop_requested"] = True


def reset_project_web_images(db: dict, project_id: str) -> None:
    db["project_assets"] = [
        item for item in db.get("project_assets", [])
        if not (
            item.get("project_id") == project_id
            and item.get("asset_source") in {"web_search", "ai_generated"}
        )
    ]
    db["generated_assets"] = [
        item for item in db.get("generated_assets", [])
        if not (
            item.get("project_id") == project_id
            and item.get("asset_source") in {"web_search", "ai_generated"}
        )
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
        project["search_stage"] = "pending"
        project["search_error"] = ""
        project["search_stop_requested"] = False
        project["intent_analysis_started_at"] = ""
        project["intent_keyword_estimate"] = ""
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
        shot["web_image_seen_urls"] = []
        shot["web_image_seen_hashes"] = []
        shot["web_image_seen_sources"] = []
        shot["search_attempts"] = 0
        shot["visual_intent"] = ""
        shot["visual_need"] = ""
        shot["search_started_at"] = now
        shot["updated_at"] = now


def _remember_seen_images(shot: dict, items: list[dict]) -> None:
    shot["web_image_seen_urls"] = list(dict.fromkeys([
        *(shot.get("web_image_seen_urls") or []),
        *[item.get("image_url") for item in items if item.get("image_url")],
    ]))
    shot["web_image_seen_hashes"] = list(dict.fromkeys([
        *(shot.get("web_image_seen_hashes") or []),
        *[item.get("hash") for item in items if item.get("hash")],
    ]))
    shot["web_image_seen_sources"] = list(dict.fromkeys([
        *(shot.get("web_image_seen_sources") or []),
        *[item.get("source_page") for item in items if item.get("source_page")],
    ]))


def _cleanup_unselected(items: list[dict], keep_items: dict | list[dict] | None) -> None:
    if isinstance(keep_items, dict):
        keep_paths = {Path(keep_items["local_path"])}
    else:
        keep_paths = {Path(item["local_path"]) for item in (keep_items or []) if item.get("local_path")}
    for item in items:
        path = Path(item.get("local_path") or "")
        if path in keep_paths:
            continue
        if path.exists():
            path.unlink(missing_ok=True)


def _clear_shot_web_assets(db: dict, project_id: str, shot_id: str) -> None:
    old_ids = [
        item.get("asset_id") for item in db.get("project_assets", [])
        if item.get("project_id") == project_id
        and item.get("shot_id") == shot_id
        and item.get("asset_source") in {"web_search", "ai_generated"}
    ]
    old_ids.extend(
        item.get("id") for item in db.get("generated_assets", [])
        if item.get("project_id") == project_id
        and item.get("shot_id") == shot_id
        and item.get("asset_source") in {"web_search", "ai_generated"}
    )
    old_ids = list(dict.fromkeys([item for item in old_ids if item]))
    for asset in db.get("generated_assets", []):
        if asset.get("id") in old_ids and asset.get("local_path"):
            Path(asset["local_path"]).unlink(missing_ok=True)
    db["project_assets"] = [
        item for item in db.get("project_assets", [])
        if not (
            item.get("project_id") == project_id
            and item.get("shot_id") == shot_id
            and item.get("asset_source") in {"web_search", "ai_generated"}
        )
    ]
    db["generated_assets"] = [
        item for item in db.get("generated_assets", [])
        if item.get("id") not in old_ids
    ]


def _register_downloaded_image(db: dict, project_id: str, shot: dict, item: dict, index: int = 1) -> None:
    now = _now()
    asset_id = new_id()
    path = Path(item["local_path"])
    item["file_url"] = public_url(path)
    if not shot.get("selected_asset_id"):
        shot["selected_asset_id"] = asset_id
        shot["asset_source"] = "web_search"
    shot["downloaded_image_count"] = index
    shot["status"] = "searching" if index < DEFAULT_CANDIDATE_LIMIT else "web_downloaded"
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
        "score_result": item.get("score_result"),
        "match_score": (item.get("score_result") or {}).get("score", 0),
        "watermark": item.get("watermark"),
        "created_at": now,
    })
    score_result = item.get("score_result") or {}
    db.setdefault("project_assets", []).append({
        "project_id": project_id,
        "shot_id": shot["id"],
        "asset_id": asset_id,
        "asset_source": "web_search",
        "match_score": score_result.get("score", max(100 - (index - 1) * 5, 80)),
        "match_reason": score_result.get("reason") or item["keyword"],
        "score_result": score_result,
        "watermark": item.get("watermark"),
        "source_page": item["source_page"],
        "image_url": item["image_url"],
        "is_selected": shot.get("selected_asset_id") == asset_id,
        "created_at": now,
    })


def _top_scored_downloads(shot: dict, downloaded: list[dict], *, limit: int = DEFAULT_CANDIDATE_LIMIT, visual_limit: int = FAST_VISUAL_SCORE_LIMIT) -> list[dict]:
    ranked = rank_images_for_shot(shot, downloaded, visual_limit=min(visual_limit, len(downloaded))) if downloaded else []
    return [item for item in ranked if not _reject_scored_image(item)][:limit]


def _select_best_download(db: dict, project_id: str, shot: dict, downloaded: list[dict]) -> dict | None:
    _remember_seen_images(shot, downloaded)
    top_items = _top_scored_downloads(shot, downloaded, limit=DEFAULT_CANDIDATE_LIMIT, visual_limit=FULL_VISUAL_SCORE_LIMIT)
    for item in top_items:
        _cleanup_watermark(item, shot)
    _cleanup_unselected(downloaded, top_items)
    for index, item in enumerate(top_items, start=1):
        _register_downloaded_image(db, project_id, shot, item, index)
    if top_items:
        shot["downloaded_image_count"] = len(top_items)
        shot["match_score"] = top_items[0]["score_result"]["score"]
        shot["image_score"] = top_items[0]["score_result"]
        shot["status"] = "web_downloaded"
    return top_items[0] if top_items else None


def _reject_scored_image(item: dict | None) -> bool:
    if not item:
        return False
    score_result = item.get("score_result") or {}
    return bool(score_result.get("non_photo_reasons")) or int(score_result.get("score") or 0) < MIN_ACCEPT_SCORE


def _cleanup_watermark(item: dict | None, shot: dict | None = None) -> None:
    if not item:
        return
    path = Path(item.get("local_path") or "")
    item["watermark"] = remove_watermark_if_present(path)


def _generate_ai_asset(project_id: str, shot: dict) -> dict:
    output_dir = project_dir(project_id) / "images" / f"shot_{shot['shot_index']:03d}"
    output_dir.mkdir(parents=True, exist_ok=True)
    video_ratio = shot.get("_project_video_ratio") or "1:1"
    out = output_dir / f"shot_{shot['shot_index']:03d}_ai.png"
    try:
        result = generate_doubao_image(out, shot, video_ratio)
    except Exception as exc:
        out = output_dir / f"shot_{shot['shot_index']:03d}_ai.svg"
        prompt = generate_svg_placeholder(out, shot)
        result = {
            "prompt": prompt,
            "provider": "local_svg_placeholder",
            "model": "svg_placeholder",
            "image_size": "1080x1920",
            "remote_url": "",
            "error": str(exc)[:300],
        }
    return {
        "local_path": str(out),
        "file_url": "",
        "file_name": out.name,
        "prompt": result.get("prompt", ""),
        "provider": result.get("provider", ""),
        "model": result.get("model", ""),
        "image_size": result.get("image_size", ""),
        "remote_url": result.get("remote_url", ""),
        "seed": result.get("seed"),
        "generation_error": result.get("error", ""),
    }


def _register_ai_asset(db: dict, project_id: str, shot: dict, item: dict) -> None:
    now = _now()
    asset_id = new_id()
    path = Path(item["local_path"])
    db.setdefault("generated_assets", []).append({
        "id": asset_id,
        "project_id": project_id,
        "shot_id": shot["id"],
        "type": "image",
        "file_type": "image",
        "file_name": item["file_name"],
        "asset_source": "ai_generated",
        "prompt": item.get("prompt", ""),
        "provider": item.get("provider", ""),
        "model": item.get("model", ""),
        "image_size": item.get("image_size", ""),
        "remote_url": item.get("remote_url", ""),
        "seed": item.get("seed"),
        "file_url": public_url(path),
        "local_path": item["local_path"],
        "status": "success",
        "generation_error": item.get("generation_error", ""),
        "created_at": now,
    })
    shot["selected_asset_id"] = asset_id
    shot["asset_source"] = "ai_generated"
    shot["status"] = "ai_generated"
    shot["match_score"] = 0
    shot["updated_at"] = now


def _download_and_rank_shot(project_id: str, shot: dict) -> dict:
    output_dir = project_dir(project_id) / "images" / f"shot_{shot['shot_index']:03d}"
    failures: list[dict] = []
    all_downloaded: list[dict] = []
    top_items: list[dict] = []
    seen_urls = set(shot.get("web_image_seen_urls") or [])
    seen_hashes = set(shot.get("web_image_seen_hashes") or [])
    seen_sources = set(shot.get("web_image_seen_sources") or [])
    search_rounds = [
        {
            "images_per_shot": FAST_IMAGES_PER_SHOT,
            "images_per_keyword": FAST_IMAGES_PER_KEYWORD,
            "results_per_keyword": 8,
            "timeout": 5,
            "visual_limit": FAST_VISUAL_SCORE_LIMIT,
            "keyword_start": 0,
            "keyword_limit": 1,
        },
        {
            "images_per_shot": FULL_IMAGES_PER_SHOT,
            "images_per_keyword": FULL_IMAGES_PER_KEYWORD,
            "results_per_keyword": 10,
            "timeout": 6,
            "visual_limit": FULL_VISUAL_SCORE_LIMIT,
            "keyword_start": 1,
            "keyword_limit": 2,
        },
    ][:MAX_SEARCH_ROUNDS]
    rounds = 0
    for round_index, round_config in enumerate(search_rounds, start=1):
        rounds = round_index
        _, downloaded, round_failures = download_images_for_shot(
            shot,
            output_dir,
            images_per_shot=round_config["images_per_shot"],
            images_per_keyword=round_config["images_per_keyword"],
            results_per_keyword=round_config["results_per_keyword"],
            keyword_start=round_config["keyword_start"],
            keyword_limit=round_config["keyword_limit"],
            delay=0,
            timeout=round_config["timeout"],
            exclude_urls=seen_urls,
            exclude_hashes=seen_hashes,
            exclude_sources=seen_sources,
        )
        failures.extend({**failure, "round": round_index} for failure in round_failures)
        all_downloaded.extend(downloaded)
        seen_urls.update(item.get("image_url") for item in downloaded if item.get("image_url"))
        seen_hashes.update(item.get("hash") for item in downloaded if item.get("hash"))
        seen_sources.update(item.get("source_page") for item in downloaded if item.get("source_page"))
        top_items = _top_scored_downloads(shot, all_downloaded, limit=DEFAULT_CANDIDATE_LIMIT, visual_limit=round_config["visual_limit"])
        if top_items:
            for item in top_items:
                _cleanup_watermark(item, shot)
            break
        _cleanup_unselected(downloaded, None)
        if _project_stop_requested(project_id):
            _cleanup_unselected(all_downloaded, None)
            return {
                "shot_id": shot["id"],
                "shot_index": shot["shot_index"],
                "downloaded": [],
                "top_items": [],
                "best": None,
                "ai_asset": None,
                "rounds": rounds,
                "failures": failures,
                "stopped": True,
            }
    if top_items:
        _cleanup_unselected(all_downloaded, top_items)
    if _project_stop_requested(project_id):
        _cleanup_unselected(all_downloaded, top_items)
        return {
            "shot_id": shot["id"],
            "shot_index": shot["shot_index"],
            "downloaded": top_items,
            "top_items": top_items,
            "best": top_items[0] if top_items else None,
            "ai_asset": None,
            "rounds": rounds,
            "failures": failures,
            "stopped": True,
        }
    ai_asset = None
    return {
        "shot_id": shot["id"],
        "shot_index": shot["shot_index"],
        "downloaded": all_downloaded,
        "top_items": top_items,
        "best": top_items[0] if top_items else None,
        "ai_asset": ai_asset,
        "rounds": rounds,
        "failures": failures,
    }


def _apply_search_result(db: dict, project_id: str, result: dict) -> None:
    shot = next((s for s in db.get("shots", []) if s.get("id") == result["shot_id"]), None)
    if not shot:
        _cleanup_unselected(result.get("downloaded") or [], None)
        return
    downloaded = result.get("downloaded") or []
    top_items = result.get("top_items") or ([result["best"]] if result.get("best") else [])
    _remember_seen_images(shot, downloaded)
    if top_items:
        for index, item in enumerate(top_items[:DEFAULT_CANDIDATE_LIMIT], start=1):
            _register_downloaded_image(db, project_id, shot, item, index)
        shot["match_score"] = top_items[0]["score_result"]["score"]
        shot["image_score"] = top_items[0]["score_result"]
        shot["status"] = "web_downloaded"
    elif result.get("ai_asset"):
        _register_ai_asset(db, project_id, shot, result["ai_asset"])
    else:
        shot["status"] = "no_image"
    shot["downloaded_image_count"] = len(top_items[:DEFAULT_CANDIDATE_LIMIT])
    shot["current_search_keyword"] = ""
    shot["search_attempts"] = int(shot.get("search_attempts") or 0) + int(result.get("rounds") or 1)
    shot["search_finished_at"] = _now()
    shot["updated_at"] = shot["search_finished_at"]
    failures = result.get("failures") or []
    if failures:
        db.setdefault("web_image_failures", []).extend({
            "project_id": project_id,
            "shot_id": shot["id"],
            "shot_index": shot["shot_index"],
            "created_at": _now(),
            **failure,
        } for failure in failures)


def _search_shots_batch(project_id: str, shots: list[dict], *, total: int) -> bool:
    db = load_db()
    project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
    shot_ids = {shot.get("id") for shot in shots}
    for shot in db.get("shots", []):
        if shot.get("id") not in shot_ids:
            continue
        shot["status"] = "searching"
        shot["current_search_keyword"] = search_query_for_shot(shot)
        shot["_project_video_ratio"] = project.get("video_ratio", "1:1") if project else "1:1"
        shot["downloaded_image_count"] = 0
        shot["updated_at"] = _now()
    if project:
        project["search_total"] = total
        project["search_completed"] = _completed_count(db.get("shots", []), project_id)
        project["current_shot_index"] = None
        project["search_stage"] = "downloading"
        project["current_search_keyword"] = f"并发搜索 {min(web_image_concurrency(), max(1, len(shots)))} 个镜头"
        project["updated_at"] = _now()
    save_db(db)

    if _project_stop_requested(project_id):
        db = load_db()
        _mark_project_search_stopped(db, project_id)
        save_db(db)
        return False

    shot_snapshots = [
        dict(shot) for shot in db.get("shots", [])
        if shot.get("id") in shot_ids and shot.get("status") == "searching"
    ]
    with ThreadPoolExecutor(max_workers=min(web_image_concurrency(), max(1, len(shot_snapshots)))) as executor:
        futures = {executor.submit(_download_and_rank_shot, project_id, shot): shot for shot in shot_snapshots}
        for future in as_completed(futures):
            if _project_stop_requested(project_id):
                for pending in futures:
                    pending.cancel()
                db = load_db()
                _mark_project_search_stopped(db, project_id)
                save_db(db)
                return False
            source_shot = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "shot_id": source_shot["id"],
                    "shot_index": source_shot["shot_index"],
                    "downloaded": [],
                    "best": None,
                    "ai_asset": None,
                    "failures": [{"keyword": search_query_for_shot(source_shot), "stage": "worker", "error": str(exc)[:300]}],
                }
            db = load_db()
            if (next((p for p in db.get("projects", []) if p.get("id") == project_id), {}) or {}).get("search_stop_requested"):
                _cleanup_unselected(result.get("downloaded") or [], None)
                _mark_project_search_stopped(db, project_id)
                save_db(db)
                return False
            _apply_search_result(db, project_id, result)
            project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
            if project:
                project["search_completed"] = _completed_count(db.get("shots", []), project_id)
                project["search_total"] = total
                project["current_shot_index"] = result.get("shot_index")
                project["search_stage"] = "downloading"
                project["current_search_keyword"] = "并发搜索中"
                project["updated_at"] = _now()
            save_db(db)
    return True


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
    project["search_stage"] = "analyzing_intent"
    project["search_error"] = ""
    project["intent_analysis_started_at"] = now
    project["intent_batch_size"] = INTENT_BATCH_SIZE
    project["intent_keyword_estimate"] = f"{total * 3}-{total * 5}" if total else "0"
    project["current_search_keyword"] = f"正在按每批 {INTENT_BATCH_SIZE} 个分镜分析关键词，预计生成 {project['intent_keyword_estimate']} 个关键词"
    project["updated_at"] = now
    save_db(db)

    analyzed_count = 0
    batches = _intent_batches(shots)
    for batch_index, batch in enumerate(batches, start=1):
        if _project_stop_requested(project_id):
            db = load_db()
            _mark_project_search_stopped(db, project_id)
            save_db(db)
            return
        batch_start = batch[0].get("shot_index", analyzed_count + 1) if batch else analyzed_count + 1
        batch_end = batch[-1].get("shot_index", analyzed_count + len(batch)) if batch else analyzed_count
        db = load_db()
        project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
        if project:
            project["search_stage"] = "analyzing_intent"
            project["current_shot_index"] = batch_start
            project["current_search_keyword"] = f"正在分析关键词：第 {batch_start}-{batch_end} 个分镜（{batch_index}/{len(batches)} 批）"
            project["intent_batches_total"] = len(batches)
            project["intent_batches_completed"] = batch_index - 1
            project["intent_shots_completed"] = analyzed_count
            project["updated_at"] = _now()
            save_db(db)

        try:
            intents = ai_search_intents(batch, full_text)
        except SearchIntentBatchError as exc:
            db = load_db()
            project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
            if project:
                project["status"] = "search_failed"
                project["search_stage"] = "intent_failed"
                project["current_shot_index"] = batch_start
                project["current_search_keyword"] = f"关键词分析失败：第 {batch_start}-{batch_end} 个分镜"
                project["search_error"] = str(exc)[:500]
                project["updated_at"] = _now()
            for shot in db.get("shots", []):
                if shot.get("project_id") == project_id and shot.get("status") == "analyzing_intent":
                    shot["status"] = "intent_failed"
                    shot["current_search_keyword"] = ""
                    shot["updated_at"] = _now()
            save_db(db)
            return

        if _project_stop_requested(project_id):
            db = load_db()
            _mark_project_search_stopped(db, project_id)
            save_db(db)
            return

        db = load_db()
        project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
        db_shots = sorted(
            [s for s in db.get("shots", []) if s.get("project_id") == project_id],
            key=lambda s: s.get("shot_index", 0),
        )
        batch_ids = {str(shot.get("id") or shot.get("shot_index")) for shot in batch}
        for shot in db_shots:
            shot_key = str(shot.get("id") or shot.get("shot_index"))
            if shot_key not in batch_ids:
                continue
            intent = intents.get(str(shot.get("id"))) or intents.get(str(shot.get("shot_index")))
            if not intent:
                continue
            apply_intent_to_shot(shot, intent)
            shot["status"] = "pending_search"
            shot["updated_at"] = _now()
        analyzed_count += len(batch)
        if project:
            project["search_stage"] = "analyzing_intent"
            project["current_shot_index"] = batch_end
            project["current_search_keyword"] = f"已完成关键词分析：{analyzed_count}/{total} 个分镜"
            project["intent_batches_total"] = len(batches)
            project["intent_batches_completed"] = batch_index
            project["intent_shots_completed"] = analyzed_count
            project["updated_at"] = _now()
        save_db(db)

        batch_db_shots = [shot for shot in db_shots if str(shot.get("id") or shot.get("shot_index")) in batch_ids]
        if not _search_shots_batch(project_id, batch_db_shots, total=total):
            return

    db = load_db()
    project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
    if project:
        project["status"] = "shots_ready"
        project["search_completed"] = _completed_count(db.get("shots", []), project_id)
        project["search_total"] = total
        project["current_shot_index"] = None
        project["current_search_keyword"] = ""
        project["search_stage"] = "done"
        project["updated_at"] = _now()
        save_db(db)


def rerun_shot_web_image_search(project_id: str, shot_id: str) -> None:
    db = load_db()
    project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
    shot = next((s for s in db.get("shots", []) if s.get("project_id") == project_id and s.get("id") == shot_id), None)
    if not project or not shot:
        return
    if int(shot.get("search_attempts") or 0) >= 2:
        return

    now = _now()
    _clear_shot_web_assets(db, project_id, shot_id)
    shot["selected_asset_id"] = None
    shot["asset_source"] = None
    shot["match_score"] = 0
    shot["image_score"] = None
    shot["status"] = "searching"
    shot["downloaded_image_count"] = 0
    shot["current_search_keyword"] = search_query_for_shot(shot)
    shot["_project_video_ratio"] = project.get("video_ratio", "1:1")
    shot["updated_at"] = now
    project["status"] = "searching_images"
    project["current_shot_index"] = shot.get("shot_index")
    project["current_search_keyword"] = shot["current_search_keyword"]
    project["updated_at"] = now
    save_db(db)

    result = _download_and_rank_shot(project_id, dict(shot))
    db = load_db()
    if (next((p for p in db.get("projects", []) if p.get("id") == project_id), {}) or {}).get("search_stop_requested"):
        _cleanup_unselected(result.get("downloaded") or [], None)
        _mark_project_search_stopped(db, project_id)
        save_db(db)
        return
    _apply_search_result(db, project_id, result)
    shot = next((s for s in db.get("shots", []) if s.get("project_id") == project_id and s.get("id") == shot_id), None)
    project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
    if project:
        project["status"] = "shots_ready"
        project["current_shot_index"] = None
        project["current_search_keyword"] = ""
        project["updated_at"] = _now()
    save_db(db)
