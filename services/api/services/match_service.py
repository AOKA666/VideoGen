from __future__ import annotations


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _overlap(a: list[str], b: list[str]) -> float:
    left = {x.lower() for x in a if x}
    right = {x.lower() for x in b if x}
    if not left or not right:
        return 0.0
    return len(left & right) / max(len(left), 1)


def _tag_score(required: list[str], asset_tags: list[str]) -> float:
    if not required:
        return 0.0
    required_lower = [item.lower() for item in required if item]
    asset_lower = [item.lower() for item in asset_tags if item]
    if not required_lower or not asset_lower:
        return 0.0
    if any(req in tag or tag in req for req in required_lower for tag in asset_lower):
        return 1.0
    return _overlap(required_lower, asset_lower)


def score_asset(shot: dict, asset: dict) -> tuple[int, str]:
    required_object = _as_list(shot.get("required_object"))
    required_scene = _as_list(shot.get("required_scene"))
    object_tags = asset.get("object") or asset.get("people") or []
    object_score = _tag_score(required_object, object_tags)
    scene_score = _tag_score(required_scene, asset.get("scene", []))

    object_weight = 60 if required_object else 0
    scene_weight = 40 if required_scene else 0
    total_weight = object_weight + scene_weight
    if not total_weight:
        return 0, "分镜缺少主体和场景"

    score = (object_score * object_weight + scene_score * scene_weight) / total_weight * 100
    reason = []
    if object_score:
        reason.append("主体标签匹配")
    if scene_score:
        reason.append("场景标签匹配")
    return round(score), "、".join(reason) or "主体/场景未匹配"


def status_from_score(score: int) -> str:
    if score >= 80:
        return "matched"
    if score >= 50:
        return "needs_review"
    return "no_match"
