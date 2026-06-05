from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from services.asset_service import analyze_asset

try:
    import numpy as np
    from PIL import Image
except Exception:  # pragma: no cover - optional local heuristic
    Image = None
    np = None


WEIGHTS = {
    "subject": 30,
    "scene": 25,
    "era_style": 15,
    "keyword": 15,
    "quality": 10,
    "source": 5,
}

ERA_WORDS = ["旧照", "老照片", "黑白", "档案", "资料图", "历史", "年代", "纪实"]
LOW_TRUST_WORDS = ["素材", "模板", "ppt", "海报", "插画", "ai", "游戏", "表情包", "广告"]
HIGH_TRUST_DOMAINS = ["baike", "news", "people", "xinhuanet", "cctv", "museum", "archive", "gov"]
TARGET_ASPECT_RATIO = 1.0
NON_PHOTO_MEDIA_TYPES = {"screenshot", "render", "illustration", "poster", "document", "chart"}
NON_PHOTO_WORDS = [
    "截图", "屏幕截图", "软件界面", "网页截图", "界面", "工具栏", "菜单栏", "工作平面", "透视图",
    "三维建模", "建模", "渲染", "cad", "rhino", "3d", "模型", "网格", "控制点", "曲面",
    "插画", "海报", "ppt", "模板", "图表", "流程图", "漫画", "表情包",
    "screenshot", "screen capture", "software", "interface", "toolbar", "render", "rendering",
    "illustration", "poster", "modeling", "viewport",
]
TEXT_HEAVY_WORDS = [
    "文字", "书页", "书本", "文章", "帖子", "评论", "微博", "知乎", "公众号", "网页", "页面",
    "聊天记录", "截图文字", "长文", "段落", "排版", "标题", "正文",
    "text", "book page", "article", "post", "comment", "forum", "webpage", "page",
    "paragraph", "document", "novel", "ebook",
]
DESIGN_HEAVY_WORDS = [
    "logo", "icon", "symbol", "sign", "warning sign", "badge", "emblem", "seal",
    "thumbnail", "cover", "title card", "poster", "banner", "infographic", "table",
    "chart", "list", "directory",
    "封面", "标题", "大字", "海报", "标志", "图标", "徽章", "警示牌", "警告牌",
    "表格", "名单", "目录", "信息图", "宣传图",
]


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _text_parts(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value:
            parts.append(str(value))
    return " ".join(parts).lower()


def _contains_any(text: str, words: list[str]) -> list[str]:
    return [word for word in words if word and word.lower() in text]


def _component_boxes_bool(mask: np.ndarray, *, max_components: int = 1000) -> list[tuple[int, int, int, int, int]]:
    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    boxes: list[tuple[int, int, int, int, int]] = []
    components = 0
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or visited[y, x]:
                continue
            components += 1
            if components > max_components:
                return boxes
            stack = [(y, x)]
            visited[y, x] = True
            area = 0
            min_y = max_y = y
            min_x = max_x = x
            while stack:
                cy, cx = stack.pop()
                area += 1
                min_y, max_y = min(min_y, cy), max(max_y, cy)
                min_x, max_x = min(min_x, cx), max(max_x, cx)
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            boxes.append((min_x, min_y, max_x + 1, max_y + 1, area))
    return boxes


def _score_overlap(candidates: list[str], evidence: str, max_score: int, *, partial: int = 4) -> tuple[int, list[str], list[str]]:
    candidates = list(dict.fromkeys([item for item in candidates if item]))
    if not candidates:
        return max_score // 2, [], []
    matched = _contains_any(evidence, candidates)
    missing = [item for item in candidates if item not in matched]
    if matched:
        score = min(max_score, int(max_score * len(matched) / len(candidates)) + partial)
    else:
        score = partial if evidence else 0
    return score, matched, missing


def aspect_ratio_score(item: dict) -> int:
    width = int(item.get("width") or 0)
    height = int(item.get("height") or 0)
    if width <= 0 or height <= 0:
        return 0
    ratio = width / max(height, 1)
    distance = abs(ratio - TARGET_ASPECT_RATIO) / TARGET_ASPECT_RATIO
    if distance <= 0.08:
        return 10
    if distance <= 0.16:
        return 8
    if distance <= 0.28:
        return 5
    if distance <= 0.45:
        return 2
    return 0


def _quality_score(item: dict) -> int:
    width = int(item.get("width") or 0)
    height = int(item.get("height") or 0)
    if width <= 0 or height <= 0:
        return 4
    pixels = width * height
    score = 1
    if pixels >= 1280 * 720:
        score += 2
    if min(width, height) >= 600:
        score += 1
    score += min(6, round(aspect_ratio_score(item) * 0.6))
    return min(WEIGHTS["quality"], score)


def _source_score(meta_text: str, source_page: str) -> int:
    host = urlparse(source_page or "").netloc.lower()
    score = 2
    if _contains_any(meta_text, ERA_WORDS):
        score += 1
    if any(domain in host for domain in HIGH_TRUST_DOMAINS):
        score += 2
    if _contains_any(meta_text, LOW_TRUST_WORDS):
        score -= 2
    return max(0, min(WEIGHTS["source"], score))


def _ui_screenshot_reason(item: dict) -> str:
    if Image is None or np is None:
        return ""
    path = Path(item.get("local_path") or "")
    if not path.exists():
        return ""
    try:
        image = Image.open(path).convert("RGB")
        image.thumbnail((360, 360))
        arr = np.asarray(image, dtype=np.float32) / 255.0
    except Exception:
        return ""
    if arr.size == 0:
        return ""
    maxc = arr.max(axis=2)
    minc = arr.min(axis=2)
    saturation = (maxc - minc) / np.maximum(maxc, 0.001)
    light_gray = (saturation < 0.16) & (maxc > 0.52) & (maxc < 0.98)
    height, width = light_gray.shape
    top = light_gray[:max(1, int(height * 0.14)), :].mean()
    left = light_gray[:, :max(1, int(width * 0.12))].mean()
    overall = light_gray.mean()
    if top > 0.62 and left > 0.42:
        return "软件界面截图"
    if left > 0.72 and overall > 0.45:
        return "软件界面截图"
    if top > 0.40 and left > 0.70:
        return "软件界面截图"
    if top > 0.75 and overall > 0.48:
        return "截图/界面类图片"
    return ""


def _text_heavy_reason(item: dict) -> str:
    if Image is None or np is None:
        return ""
    path = Path(item.get("local_path") or "")
    if not path.exists():
        return ""
    try:
        image = Image.open(path).convert("L")
        image.thumbnail((260, 260))
        gray = np.asarray(image, dtype=np.float32) / 255.0
    except Exception:
        return ""
    if gray.size == 0:
        return ""

    light_ratio = float((gray > 0.78).mean())
    dark = gray < 0.68
    dark_ratio = float(dark.mean())
    if light_ratio < 0.42 or not (0.015 <= dark_ratio <= 0.48):
        return ""

    visited = np.zeros(dark.shape, dtype=bool)
    height, width = dark.shape
    small_components = 0
    total_components = 0
    for y in range(height):
        for x in range(width):
            if not dark[y, x] or visited[y, x]:
                continue
            total_components += 1
            stack = [(y, x)]
            visited[y, x] = True
            area = 0
            min_y = max_y = y
            min_x = max_x = x
            while stack:
                cy, cx = stack.pop()
                area += 1
                min_y, max_y = min(min_y, cy), max(max_y, cy)
                min_x, max_x = min(min_x, cx), max(max_x, cx)
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < height and 0 <= nx < width and dark[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            box_w = max_x - min_x + 1
            box_h = max_y - min_y + 1
            if 2 <= area <= 90 and box_w <= 28 and box_h <= 28:
                small_components += 1
            if total_components > 700:
                break
        if total_components > 700:
            break

    row_ink = dark.mean(axis=1)
    rows_with_ink = float((row_ink > 0.01).mean())
    text_line_count = 0
    in_line = False
    for active in row_ink > 0.012:
        if active and not in_line:
            text_line_count += 1
            in_line = True
        elif not active:
            in_line = False
    if light_ratio > 0.70 and dark_ratio > 0.018 and text_line_count >= 10:
        return "大段文字/书页截图"
    if small_components >= 90 and rows_with_ink > 0.32:
        return "大段文字/书页截图"
    if small_components >= 140:
        return "文字密集图片"
    return ""


def _table_or_document_reason(item: dict) -> str:
    if Image is None or np is None:
        return ""
    path = Path(item.get("local_path") or "")
    if not path.exists():
        return ""
    try:
        image = Image.open(path).convert("L")
        image.thumbnail((320, 320))
        gray = np.asarray(image, dtype=np.float32) / 255.0
    except Exception:
        return ""
    if gray.size == 0:
        return ""

    height, width = gray.shape
    if float((gray > 0.74).mean()) < 0.42:
        return ""
    gx = np.abs(np.diff(gray, axis=1, append=gray[:, -1:]))
    gy = np.abs(np.diff(gray, axis=0, append=gray[-1:, :]))
    vertical_lines = gx > 0.20
    horizontal_lines = gy > 0.20
    col_hits = int((vertical_lines.mean(axis=0) > 0.22).sum())
    row_hits = int((horizontal_lines.mean(axis=1) > 0.20).sum())
    row_ink = (gray < 0.55).mean(axis=1)
    many_text_rows = int((row_ink > 0.025).sum()) > height * 0.28
    if row_hits >= 8 and col_hits >= 3 and many_text_rows:
        return "table/document screenshot"
    if row_hits >= 14 and many_text_rows:
        return "document/list screenshot"
    return ""


def _large_title_card_reason(item: dict) -> str:
    if Image is None or np is None:
        return ""
    path = Path(item.get("local_path") or "")
    if not path.exists():
        return ""
    try:
        image = Image.open(path).convert("RGB")
        image.thumbnail((360, 360))
        arr = np.asarray(image, dtype=np.float32) / 255.0
    except Exception:
        return ""
    if arr.size == 0:
        return ""

    maxc = arr.max(axis=2)
    minc = arr.min(axis=2)
    saturation = (maxc - minc) / np.maximum(maxc, 0.001)
    gray = arr.mean(axis=2)
    height, width = gray.shape
    red_or_yellow_text = (
        ((arr[:, :, 0] > 0.62) & (arr[:, :, 1] < 0.45) & (arr[:, :, 2] < 0.45))
        | ((arr[:, :, 0] > 0.70) & (arr[:, :, 1] > 0.55) & (arr[:, :, 2] < 0.34))
        | ((arr[:, :, 2] > 0.62) & (arr[:, :, 0] < 0.50) & (arr[:, :, 1] > 0.42))
    )
    white_title = (gray > 0.78) & (saturation < 0.24)
    title_fill = red_or_yellow_text | white_title
    title_boxes = []
    for x1, y1, x2, y2, area in _component_boxes_bool(title_fill, max_components=1200):
        box_w = x2 - x1
        box_h = y2 - y1
        if area < width * height * 0.004:
            continue
        if box_w > width * 0.10 and box_h > height * 0.045 and box_h < height * 0.42:
            title_boxes.append((x1, y1, x2, y2, area))
    if len(title_boxes) >= 2:
        x1 = min(box[0] for box in title_boxes)
        y1 = min(box[1] for box in title_boxes)
        x2 = max(box[2] for box in title_boxes)
        y2 = max(box[3] for box in title_boxes)
        covered = ((x2 - x1) * (y2 - y1)) / max(width * height, 1)
        fill = sum(box[4] for box in title_boxes) / max(width * height, 1)
        if covered > 0.10 and fill > 0.018:
            return "large colored title text / thumbnail cover"

    gx = np.abs(np.diff(gray, axis=1, append=gray[:, -1:]))
    gy = np.abs(np.diff(gray, axis=0, append=gray[-1:, :]))
    high_contrast = ((gx + gy) > 0.16) & ((gray < 0.28) | (gray > 0.72) | (saturation > 0.46))

    boxes = _component_boxes_bool(high_contrast, max_components=1400)
    large_boxes = []
    for x1, y1, x2, y2, area in boxes:
        box_w = x2 - x1
        box_h = y2 - y1
        if box_w < width * 0.08 or box_h < height * 0.035:
            continue
        if box_h > height * 0.42:
            continue
        if area < width * height * 0.0015:
            continue
        large_boxes.append((x1, y1, x2, y2, area))

    if large_boxes:
        x1 = min(box[0] for box in large_boxes)
        y1 = min(box[1] for box in large_boxes)
        x2 = max(box[2] for box in large_boxes)
        y2 = max(box[3] for box in large_boxes)
        covered = ((x2 - x1) * (y2 - y1)) / max(width * height, 1)
        ink = sum(box[4] for box in large_boxes) / max(width * height, 1)
        colorful = float((saturation > 0.38).mean())
        if len(large_boxes) >= 3 and covered > 0.16 and ink > 0.018:
            return "large title text / thumbnail cover"
        if len(large_boxes) >= 2 and colorful > 0.22 and covered > 0.12:
            return "large title text / poster"

    return ""


def _icon_or_logo_reason(item: dict) -> str:
    if Image is None or np is None:
        return ""
    path = Path(item.get("local_path") or "")
    if not path.exists():
        return ""
    try:
        image = Image.open(path).convert("RGB")
        image.thumbnail((260, 260))
        arr = np.asarray(image, dtype=np.float32) / 255.0
    except Exception:
        return ""
    if arr.size == 0:
        return ""

    maxc = arr.max(axis=2)
    minc = arr.min(axis=2)
    saturation = (maxc - minc) / np.maximum(maxc, 0.001)
    gray = arr.mean(axis=2)
    light_bg = float((gray > 0.82).mean())
    dark_area = float((gray < 0.18).mean())
    saturated_area = float((saturation > 0.42).mean())
    yellow_red_blue = (
        ((arr[:, :, 0] > 0.72) & (arr[:, :, 1] > 0.48) & (arr[:, :, 2] < 0.28))
        | ((arr[:, :, 0] > 0.58) & (arr[:, :, 1] < 0.36) & (arr[:, :, 2] < 0.36))
        | ((arr[:, :, 2] > 0.55) & (arr[:, :, 0] < 0.45))
    )
    emblem_colors = float(yellow_red_blue.mean())

    quantized = (arr * 8).astype(np.int16)
    _, counts = np.unique(quantized.reshape(-1, 3), axis=0, return_counts=True)
    top_cover = float(np.sort(counts)[-12:].sum() / max(counts.sum(), 1)) if len(counts) else 0.0

    edges = (
        np.abs(np.diff(gray, axis=1, append=gray[:, -1:]))
        + np.abs(np.diff(gray, axis=0, append=gray[-1:, :]))
    ) > 0.18
    foreground = (saturation > 0.34) | (gray < 0.22)
    boxes = _component_boxes_bool(foreground, max_components=700)
    large_components = [
        box for box in boxes
        if box[4] > foreground.size * 0.035
        and (box[2] - box[0]) > foreground.shape[1] * 0.18
        and (box[3] - box[1]) > foreground.shape[0] * 0.18
    ]

    if light_bg > 0.35 and top_cover > 0.72 and saturated_area + dark_area > 0.18 and len(large_components) <= 4:
        return "icon/logo/sign graphic"
    if emblem_colors > 0.30 and top_cover > 0.65 and float(edges.mean()) > 0.035 and len(large_components) <= 5:
        return "emblem/warning/logo graphic"
    return ""


def _local_design_reasons(item: dict) -> list[str]:
    reasons = []
    for detector in (_table_or_document_reason, _large_title_card_reason, _icon_or_logo_reason):
        reason = detector(item)
        if reason:
            reasons.append(reason)
    return list(dict.fromkeys(reasons))


def _non_photo_reasons(item: dict, tags: dict | None, all_text: str, *, visual: bool) -> list[str]:
    tags = tags or {}
    reasons: list[str] = []
    media_type = str(tags.get("media_type") or "").strip().lower()
    if media_type in NON_PHOTO_MEDIA_TYPES:
        reasons.append(f"media_type={media_type}")
    real_photo_value = tags.get("is_real_photo")
    if real_photo_value is False or str(real_photo_value).strip().lower() in {"false", "no", "0"}:
        reasons.append("非真实照片")
    style_text = _text_parts(tags.get("visual_style"), tags.get("media_type"), tags.get("keywords"), all_text)
    hits = _contains_any(style_text, NON_PHOTO_WORDS)
    if hits:
        reasons.extend(hits[:3])
    text_hits = _contains_any(style_text, TEXT_HEAVY_WORDS)
    if text_hits:
        reasons.extend(text_hits[:3])
    design_hits = _contains_any(style_text, DESIGN_HEAVY_WORDS)
    if design_hits:
        reasons.extend(design_hits[:3])
    ui_reason = _ui_screenshot_reason(item)
    if ui_reason:
        reasons.append(ui_reason)
    text_reason = _text_heavy_reason(item)
    if text_reason:
        reasons.append(text_reason)
    reasons.extend(_local_design_reasons(item))
    return list(dict.fromkeys(reasons))


def _score_from_evidence(shot: dict, item: dict, visual_text: str, tags: dict | None = None, *, visual: bool = False) -> dict:
    keywords = _as_list(shot.get("search_keywords"))[:3]
    subject_terms = _as_list(shot.get("required_object"))
    scene_terms = _as_list(shot.get("required_scene"))
    visual_terms = _as_list(shot.get("visual_intent")) + _as_list(shot.get("visual_need"))

    meta_text = _text_parts(item.get("title"), item.get("keyword"), item.get("source"), item.get("source_page"), item.get("image_url"))
    all_text = _text_parts(visual_text, meta_text)

    subject_partial = 8 if visual else 4
    scene_partial = 7 if visual else 3
    subject_score, subject_matched, subject_missing = _score_overlap(subject_terms or keywords[:1], all_text, WEIGHTS["subject"], partial=subject_partial)
    scene_score, scene_matched, scene_missing = _score_overlap(scene_terms + visual_terms, all_text, WEIGHTS["scene"], partial=scene_partial)
    keyword_score, keyword_matched, keyword_missing = _score_overlap(keywords, all_text, WEIGHTS["keyword"], partial=3)

    era_hits = _contains_any(all_text, ERA_WORDS)
    era_score = 10 if era_hits else 6
    if _contains_any(all_text, LOW_TRUST_WORDS):
        era_score -= 4
    era_score = max(0, min(WEIGHTS["era_style"], era_score + min(5, len(era_hits))))

    details = {
        "subject": subject_score,
        "scene": scene_score,
        "era_style": era_score,
        "keyword": keyword_score,
        "quality": _quality_score(item),
        "source": _source_score(meta_text, item.get("source_page") or ""),
        "aspect_1_1": aspect_ratio_score(item),
    }
    score = sum(details.values())
    aspect_score = details["aspect_1_1"]
    if aspect_score == 0:
        score = min(score, 40)
    elif aspect_score < 5:
        score = min(score, 55)
    elif aspect_score >= 8:
        score = min(score + 5, 100)
    non_photo_reasons = _non_photo_reasons(item, tags, all_text, visual=visual)
    if non_photo_reasons:
        details["quality"] = min(details["quality"], 2)
        details["source"] = min(details["source"], 1)
        score = min(sum(details.values()), 30)
    matched = list(dict.fromkeys(subject_matched + scene_matched + keyword_matched + era_hits))
    missing = list(dict.fromkeys(subject_missing + scene_missing + keyword_missing))[:8]
    level = "good" if score >= 75 else "ok" if score >= 60 else "weak"
    reason = "；".join([
        f"主体{details['subject']}/{WEIGHTS['subject']}",
        f"场景{details['scene']}/{WEIGHTS['scene']}",
        f"时代风格{details['era_style']}/{WEIGHTS['era_style']}",
        f"关键词{details['keyword']}/{WEIGHTS['keyword']}",
        f"画质{details['quality']}/{WEIGHTS['quality']}",
        f"来源{details['source']}/{WEIGHTS['source']}",
    ])
    if non_photo_reasons:
        reason = f"非真实照片/截图类素材，封顶30分：{', '.join(non_photo_reasons)}；{reason}"
    reason = f"{reason}；1:1{details['aspect_1_1']}/10"
    return {
        "score": score,
        "level": level,
        "reason": reason,
        "details": details,
        "matched": matched,
        "missing": list(dict.fromkeys([*missing, "真实历史照片"])) if non_photo_reasons else missing,
        "non_photo_reasons": non_photo_reasons,
        "hard_rejected": bool(non_photo_reasons) or score <= 40,
        "tags": tags or {},
        "scoring_provider": "visual" if visual else "quick",
    }


def quick_score_image_for_shot(shot: dict, item: dict) -> dict:
    return _score_from_evidence(shot, item, "", {}, visual=False)


def score_image_for_shot(shot: dict, item: dict) -> dict:
    path = Path(item.get("local_path") or "")
    tags = analyze_asset(item.get("file_name") or path.name, path if path.exists() else None, "image")
    visual_text = _text_parts(tags.get("object"), tags.get("scene"), tags.get("keywords"))
    return _score_from_evidence(shot, item, visual_text, tags, visual=True)


def rank_images_for_shot(shot: dict, downloaded: list[dict], *, visual_limit: int = 1) -> list[dict]:
    ranked: list[dict] = []
    for item in downloaded:
        scored = {**item, "score_result": quick_score_image_for_shot(shot, item)}
        ranked.append(scored)
    ranked.sort(key=lambda item: (aspect_ratio_score(item), item["score_result"]["score"]), reverse=True)

    for index, item in enumerate(ranked[:max(0, visual_limit)]):
        ranked[index] = {**item, "score_result": score_image_for_shot(shot, item)}
    return sorted(ranked, key=lambda item: (aspect_ratio_score(item), item["score_result"]["score"]), reverse=True)
