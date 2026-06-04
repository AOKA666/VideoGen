from __future__ import annotations

import shutil
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from services.asset_service import new_id
from services.generation_service import generate_doubao_image, generate_svg_placeholder, remove_watermark_with_seedream
from services.image_postprocess_service import remove_watermark_if_present
from services.image_scoring_service import rank_images_for_shot
from services.search_intent_service import ai_search_intents, apply_intent_to_shot
from services.store import load_db, project_dir, public_url, save_db
from services.web_image_service import download_images_for_shot, search_query_for_shot


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


DONE_STATUSES = {"web_downloaded", "no_image", "ai_generated"}
MIN_ACCEPT_SCORE = 30
MAX_SEARCH_ROUNDS = 2


def web_image_concurrency() -> int:
    try:
        return max(1, min(8, int(os.getenv("WEB_IMAGE_CONCURRENCY", "4"))))
    except ValueError:
        return 4


def _completed_count(shots: list[dict], project_id: str) -> int:
    return sum(
        1 for item in shots
        if item.get("project_id") == project_id and item.get("status") in DONE_STATUSES
    )


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


def _cleanup_unselected(items: list[dict], keep_item: dict | None) -> None:
    keep_path = Path(keep_item["local_path"]) if keep_item else None
    for item in items:
        path = Path(item.get("local_path") or "")
        if keep_path and path == keep_path:
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


def _select_best_download(db: dict, project_id: str, shot: dict, downloaded: list[dict]) -> dict | None:
    _remember_seen_images(shot, downloaded)
    ranked = rank_images_for_shot(shot, downloaded) if downloaded else []
    best = next((item for item in ranked if not _reject_scored_image(item)), None)
    _cleanup_watermark(best, shot)
    _cleanup_unselected(downloaded, best)
    if best:
        _register_downloaded_image(db, project_id, shot, best, 1)
        shot["match_score"] = best["score_result"]["score"]
        shot["image_score"] = best["score_result"]
        shot["status"] = "web_downloaded"
    return best


def _reject_scored_image(item: dict | None) -> bool:
    if not item:
        return False
    score_result = item.get("score_result") or {}
    return bool(score_result.get("non_photo_reasons")) or int(score_result.get("score") or 0) < MIN_ACCEPT_SCORE


