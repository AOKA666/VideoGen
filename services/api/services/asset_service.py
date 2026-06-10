from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

from services.text_service import keywords_from_text

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}
VIDEO_EXTS = {".mp4", ".mov", ".webm"}


def bigmodel_endpoint() -> str:
    return os.getenv("BIGMODEL_ENDPOINT", "https://open.bigmodel.cn/api/paas/v4")


def bigmodel_model() -> str:
    return os.getenv("BIGMODEL_MODEL", "glm-5.1")


def detect_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return "unknown"


def new_id() -> str:
    return str(uuid4())


def safe_storage_name(filename: str) -> str:
    name = Path(filename).name
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned or "asset"


def fallback_analyze_asset(filename: str, file_type: str = "image") -> dict[str, Any]:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ")
    tags = keywords_from_text(stem)
    tags.update({
        "media_type": "photo" if file_type == "image" else "video",
        "analysis_provider": "local_fallback",
    })
    return normalize_tags(tags, file_type)


def normalize_tags(tags: dict[str, Any], file_type: str) -> dict[str, Any]:
    def as_list(key: str) -> list[str]:
        value = tags.get(key, [])
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            value = []
        return [str(item).strip() for item in value if str(item).strip()][:12]

    return {
        "object": as_list("object") or as_list("people"),
        "scene": as_list("scene"),
        "keywords": as_list("keywords"),
        "media_type": tags.get("media_type") or ("photo" if file_type == "image" else "video"),
        "visual_style": str(tags.get("visual_style") or "").strip(),
        "is_real_photo": tags.get("is_real_photo"),
        "analysis_provider": tags.get("analysis_provider") or "glm",
        "analysis_error": tags.get("analysis_error", ""),
    }


def analyze_asset(filename: str, file_path: Path | None = None, file_type: str | None = None) -> dict[str, Any]:
    detected_type = file_type or detect_file_type(filename)
    fallback = fallback_analyze_asset(filename, detected_type)
    if detected_type != "image":
        return fallback

    api_key = os.getenv("BIGMODEL_API_KEY", "").strip()
    if not api_key or not file_path or not file_path.exists():
        return fallback

    try:
        glm_tags = analyze_image_with_glm(filename, file_path, api_key)
        merged = {**fallback, **glm_tags, "analysis_provider": bigmodel_model()}
        return normalize_tags(merged, detected_type)
    except Exception as exc:
        fallback["analysis_error"] = str(exc)[:300]
        return fallback


def analyze_image_with_glm(filename: str, file_path: Path, api_key: str) -> dict[str, Any]:
    mime = mimetypes.guess_type(filename)[0] or "image/jpeg"
    image_b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
    prompt = (
        '你是短视频素材库的图片打标助手。请根据图片内容和文件名生成精确标签。'
        '规则：'
        '1. object（主体标签）：图片中可识别的人、物、建筑、标志物等核心对象，1-3个词，每个2-6字。必须是具体可识别的对象，如【钱学森】【核潜艇】【纪念碑】。'
        '2. scene（场景标签）：图片发生的地点、环境，1-2个词，每个2-6字。如【实验室】【戈壁滩】【会议室】。无法判断则留空数组。'
        '3. keywords（关键词）：独立描述这张图体现的内容，1-3个词，每个2-8字。站在图片搜索角度，想想搜什么词能找到这张图。禁止使用【老照片】【历史档案】【历史画面】【纪实画面】等泛化词。'
        '4. 人物识别规则：如果图片主体是独立人物，必须尽量给出该人物的真名（如【钱学森】【邓稼先】），严禁使用【女科学家】【男教授】【老妇人】【中年男人】等泛化描述代替人名。只有确实无法确认身份的群像或路人角色才可用泛化词。'
        '词不在多，在精确。不要凑数，每个标签都必须有区分度。'
        '只返回 JSON，不要 Markdown。字段必须包含：'
        'object, scene, keywords。'
        'object/scene/keywords 都是中文字符串数组；'
        f'文件名：{filename}'
    )
    prompt += (
        "\n\n重要补充规则：必须判断图片是否真实照片。"
        "软件截图、网页截图、CAD/三维建模界面、三维渲染图、插画、海报、PPT、图表、表情包都不是照片。"
        "返回 JSON 必须额外包含 media_type, visual_style, is_real_photo。"
        "media_type 只能是 photo、screenshot、render、illustration、poster、document、chart 之一。"
        "非真实照片的 is_real_photo 必须为 false。"
    )
    prompt += "\n一页书、文章截图、帖子截图、评论截图、聊天记录、大段文字图片也不是可用分镜照片，is_real_photo 必须为 false。"
    payload = {
        "model": bigmodel_model(),
        "messages": [
            {"role": "system", "content": "你只输出可解析 JSON。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                ],
            },
        ],
        "temperature": 0.2,
        "stream": False,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{bigmodel_endpoint()}/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GLM API {exc.code}: {error_body}") from exc

    content = body["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return json.loads(extract_json(str(content)))


def extract_json(text: str) -> str:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("GLM response does not contain JSON")
    return match.group(0)
