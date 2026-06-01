from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from services.match_service import score_asset, status_from_score
from services.store import load_db, save_db

router = APIRouter(prefix="/api/projects", tags=["matching"])


@router.post("/{project_id}/match-assets")
def match_assets(project_id: str):
    db = load_db()
    shots = [s for s in db["shots"] if s["project_id"] == project_id]
    assets = [a for a in db["assets"] if a.get("is_available", True)]
    results = []
    db["project_assets"] = [pa for pa in db["project_assets"] if pa["project_id"] != project_id]
    for shot in shots:
        candidates = []
        for asset in assets:
            score, reason = score_asset(shot, asset)
            candidates.append({"asset_id": asset["id"], "asset": asset, "match_score": score, "match_reason": reason})
        candidates = sorted(candidates, key=lambda c: c["match_score"], reverse=True)[:5]
        best = candidates[0] if candidates else None
        status = status_from_score(best["match_score"]) if best else "no_match"
        if best and best["match_score"] >= 50:
            shot["selected_asset_id"] = best["asset_id"]
            shot["asset_source"] = "local"
            shot["match_score"] = best["match_score"]
        shot["status"] = status
        shot["updated_at"] = datetime.now().isoformat(timespec="seconds")
        for c in candidates:
            db["project_assets"].append({
                "project_id": project_id,
                "shot_id": shot["id"],
                "asset_id": c["asset_id"],
                "asset_source": "local",
                "match_score": c["match_score"],
                "match_reason": c["match_reason"],
                "is_selected": c["asset_id"] == shot.get("selected_asset_id"),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            })
        results.append({"shot_id": shot["id"], "status": status, "candidates": candidates})
    save_db(db)
    return {"project_id": project_id, "results": results}


@router.patch("/{project_id}/shots/{shot_id}/asset")
def select_asset(project_id: str, shot_id: str, payload: dict):
    db = load_db()
    shot = next((s for s in db["shots"] if s["project_id"] == project_id and s["id"] == shot_id), None)
    if not shot:
        raise HTTPException(404, "Shot not found")
    asset_id = payload.get("asset_id")
    if asset_id and not any(a["id"] == asset_id for a in db["assets"]):
        raise HTTPException(404, "Asset not found")
    shot["selected_asset_id"] = asset_id
    shot["asset_source"] = payload.get("asset_source", "local")
    shot["status"] = "manually_selected" if asset_id else "no_match"
    save_db(db)
    return {"shot": shot}
