from __future__ import annotations


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _tag_score(required: list[str], asset_tags: list[str]) -> float:
    required_lower = [item.lower() for item in required if item]
    asset_lower = [str(item).lower() for item in asset_tags if item]
    if not required_lower or not asset_lower:
        return 0.0
    matched = sum(
        1 for req in required_lower
        if any(req in tag or tag in req for tag in asset_lower)
    )
    return matched / len(required_lower)


def score_asset(shot: dict, asset: dict) -> tuple[int, str]:
    intent = shot.get("material_intent") or {}
    fields = [
        ("主体", _as_list(intent.get("objects") or shot.get("required_object")), asset.get("object") or asset.get("people") or [], 40),
        ("场景", _as_list(intent.get("scenes") or shot.get("required_scene")), asset.get("scene") or [], 30),
        ("事件/年代", _as_list(intent.get("keywords")) + _as_list(intent.get("era")), asset.get("keywords") or [], 20),
        ("风格", _as_list(intent.get("style")), [asset.get("visual_style", "")], 10),
    ]
    active = [(name, required, tags, weight) for name, required, tags, weight in fields if required]
    if not active:
        return 0, "分镜缺少素材匹配意图"
    total_weight = sum(weight for _, _, _, weight in active)
    scores = [(name, _tag_score(required, tags), weight) for name, required, tags, weight in active]
    score = round(sum(value * weight for _, value, weight in scores) / total_weight * 100)
    reasons = [f"{name}匹配" for name, value, _ in scores if value > 0]
    return score, "、".join(reasons) or "素材标签未匹配"


def status_from_score(score: int) -> str:
    if score >= 80:
        return "matched"
    if score >= 50:
        return "needs_review"
    return "no_match"