def _cleanup_watermark(item: dict | None, shot: dict | None = None) -> None:
    if not item:
        return
    path = Path(item.get("local_path") or "")
    try:
        watermark = remove_watermark_with_seedream(path, shot)
    except Exception as exc:
        watermark = remove_watermark_if_present(path)
        watermark["ai_watermark_error"] = str(exc)[:300]
    item["watermark"] = watermark


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
    best = None
    seen_urls = set(shot.get("web_image_seen_urls") or [])
    seen_hashes = set(shot.get("web_image_seen_hashes") or [])
    seen_sources = set(shot.get("web_image_seen_sources") or [])
    rounds = 0
    for round_index in range(1, MAX_SEARCH_ROUNDS + 1):
        rounds = round_index
        _, downloaded, round_failures = download_images_for_shot(
            shot,
            output_dir,
            images_per_shot=3,
            results_per_keyword=10,
            delay=0,
            timeout=6,
            exclude_urls=seen_urls,
            exclude_hashes=seen_hashes,
            exclude_sources=seen_sources,
        )
        failures.extend({**failure, "round": round_index} for failure in round_failures)
        all_downloaded.extend(downloaded)
        seen_urls.update(item.get("image_url") for item in downloaded if item.get("image_url"))
        seen_hashes.update(item.get("hash") for item in downloaded if item.get("hash"))
        seen_sources.update(item.get("source_page") for item in downloaded if item.get("source_page"))
        ranked = rank_images_for_shot(shot, downloaded, visual_limit=1) if downloaded else []
        best = next((item for item in ranked if not _reject_scored_image(item)), None)
        if best:
            _cleanup_watermark(best, shot)
            break
        _cleanup_unselected(downloaded, None)
    if best:
        _cleanup_unselected(all_downloaded, best)
    ai_asset = None if best else _generate_ai_asset(project_id, shot)
    return {
        "shot_id": shot["id"],
        "shot_index": shot["shot_index"],
        "downloaded": all_downloaded,
        "best": best,
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
    best = result.get("best")
    _remember_seen_images(shot, downloaded)
    if best:
        _register_downloaded_image(db, project_id, shot, best, 1)
        shot["match_score"] = best["score_result"]["score"]
        shot["image_score"] = best["score_result"]
        shot["status"] = "web_downloaded"
    elif result.get("ai_asset"):
        _register_ai_asset(db, project_id, shot, result["ai_asset"])
    else:
        shot["status"] = "no_image"
    shot["downloaded_image_count"] = 1 if best else 0
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

    db = load_db()
    project = next((p for p in db.get("projects", []) if p.get("id") == project_id), project)
    shots = sorted(
        [s for s in db.get("shots", []) if s.get("project_id") == project_id],
        key=lambda s: s.get("shot_index", 0),
    )
    for shot in shots:
        shot["status"] = "searching"
        shot["current_search_keyword"] = search_query_for_shot(shot)
        shot["_project_video_ratio"] = project.get("video_ratio", "1:1") if project else "1:1"
        shot["downloaded_image_count"] = 0
        shot["updated_at"] = _now()
    if project:
        project["search_total"] = total
        project["search_completed"] = 0
        project["current_shot_index"] = None
        project["current_search_keyword"] = f"并发搜索 {web_image_concurrency()} 个镜头"
        project["updated_at"] = _now()
    save_db(db)

    shot_snapshots = [dict(shot) for shot in shots]
    with ThreadPoolExecutor(max_workers=web_image_concurrency()) as executor:
        futures = {executor.submit(_download_and_rank_shot, project_id, shot): shot for shot in shot_snapshots}
        for future in as_completed(futures):
            source_shot = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "shot_id": source_shot["id"],
                    "shot_index": source_shot["shot_index"],
                    "downloaded": [],
                    "best": None,
                    "ai_asset": _generate_ai_asset(project_id, source_shot),
                    "failures": [{"keyword": search_query_for_shot(source_shot), "stage": "worker", "error": str(exc)[:300]}],
                }
            db = load_db()
            _apply_search_result(db, project_id, result)
            project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
            if project:
                project["search_completed"] = _completed_count(db.get("shots", []), project_id)
                project["search_total"] = total
                project["current_shot_index"] = result.get("shot_index")
                project["current_search_keyword"] = "并发搜索中"
                project["updated_at"] = _now()
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

    output_dir = project_dir(project_id) / "images" / f"shot_{shot['shot_index']:03d}"
    _, downloaded, failures = download_images_for_shot(
        shot,
        output_dir,
        images_per_shot=3,
        exclude_urls=set(shot.get("web_image_seen_urls") or []),
        exclude_hashes=set(shot.get("web_image_seen_hashes") or []),
        exclude_sources=set(shot.get("web_image_seen_sources") or []),
    )

    db = load_db()
    shot = next((s for s in db.get("shots", []) if s.get("project_id") == project_id and s.get("id") == shot_id), None)
    project = next((p for p in db.get("projects", []) if p.get("id") == project_id), None)
    if shot:
        best = _select_best_download(db, project_id, shot, downloaded)
        if not best:
            _register_ai_asset(db, project_id, shot, _generate_ai_asset(project_id, shot))
        else:
            shot["status"] = "web_downloaded"
        shot["downloaded_image_count"] = 1 if best else 0
        shot["current_search_keyword"] = ""
        shot["search_attempts"] = int(shot.get("search_attempts") or 0) + 1
        shot["search_finished_at"] = _now()
        shot["updated_at"] = shot["search_finished_at"]
        if failures:
            db.setdefault("web_image_failures", []).extend({
                "project_id": project_id,
                "shot_id": shot["id"],
                "shot_index": shot["shot_index"],
                "created_at": _now(),
                **failure,
            } for failure in failures)
    if project:
        project["status"] = "shots_ready"
        project["current_shot_index"] = None
        project["current_search_keyword"] = ""
        project["updated_at"] = _now()
    save_db(db)
