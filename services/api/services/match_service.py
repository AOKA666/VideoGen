from __future__ import annotations


def _overlap(a: list[str], b: list[str]) -> float:
    left = {x.lower() for x in a if x}
    right = {x.lower() for x in b if x}
    if not left or not right:
        return 0.0
    return len(left & right) / max(len(left), 1)


def score_asset(shot: dict, asset: dict) -> tuple[int, str]:
    shot_words = []
    for key in ["exact_keywords", "alternative_keywords", "atmosphere_keywords"]:
        shot_words.extend(shot.get(key) or [])
    voice = shot.get("voice_text", "")
    visual = shot.get("visual_need", "")
    joined = " ".join(shot_words + [voice, visual])

    object_tags = asset.get("object") or asset.get("people") or []
    object_score = 1.0 if any(item and item in joined for item in object_tags) else 0.0
    scene_score = 1.0 if any(s and s in joined for s in asset.get("scene", [])) else _overlap(shot_words, asset.get("scene", []))
    keyword_score = 1.0 if any(k and k in joined for k in asset.get("keywords", [])) else _overlap(shot_words, asset.get("keywords", []))
    quality_score = min(float(asset.get("quality_score", 75)) / 100, 1)
    score = object_score * 45 + scene_score * 30 + keyword_score * 20 + quality_score * 5
    reason = []
    if object_score:
        reason.append("主体标签匹配")
    if scene_score:
        reason.append("场景标签匹配")
    if keyword_score:
        reason.append("关键词匹配")
    return round(score), "、".join(reason) or "仅有基础质量分"


def status_from_score(score: int) -> str:
    if score >= 80:
        return "matched"
    if score >= 50:
        return "needs_review"
    return "no_match"
