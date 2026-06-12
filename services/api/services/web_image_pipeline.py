from __future__ import annotations

import shutil
import os
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from datetime import datetime, timedelta
from pathlib import Path

from services.asset_service import new_id
from services.image_scoring_service import rank_images_for_shot
from services.material_library_service import apply_library_match, apply_material_intent
from services.search_intent_service import SearchIntentBatchError, ai_search_intents, apply_intent_to_shot
from services.store import load_db, project_dir, public_url, save_db
from services.web_image_service import _url_key, download_images_for_shot, search_keywords_for_shot, search_query_for_shot


logger = logging.getLogger("uvicorn.error")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


DONE_STATUSES = {"web_downloaded", "no_image", "ai_generated", "matched"}
ACTIVE_SEARCH_STATUSES = {"pending_search", "analyzing_intent", "searching"}
MIN_ACCEPT_SCORE = 30
MAX_SEARCH_ROUNDS = 2
INTENT_BATCH_SIZE = 5
DEFAULT_CANDIDATE_LIMIT = 2
FAST_IMAGES_PER_SHOT = 2
FAST_IMAGES_PER_KEYWORD = 2
FULL_IMAGES_PER_SHOT = 8
FULL_IMAGES_PER_KEYWORD = 4
FAST_VISUAL_SCORE_LIMIT = 0
FULL_VISUAL_SCORE_LIMIT = 0
SEARCH_BATCH_TIMEOUT_SECONDS = 180
STALE_SEARCH_SECONDS = 180
TENCENT_RETRY_SUFFIXES = [
    "现场照片",
    "新闻图片",
    "历史照片",
    "纪实影像",
    "事件现场",
    "资料图片",
    "高清照片",
    "媒体报道",
    "真实场景",
    "档案图片",
    "现场纪实",
    "新闻现场",
]


def web_image_concurrency() -> int:
    try:
        return max(1, min(8, int(os.getenv("WEB_IMAGE_CONCURRENCY", "6"))))
    except ValueError:
        return 6


