from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

from services.asset_service import analyze_asset, new_id, safe_storage_name
from services.match_service import score_asset
from services.store import ASSETS_DIR, load_db, public_url, save_db
from services.text_service import keywords_from_text


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def build_material_intent(shot: dict) -> dict:
    # Prefer AI-generated structured tags (object_tags, scene_tags, keywords)
    # over the legacy keywords_from_text() approach
    ai_objects = [str(x).strip() for x in shot.get("object_tags") or [] if str(x).strip()]
    ai_scenes = [str(x).strip() for x in shot.get("scene_tags") or [] if str(x).strip()]
    ai_keywords = [str(x).strip() for x in shot.get("keywords") or [] if str(x).strip()]

    if ai_objects or ai_scenes or ai_keywords:
        return {
            "objects": ai_objects[:4],
            "scenes": ai_scenes[:3],
            "keywords": ai_keywords[:5],
        }

    # Fallback: use required_object / required_scene if AI tags are not yet available
    objects = list(dict.fromkeys([
        *[str(x).strip() for x in shot.get("required_object") or [] if str(x).strip()],
    ]))[:4]
    scenes = list(dict.fromkeys([
        *[str(x).strip() for x in shot.get("required_scene") or [] if str(x).strip()],
    ]))[:3]
    keywords: list[str] = []

    if not objects and not scenes:
        # Last resort: use keywords_from_text (no more "老照片"/"历史档案" defaults)
        tags = keywords_from_text(f"{shot.get('voice_text', '')} {shot.get('visual_need', '')}")
        objects = [str(x).strip() for x in tags.get("people") or [] if str(x).strip()][:4]
        scenes = [str(x).strip() for x in tags.get("scene") or [] if str(x).strip()][:3]

    return {
        "objects": objects,
        "scenes": scenes,
        "keywords": keywords,
    }


def apply_material_intent(shot: dict) -> dict:
    intent = build_material_intent(shot)
    shot["material_intent"] = intent
    # Only 3 categories: objects (主体), scenes (场景), keywords (关键词)
    shot["material_keywords"] = list(dict.fromkeys([
        *intent["objects"], *intent["scenes"], *intent["keywords"],
    ]))
    return intent


def apply_library_match(db: dict, project_id: str, shot: dict, minimum_score: int = 50) -> bool:
    best: tuple[dict | None, int, str] = (None, 0, "")
    for asset in db.get("assets", []):
        if asset.get("file_type") != "image" or not asset.get("is_available", True):
            continue
        if not Path(str(asset.get("local_path") or "")).exists():
            continue
        score, reason = score_asset(shot, asset)
        if score > best[1]:
            best = (asset, score, reason)
    asset, score, reason = best
    if not asset or score < minimum_score:
        return False
    now = now_iso()
    shot.update({
        "selected_asset_id": asset["id"],
        "asset_source": "local",
        "match_score": score,
        "status": "matched",
        "downloaded_image_count": 1,
        "updated_at": now,
    })
    db.setdefault("project_assets", []).append({
        "project_id": project_id,
        "shot_id": shot["id"],
        "asset_id": asset["id"],
        "asset_source": "local",
        "match_score": score,
        "match_reason": reason,
        "is_selected": True,
        "created_at": now,
    })
    return True


def analyze_archived_asset(asset_id: str) -> None:
    db = load_db()
    asset = next((x for x in db.get("assets", []) if x.get("id") == asset_id), None)
    if not asset:
        return
    path = Path(str(asset.get("local_path") or ""))
    tags = analyze_asset(asset.get("file_name") or path.name, path, "image")
    for key in ("object", "scene", "keywords"):
        tags[key] = list(dict.fromkeys([
            *[str(x).strip() for x in asset.get(key) or [] if str(x).strip()],
            *[str(x).strip() for x in tags.get(key) or [] if str(x).strip()],
        ]))[:12]
    asset.update(tags)
    asset["analysis_status"] = "ready" if not tags.get("analysis_error") else "failed"
    asset["updated_at"] = now_iso()
    save_db(db)


def archive_project_images(db: dict, project_id: str) -> dict:
    shots = sorted(
        [x for x in db.get("shots", []) if x.get("project_id") == project_id],
        key=lambda x: x.get("shot_index", 0),
    )
    generated = [x for x in db.get("generated_assets", []) if x.get("project_id") == project_id]
    existing_hashes: set[str] = set()
    for asset in db.get("assets", []):
        content_hash = str(asset.get("hash") or "")
        path = Path(str(asset.get("local_path") or ""))
        if not content_hash and path.exists() and asset.get("file_type") == "image":
            content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            asset["hash"] = content_hash
        if content_hash:
            existing_hashes.add(content_hash)
    created_ids: list[str] = []
    skipped = 0
    missing = 0
    for shot in shots:
        candidates = [x for x in generated if x.get("shot_id") == shot.get("id")]
        selected_id = shot.get("selected_asset_id")
        if selected_id and any(x.get("id") == selected_id for x in db.get("assets", [])):
            skipped += 1
            continue
        selected = next((x for x in candidates if x.get("id") == selected_id), None)
        if not selected and not selected_id:
            selected = candidates[0] if candidates else None
        if not selected:
            missing += 1
            continue
        source = Path(str(selected.get("local_path") or ""))
        if not source.exists():
            missing += 1
            continue
        content_hash = selected.get("hash") or hashlib.sha256(source.read_bytes()).hexdigest()
        if content_hash in existing_hashes:
            skipped += 1
            continue
        asset_id = new_id()
        suffix = source.suffix.lower() or ".jpg"
        filename = safe_storage_name(f"shot-{shot.get('shot_index', 0):03d}-{asset_id}{suffix}")
        target = ASSETS_DIR / filename
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        intent = shot.get("material_intent") or apply_material_intent(shot)
        now = now_iso()
        asset = {
            "id": asset_id,
            "library_id": (db.get("asset_library") or {}).get("id"),
            "file_name": filename,
            "original_path": selected.get("source_page") or selected.get("remote_url") or "",
            "file_type": "image",
            "file_url": public_url(target),
            "thumbnail_url": public_url(target),
            "local_path": str(target),
            "hash": content_hash,
            "object": list(intent.get("objects") or []),
            "scene": list(intent.get("scenes") or []),
            "keywords": list(dict.fromkeys([
                *(intent.get("keywords") or []),
            ])),
            "visual_style": "新闻纪实",
            "analysis_status": "analyzing",
            "analysis_provider": "pending",
            "analysis_error": "",
            "source_note": selected.get("source_page") or selected.get("provider") or "分镜批量入库",
            "copyright_note": "来源与使用权需人工确认",
            "is_available": True,
            "archived_from_project_id": project_id,
            "archived_from_shot_id": shot.get("id"),
            "created_at": now,
            "updated_at": now,
        }
        db.setdefault("assets", []).append(asset)
        existing_hashes.add(content_hash)
        created_ids.append(asset_id)
    return {
        "created_ids": created_ids,
        "created": len(created_ids),
        "skipped_duplicates": skipped,
        "missing": missing,
    }
