from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from services.asset_service import new_id
from services.generation_service import generate_doubao_image
from services.match_service import score_asset, status_from_score
from services.store import load_db, project_dir, public_url, save_db
from services.text_service import generate_shots

router = APIRouter(prefix="/api/projects", tags=["shots"])


def match_or_generate_for_shot(db: dict, project: dict, shot: dict) -> None:
    assets = [a for a in db["assets"] if a.get("is_available", True) and a.get("analysis_status", "ready") != "analyzing"]
    candidates = []
    for asset in assets:
        score, reason = score_asset(shot, asset)
        candidates.append({"asset_id": asset["id"], "asset": asset, "match_score": score, "match_reason": reason})
    candidates = sorted(candidates, key=lambda c: c["match_score"], reverse=True)[:5]
    best = candidates[0] if candidates else None
    status = status_from_score(best["match_score"]) if best else "no_match"
    now = datetime.now().isoformat(timespec="seconds")

    if best and best["match_score"] >= 50:
        shot["selected_asset_id"] = best["asset_id"]
        shot["asset_source"] = "local"
        shot["match_score"] = best["match_score"]
        shot["status"] = status
    else:
        out = project_dir(project["id"]) / "images" / f"shot_{shot['shot_index']:03d}.png"
        try:
            result = generate_doubao_image(out, shot, project.get("video_ratio", "9:16"))
            generated_id = new_id()
            db["generated_assets"].append({
                "id": generated_id,
                "project_id": project["id"],
                "shot_id": shot["id"],
                "type": "image",
                "prompt": result["prompt"],
                "provider": result.get("provider"),
                "model": result.get("model"),
                "image_size": result.get("image_size"),
                "remote_url": result.get("remote_url"),
                "seed": result.get("seed"),
                "file_url": public_url(out),
                "local_path": str(out),
                "status": "success",
                "created_at": now,
            })
            shot["selected_asset_id"] = generated_id
            shot["asset_source"] = "ai_generated"
            shot["match_score"] = best["match_score"] if best else 0
            shot["status"] = "ai_generated"
        except Exception as exc:
            shot["selected_asset_id"] = None
            shot["asset_source"] = None
            shot["match_score"] = best["match_score"] if best else 0
            shot["status"] = "generation_failed"
            shot["generation_error"] = str(exc)[:300]

    shot["updated_at"] = now
    for c in candidates:
        db["project_assets"].append({
            "project_id": project["id"],
            "shot_id": shot["id"],
            "asset_id": c["asset_id"],
            "asset_source": "local",
            "match_score": c["match_score"],
            "match_reason": c["match_reason"],
            "is_selected": c["asset_id"] == shot.get("selected_asset_id"),
            "created_at": now,
        })


@router.post("/{project_id}/shots")
def create_shots(project_id: str):
    db = load_db()
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "Project not found")
    db["shots"] = [s for s in db["shots"] if s["project_id"] != project_id]
    db["project_assets"] = [pa for pa in db["project_assets"] if pa["project_id"] != project_id]
    now = datetime.now().isoformat(timespec="seconds")
    shots = []
    for shot in generate_shots(project.get("rewritten_script") or project["raw_script"]):
        shot.update({"id": str(uuid4()), "project_id": project_id, "created_at": now, "updated_at": now})
        match_or_generate_for_shot(db, project, shot)
        shots.append(shot)
    db["shots"].extend(shots)
    project["status"] = "shots_ready"
    project["updated_at"] = now
    save_db(db)
    return {"shots": shots}


@router.patch("/{project_id}/shots/{shot_id}")
def update_shot(project_id: str, shot_id: str, patch: dict):
    db = load_db()
    shot = next((s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id), None)
    if not shot:
        raise HTTPException(404, "Shot not found")
    allowed = {"voice_text", "duration_sec", "visual_need", "exact_keywords", "alternative_keywords", "atmosphere_keywords"}
    for key, value in patch.items():
        if key in allowed:
            shot[key] = value
    shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_db(db)
    return {"shot": shot}