def _tencent_retry_queries(shot: dict) -> list[str]:
    core = str((shot.get("search_keywords") or [""])[0]).strip()
    if not core:
        return []
    completed_searches = max(0, int(shot.get("search_attempts") or 0) // MAX_SEARCH_ROUNDS)
    retry_index = max(0, completed_searches - 1)
    start = (retry_index * MAX_SEARCH_ROUNDS) % len(TENCENT_RETRY_SUFFIXES)
    return [
        f"{core} {TENCENT_RETRY_SUFFIXES[(start + offset) % len(TENCENT_RETRY_SUFFIXES)]}"
        for offset in range(MAX_SEARCH_ROUNDS)
    ]


def _intent_batches(shots: list[dict], batch_size: int = INTENT_BATCH_SIZE) -> list[list[dict]]:
    size = max(1, batch_size)
    return [shots[index:index + size] for index in range(0, len(shots), size)]


def _completed_count(shots: list[dict], project_id: str) -> int:
    return sum(
        1 for item in shots
        if item.get("project_id") == project_id and item.get("status") in DONE_STATUSES
    )


def _timestamp(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None


def recover_interrupted_searches(
    db: dict,
    project_id: str | None = None,
    *,
    force: bool = False,
) -> bool:
    now = datetime.now()
    cutoff = now - timedelta(seconds=STALE_SEARCH_SECONDS)
    changed = False
    for project in db.get("projects", []):
        if project_id and project.get("id") != project_id:
            continue
        if project.get("status") != "searching_images":
            continue
        updated_at = _timestamp(project.get("updated_at"))
        if not force and updated_at and updated_at > cutoff:
            continue

        active_shots = [
            shot for shot in db.get("shots", [])
            if shot.get("project_id") == project.get("id")
            and shot.get("status") in ACTIVE_SEARCH_STATUSES
        ]
        if not active_shots:
            project["status"] = "shots_ready"
            project["search_stage"] = "done"
            project["current_shot_index"] = None
            project["current_search_keyword"] = ""
        else:
            asset_ids = {
                asset.get("id") for asset in db.get("generated_assets", [])
                if asset.get("project_id") == project.get("id")
            }
            for shot in active_shots:
                selected_id = shot.get("selected_asset_id")
                shot["status"] = "web_downloaded" if selected_id in asset_ids else "no_image"
                shot["current_search_keyword"] = ""
                shot["search_finished_at"] = now.isoformat(timespec="seconds")
                shot["updated_at"] = shot["search_finished_at"]
            project["status"] = "shots_ready"
            project["search_stage"] = "interrupted"
            project["current_shot_index"] = None
            project["current_search_keyword"] = ""
            project["search_error"] = "Image search was interrupted or timed out; unfinished shots were released for retry."
        project["search_completed"] = _completed_count(db.get("shots", []), project.get("id"))
        project["updated_at"] = now.isoformat(timespec="seconds")
        changed = True
    return changed


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
    db["web_image_diagnostics"] = [
        item for item in db.get("web_image_diagnostics", [])
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
        shot.pop("archive_keywords", None)
        shot["web_image_seen_urls"] = []
        shot["web_image_seen_hashes"] = []
        shot["web_image_seen_sources"] = []
        shot["search_attempts"] = 0
        shot.pop("visual_intent", None)
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


def _search_result_history(items: list) -> list[dict]:
    return [
        {
            "image_url": getattr(item, "image_url", "") or getattr(item, "thumb_url", ""),
            "source_page": getattr(item, "source_page", ""),
        }
        for item in items
        if getattr(item, "image_url", "") or getattr(item, "thumb_url", "")
    ]


def _project_seen_images(db: dict, project_id: str, *, exclude_shot_id: str | None = None) -> dict[str, set[str]]:
    seen = {"urls": set(), "hashes": set(), "sources": set()}
    for asset in db.get("generated_assets", []):
        if asset.get("project_id") != project_id:
            continue
        if exclude_shot_id and asset.get("shot_id") == exclude_shot_id:
            continue
        if asset.get("asset_source") not in {"web_search", "ai_generated"}:
            continue
        if asset.get("remote_url"):
            seen["urls"].add(_url_key(asset["remote_url"]))
        if asset.get("hash"):
            seen["hashes"].add(asset["hash"])
        if asset.get("source_page"):
            seen["sources"].add(_url_key(asset["source_page"]))
    return seen


def _project_has_image(db: dict, project_id: str, item: dict, *, shot_id: str | None = None) -> bool:
    seen = _project_seen_images(db, project_id, exclude_shot_id=shot_id)
    image_url = _url_key(item.get("image_url") or "")
    source_page = _url_key(item.get("source_page") or "")
    return (
        bool(item.get("hash") and item["hash"] in seen["hashes"])
        or bool(image_url and image_url in seen["urls"])
        or bool(source_page and source_page in seen["sources"])
    )


def _project_unique_items(db: dict, project_id: str, shot_id: str, items: list[dict]) -> list[dict]:
    unique = []
    seen = _project_seen_images(db, project_id, exclude_shot_id=shot_id)
    for item in items:
        image_url = _url_key(item.get("image_url") or "")
        source_page = _url_key(item.get("source_page") or "")
        image_hash = item.get("hash") or ""
        if (
            (image_hash and image_hash in seen["hashes"])
            or (image_url and image_url in seen["urls"])
            or (source_page and source_page in seen["sources"])
        ):
            continue
        unique.append(item)
        if image_hash:
            seen["hashes"].add(image_hash)
        if image_url:
            seen["urls"].add(image_url)
        if source_page:
            seen["sources"].add(source_page)
    return unique


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


def _register_downloaded_image(db: dict, project_id: str, shot: dict, item: dict, index: int = 1) -> bool:
    path = Path(item["local_path"])
    if not path.exists():
        logger.warning(
            "[搜图] 镜头=%s 候选登记失败：本地文件不存在 path=%s",
            shot.get("shot_index"),
            path,
        )
        return False
    if _project_has_image(db, project_id, item, shot_id=shot["id"]):
        return False
    now = _now()
    asset_id = new_id()
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
    return True


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
    item["watermark"] = {
        "watermark_detected": False,
        "watermark_removed": False,
        "regions": [],
    }


def _download_and_rank_shot(project_id: str, shot: dict) -> dict:
    shot_started = time.monotonic()
    output_dir = project_dir(project_id) / "images" / f"shot_{shot['shot_index']:03d}"
    failures: list[dict] = []
    diagnostics: list[dict] = []
    all_downloaded: list[dict] = []
    searched_items: list[dict] = []
    top_items: list[dict] = []
    seen_urls = set(shot.get("web_image_seen_urls") or []) | set(shot.get("_project_seen_urls") or [])
    seen_hashes = set(shot.get("web_image_seen_hashes") or []) | set(shot.get("_project_seen_hashes") or [])
    seen_sources = set(shot.get("web_image_seen_sources") or []) | set(shot.get("_project_seen_sources") or [])
    provider_name = str(shot.get("_image_search_provider") or "so")
    search_rounds = [
        {
            "images_per_shot": FAST_IMAGES_PER_SHOT,
            "images_per_keyword": FAST_IMAGES_PER_KEYWORD,
            "results_per_keyword": 10,
            "timeout": 3,
            "visual_limit": FAST_VISUAL_SCORE_LIMIT,
            "keyword_start": 0,
            "keyword_limit": 1,
        },
        {
            "images_per_shot": FULL_IMAGES_PER_SHOT,
            "images_per_keyword": FULL_IMAGES_PER_KEYWORD,
            "results_per_keyword": 30,
            "timeout": 3,
            "visual_limit": FULL_VISUAL_SCORE_LIMIT,
            "keyword_start": 0,
            "keyword_limit": 1,
        },
    ][:MAX_SEARCH_ROUNDS]
    rounds = 0
    logger.info(
        "[搜图] 镜头=%s 开始 关键词=%s",
        shot.get("shot_index"),
        search_query_for_shot(shot) or "无",
    )
    for round_index, round_config in enumerate(search_rounds, start=1):
        rounds = round_index
        round_started = time.monotonic()
        logger.info(
            "[搜图] 镜头=%s 第%d轮开始 检查候选=%d 下载上限=%d",
            shot.get("shot_index"),
            round_index,
            round_config["results_per_keyword"],
            round_config["images_per_keyword"],
        )
        search_queries = search_keywords_for_shot(shot)
        keyword_start = round_index - 1 if provider_name == "tencent" and len(search_queries) > 1 else round_config["keyword_start"]
        search_results, downloaded, round_failures, round_diagnostics = download_images_for_shot(
            shot,
            output_dir,
            images_per_shot=round_config["images_per_shot"],
            images_per_keyword=round_config["images_per_keyword"],
            results_per_keyword=round_config["results_per_keyword"],
            keyword_start=keyword_start,
            keyword_limit=round_config["keyword_limit"],
            delay=0,
            timeout=round_config["timeout"],
            exclude_urls=seen_urls,
            exclude_hashes=seen_hashes,
            exclude_sources=seen_sources,
            provider_name=provider_name,
        )
        failures.extend({**failure, "round": round_index} for failure in round_failures)
        diagnostics.extend({**entry, "round": round_index} for entry in round_diagnostics)
        round_history = _search_result_history(search_results)
        searched_items.extend(round_history)
        all_downloaded.extend(downloaded)
        seen_urls.update(item.get("image_url") for item in round_history if item.get("image_url"))
        seen_urls.update(item.get("image_url") for item in downloaded if item.get("image_url"))
        seen_hashes.update(item.get("hash") for item in downloaded if item.get("hash"))
        seen_sources.update(item.get("source_page") for item in round_history if item.get("source_page"))
        seen_sources.update(item.get("source_page") for item in downloaded if item.get("source_page"))
        top_items = _top_scored_downloads(shot, all_downloaded, limit=DEFAULT_CANDIDATE_LIMIT, visual_limit=round_config["visual_limit"])
        logger.info(
            "[搜图] 镜头=%s 第%d轮结束 新下载=%d 合格候选=%d 分数=%s 耗时=%.2fs",
            shot.get("shot_index"),
            round_index,
            len(downloaded),
            len(top_items),
            [item.get("score_result", {}).get("score", 0) for item in top_items],
            time.monotonic() - round_started,
        )
        if len(top_items) >= DEFAULT_CANDIDATE_LIMIT:
            for item in top_items:
                _cleanup_watermark(item, shot)
            break
        _cleanup_unselected(all_downloaded, top_items)
        all_downloaded = list(top_items)
        if _project_stop_requested(project_id):
            _cleanup_unselected(all_downloaded, None)
            return {
                "shot_id": shot["id"],
                "shot_index": shot["shot_index"],
                "downloaded": [],
                "top_items": [],
                "best": None,
                "rounds": rounds,
                "failures": failures,
                "diagnostics": diagnostics,
                "searched_items": searched_items,
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
            "rounds": rounds,
            "failures": failures,
            "diagnostics": diagnostics,
            "searched_items": searched_items,
            "stopped": True,
        }
    logger.info(
        "[搜图] 镜头=%s 完成 状态=%s 最终候选=%d 最佳分数=%s 总耗时=%.2fs",
        shot.get("shot_index"),
        "成功" if top_items else "无图片",
        len(top_items),
        top_items[0].get("score_result", {}).get("score", 0) if top_items else "-",
        time.monotonic() - shot_started,
    )
    return {
        "shot_id": shot["id"],
        "shot_index": shot["shot_index"],
        "downloaded": all_downloaded,
        "top_items": top_items,
        "best": top_items[0] if top_items else None,
        "rounds": rounds,
        "failures": failures,
        "diagnostics": diagnostics,
        "searched_items": searched_items,
    }


def _apply_search_result(db: dict, project_id: str, result: dict) -> None:
    shot = next((s for s in db.get("shots", []) if s.get("id") == result["shot_id"]), None)
    if not shot:
        _cleanup_unselected(result.get("downloaded") or [], None)
        return
    downloaded = result.get("downloaded") or []
    top_items = result.get("top_items") or ([result["best"]] if result.get("best") else [])
    _remember_seen_images(shot, result.get("searched_items") or [])
    _remember_seen_images(shot, downloaded)
    top_items = _project_unique_items(db, project_id, shot["id"], top_items)
    _cleanup_unselected(downloaded, top_items)
    registered_items = []
    if top_items:
        for index, item in enumerate(top_items[:DEFAULT_CANDIDATE_LIMIT], start=1):
            if _register_downloaded_image(db, project_id, shot, item, index):
                registered_items.append(item)
        if registered_items:
            shot["match_score"] = registered_items[0]["score_result"]["score"]
            shot["image_score"] = registered_items[0]["score_result"]
            shot["status"] = "web_downloaded"
        else:
            shot["selected_asset_id"] = None
            shot["asset_source"] = None
            shot["match_score"] = 0
            shot["image_score"] = None
            shot["status"] = "no_image"
    else:
        shot["status"] = "no_image"
    shot["downloaded_image_count"] = len(registered_items)
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
    diagnostics = result.get("diagnostics") or []
    if diagnostics:
        db.setdefault("web_image_diagnostics", []).extend({
            "project_id": project_id,
            "shot_id": shot["id"],
            "shot_index": shot["shot_index"],
            "created_at": _now(),
            **entry,
        } for entry in diagnostics)
        db["web_image_diagnostics"] = db.get("web_image_diagnostics", [])[-3000:]


def _search_shots_batch(project_id: str, shots: list[dict], *, total: int) -> bool:
    db = load_db()
    project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
    shot_ids = {shot.get("id") for shot in shots}
    for shot in db.get("shots", []):
        if shot.get("id") not in shot_ids:
            continue
        shot["status"] = "searching"
        shot["search_keywords"] = search_keywords_for_shot(shot)
        shot["current_search_keyword"] = search_query_for_shot(shot)
        shot["_project_video_ratio"] = project.get("video_ratio", "1:1") if project else "1:1"
        shot["_image_search_provider"] = project.get("image_search_provider", "so") if project else "so"
        project_seen = _project_seen_images(db, project_id, exclude_shot_id=shot.get("id"))
        shot["_project_seen_urls"] = list(project_seen["urls"])
        shot["_project_seen_hashes"] = list(project_seen["hashes"])
        shot["_project_seen_sources"] = list(project_seen["sources"])
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
    executor = ThreadPoolExecutor(max_workers=min(web_image_concurrency(), max(1, len(shot_snapshots))))
    futures = {executor.submit(_download_and_rank_shot, project_id, shot): shot for shot in shot_snapshots}
    handled = set()
    try:
        for future in as_completed(futures, timeout=SEARCH_BATCH_TIMEOUT_SECONDS):
            handled.add(future)
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
    except FuturesTimeoutError:
        logger.error(
            "[image-search] project=%s batch timed out after %ss",
            project_id,
            SEARCH_BATCH_TIMEOUT_SECONDS,
        )
        for future, source_shot in futures.items():
            if future in handled:
                continue
            future.cancel()
            db = load_db()
            _apply_search_result(db, project_id, {
                "shot_id": source_shot["id"],
                "shot_index": source_shot["shot_index"],
                "downloaded": [],
                "best": None,
                "failures": [{
                    "keyword": search_query_for_shot(source_shot),
                    "stage": "worker_timeout",
                    "error": f"Search exceeded {SEARCH_BATCH_TIMEOUT_SECONDS} seconds",
                }],
            })
            save_db(db)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return True


def _analyze_intents_with_progress(project_id: str, batch: list[dict], full_text: str, *, batch_index: int, total_batches: int, analyzed_count: int, total: int) -> dict[str, dict]:
    batch_start = batch[0].get("shot_index", analyzed_count + 1) if batch else analyzed_count + 1
    batch_end = batch[-1].get("shot_index", analyzed_count + len(batch)) if batch else analyzed_count
    started = time.monotonic()
    batch_started_at = _now()
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(ai_search_intents, batch, full_text)
    try:
        while not future.done():
            if _project_stop_requested(project_id):
                future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                raise SearchIntentBatchError("图片搜索已停止")
            elapsed = int(time.monotonic() - started)
            db = load_db()
            project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
            if project:
                project["search_stage"] = "analyzing_intent"
                project["current_shot_index"] = batch_start
                project["intent_batches_total"] = total_batches
                project["intent_batches_completed"] = batch_index - 1
                project["intent_shots_completed"] = analyzed_count
                project["intent_batch_started_at"] = batch_started_at
                project["intent_batch_elapsed"] = elapsed
                project["current_search_keyword"] = (
                    f"正在分析关键词：第 {batch_start}-{batch_end} 个分镜"
                    f"（{batch_index}/{total_batches} 批），GLM 正在返回中，已等待 {elapsed} 秒"
                )
                project["updated_at"] = _now()
                save_db(db)
            time.sleep(3)
        return future.result()
    finally:
        executor.shutdown(wait=future.done(), cancel_futures=True)


def _run_project_web_image_search(project_id: str) -> None:
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
    project["intent_keyword_estimate"] = str(total) if total else "0"
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
            intents = _analyze_intents_with_progress(
                project_id,
                batch,
                full_text,
                batch_index=batch_index,
                total_batches=len(batches),
                analyzed_count=analyzed_count,
                total=total,
            )
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
            apply_material_intent(shot)
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
        strategy = (project or {}).get("material_source_strategy", "library_first")
        batch_db_shots = [
            shot for shot in db_shots
            if str(shot.get("id") or shot.get("shot_index")) in batch_ids
        ]
        if strategy != "web_only":
            for shot in batch_db_shots:
                if apply_library_match(db, project_id, shot):
                    continue
                if strategy == "library_only":
                    shot["status"] = "no_image"
                    shot["selected_asset_id"] = None
                    shot["asset_source"] = None
                    shot["match_score"] = 0
                    shot["updated_at"] = _now()
        save_db(db)

        web_search_shots = [shot for shot in batch_db_shots if shot.get("status") == "pending_search"]
        if web_search_shots and not _search_shots_batch(project_id, web_search_shots, total=total):
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


def run_project_web_image_search(project_id: str) -> None:
    try:
        _run_project_web_image_search(project_id)
    except Exception as exc:
        logger.exception("[image-search] project=%s background task failed", project_id)
        db = load_db()
        project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
        if project:
            project["status"] = "search_failed"
            project["search_stage"] = "failed"
            project["current_shot_index"] = None
            project["current_search_keyword"] = ""
            project["search_error"] = str(exc)[:500]
            project["updated_at"] = _now()
        for shot in db.get("shots", []):
            if shot.get("project_id") == project_id and shot.get("status") in ACTIVE_SEARCH_STATUSES:
                shot["status"] = "no_image"
                shot["current_search_keyword"] = ""
                shot["updated_at"] = _now()
        save_db(db)


def rerun_shot_web_image_search(project_id: str, shot_id: str, image_search_provider: str = "so") -> None:
    db = load_db()
    project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
    shot = next((s for s in db.get("shots", []) if s.get("project_id") == project_id and s.get("id") == shot_id), None)
    if not project or not shot:
        return
    now = _now()
    _clear_shot_web_assets(db, project_id, shot_id)
    shot["selected_asset_id"] = None
    shot["asset_source"] = None
    shot["match_score"] = 0
    shot["image_score"] = None
    shot["status"] = "searching"
    shot["downloaded_image_count"] = 0
    shot.pop("_search_query_overrides", None)
    shot["search_keywords"] = search_keywords_for_shot(shot)
    if image_search_provider == "tencent":
        shot["_search_query_overrides"] = _tencent_retry_queries(shot)
    else:
        shot.pop("_search_query_overrides", None)
    shot["current_search_keyword"] = search_query_for_shot(shot)
    shot["_project_video_ratio"] = project.get("video_ratio", "1:1")
    shot["_image_search_provider"] = image_search_provider
    shot["updated_at"] = now
    project["status"] = "searching_images"
    project["image_search_provider"] = image_search_provider
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
    if shot:
        shot.pop("_search_query_overrides", None)
    if project:
        project["status"] = "shots_ready"
        project["current_shot_index"] = None
        project["current_search_keyword"] = ""
        project["updated_at"] = _now()
    save_db(db)
