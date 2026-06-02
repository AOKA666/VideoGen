from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from difflib import SequenceMatcher

PERSON_HINTS = ["钱学森", "邓稼先", "于敏", "黄旭华", "郭永怀", "袁隆平", "王淦昌", "两弹一星"]
SCENE_HINTS = ["实验室", "会议", "档案", "照片", "火箭", "导弹", "核潜艇", "宿舍", "办公室", "手稿", "文件"]
ERA_HINTS = ["1950", "1960", "1970", "上世纪", "建国", "抗战"]


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*", text.strip())
    return [p.strip() for p in parts if p.strip()]


def infer_title(raw_script: str) -> str:
    first = split_sentences(raw_script)[0] if raw_script.strip() else "未命名项目"
    return first[:24].strip("，。！？ ") or "未命名项目"


def extract_opening_hook(raw_script: str) -> str:
    sentences = split_sentences(raw_script)
    for line in raw_script.splitlines():
        cleaned = line.strip()
        if cleaned and len(cleaned) <= 90:
            return cleaned
        if cleaned and sentences:
            return sentences[0].strip()
    return sentences[0].strip() if sentences else ""


def preserve_opening_hook(raw_script: str, rewritten_script: str) -> str:
    hook = extract_opening_hook(raw_script)
    rewritten = rewritten_script.strip()
    if not hook or rewritten.startswith(hook):
        return rewritten

    lines = [line.strip() for line in rewritten.splitlines() if line.strip()]
    body_lines = lines[1:] if lines and is_similar_text(lines[0], hook) else lines
    body = "\n".join(body_lines).strip()
    return f"{hook}\n{body}" if body else hook


def compact_text(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)


def is_similar_text(a: str, b: str) -> bool:
    left = compact_text(a)
    right = compact_text(b)
    if not left or not right:
        return False
    return SequenceMatcher(None, left, right).ratio() >= 0.45


def clean_rewritten_script(raw_script: str, rewritten_script: str) -> str:
    text = rewritten_script.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"</?raw_script>", "", text, flags=re.I).strip()

    rewrite_markers = r"(?:二创口播稿|二创文案|改写稿|改写后文案|改写后|成稿|rewritten_script)\s*[:：]\s*"
    marker_parts = re.split(rewrite_markers, text, flags=re.I)
    if len(marker_parts) > 1 and marker_parts[-1].strip():
        text = marker_parts[-1].strip()

    raw_section = r"(?:原文|原始文案|raw_script)\s*[:：]\s*.*?(?=(?:二创口播稿|二创文案|改写稿|改写后文案|改写后|成稿|rewritten_script)\s*[:：]|$)"
    text = re.sub(raw_section, "", text, flags=re.S | re.I).strip()

    raw_clean = raw_script.strip()
    if raw_clean and raw_clean in text:
        text = text.replace(raw_clean, "").strip()

    lines = []
    seen = set()
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned in {"原文", "原始文案", "二创", "二创文案", "二创口播稿", "改写稿"}:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        lines.append(cleaned)
    return "\n".join(lines).strip()


def bigmodel_endpoint() -> str:
    return os.getenv("BIGMODEL_ENDPOINT", "https://open.bigmodel.cn/api/paas/v4")


def bigmodel_model() -> str:
    return os.getenv("BIGMODEL_MODEL", "glm-5.1")


def fallback_rewrite_script(raw_script: str, style: str = "纪实故事型") -> dict:
    sentences = split_sentences(raw_script)
    title = infer_title(raw_script)
    hook = extract_opening_hook(raw_script) or f"{title}，为什么值得被今天的人重新看见？"
    body = []
    if sentences:
        body.append(hook)
        for sentence in sentences:
            cleaned = sentence.strip()
            if cleaned == hook:
                continue
            if len(cleaned) < 8:
                continue
            body.append(cleaned)
        body.append("这些真实细节，比任何夸张的渲染都更有力量。")
    rewritten = "\n".join(body) if body else hook
    return {
        "title": title,
        "hook": hook,
        "rewritten_script": rewritten,
        "script_style": style,
        "rewrite_provider": "local_fallback",
        "rewrite_error": "",
    }


def normalize_rewrite_result(result: dict, raw_script: str, style: str) -> dict:
    title = str(result.get("title") or infer_title(raw_script)).strip()
    hook = str(result.get("hook") or f"{title}，为什么值得被今天的人重新看见？").strip()
    rewritten_script = str(result.get("rewritten_script") or "").strip()
    if not rewritten_script:
        rewritten_script = fallback_rewrite_script(raw_script, style)["rewritten_script"]
    rewritten_script = clean_rewritten_script(raw_script, rewritten_script)
    rewritten_script = preserve_opening_hook(raw_script, rewritten_script)
    return {
        "title": title[:40] or infer_title(raw_script),
        "hook": hook,
        "rewritten_script": rewritten_script,
        "script_style": str(result.get("script_style") or style),
        "rewrite_provider": result.get("rewrite_provider") or bigmodel_model(),
        "rewrite_error": result.get("rewrite_error", ""),
    }


def rewrite_script_with_glm(raw_script: str, style: str, api_key: str) -> dict:
    opening_hook = extract_opening_hook(raw_script)
    prompt = (
        "你是历史纪实短视频口播文案改写助手。请把原始文案改写成适合短视频旁白的中文稿。"
        "你只能改写 <raw_script> 标签内的内容，禁止替换主题，禁止改成其他历史事件。"
        "要求：事实不虚构；不添加没有依据的具体时间、地点、人物关系；语言自然、有画面感；"
        "必须先通读全文再整体改写，不要一句一句机械对应原文。"
        "开头黄金三秒文案必须一个字、一个标点都不改，并放在 rewritten_script 最开头；"
        f"需要原样保留的开头黄金三秒是：{opening_hook}"
        "除开头黄金三秒之外，剩余内容要大改，整体修改程度保持 50% 以上；"
        "全面校对错别字、语病和标点错误，输出必须是通顺、干净的成稿。"
        "原文案中呼吁点赞互动的钩子不要删除掉，但可以改写成更吸引人的表达；如果原文没有明显钩子，可以自己添加一个，放在开头黄金三秒之后。"
        "必须根据语义分段，一个完整意思一段；每段就是后续一个分镜，不要把多个意思放在同一段，同样的意思也不要分段；"
        "每段尽量 20 到 40 个中文字符，适合配音；不要输出 Markdown。"
        f"文案风格：{style}。"
        "rewritten_script 字段里只能放最终二创口播稿正文，禁止包含原文、原始文案、对照稿、解释、标题标签或“二创口播稿：”这类前缀。"
        "不要先输出一遍原文再输出二创稿，也不要把原文和二创内容混在一起。"
        "只返回 JSON，字段必须包含 title, hook, rewritten_script, script_style。"
        f"<raw_script>{raw_script}</raw_script>"
    )
    payload = {
        "model": bigmodel_model(),
        "messages": [
            {"role": "system", "content": "你只输出可解析 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.45,
        "top_p": 0.7,
        "max_tokens": 4096,
        "stream": False,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{bigmodel_endpoint().rstrip('/')}/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GLM API {exc.code}: {error_body}") from exc

    content = body["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    result = json.loads(extract_json(str(content)))
    result["rewrite_provider"] = bigmodel_model()
    return normalize_rewrite_result(result, raw_script, style)


def rewrite_script(raw_script: str, style: str = "纪实故事型") -> dict:
    fallback = fallback_rewrite_script(raw_script, style)
    api_key = os.getenv("BIGMODEL_API_KEY", "").strip()
    if not api_key:
        return fallback
    try:
        return rewrite_script_with_glm(raw_script, style, api_key)
    except Exception as exc:
        fallback["rewrite_error"] = str(exc)[:300]
        return fallback


def extract_json(text: str) -> str:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("GLM response does not contain JSON")
    return match.group(0)


def keywords_from_text(text: str) -> dict[str, list[str]]:
    people = [p for p in PERSON_HINTS if p in text]
    scenes = [s for s in SCENE_HINTS if s in text]
    eras = [e for e in ERA_HINTS if e in text]
    if not scenes:
        scenes = ["老照片", "历史档案"]
    if not eras:
        eras = ["历史年代"]
    emotion = ["庄重"] if any(w in text for w in ["国家", "保护", "牺牲", "秘密", "困难"]) else ["纪实"]
    keywords = list(dict.fromkeys(people + scenes + eras + emotion + re.findall(r"[\u4e00-\u9fff]{2,6}", text)[:6]))
    return {"people": people, "scene": scenes, "era": eras, "emotion": emotion, "keywords": keywords}


def is_meaningful_shot_text(text: str) -> bool:
    cleaned = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
    return bool(cleaned)


def generate_shots(script: str) -> list[dict]:
    lines = [line.strip() for line in script.splitlines() if is_meaningful_shot_text(line)]
    chunks: list[str] = lines if len(lines) > 1 else []
    if not chunks:
        for sentence in split_sentences(script):
            if len(sentence) <= 35:
                if is_meaningful_shot_text(sentence):
                    chunks.append(sentence)
                continue
            pieces = re.split(r"[，,、]", sentence)
            buf = ""
            for piece in pieces:
                if not piece:
                    continue
                candidate = f"{buf}，{piece}" if buf else piece
                if len(candidate) > 32 and buf:
                    if is_meaningful_shot_text(buf):
                        chunks.append(buf + "。")
                    buf = piece
                else:
                    buf = candidate
            if buf:
                if is_meaningful_shot_text(buf):
                    chunks.append(buf + "。")
    shots = []
    cursor = 0.0
    for idx, text in enumerate(chunks[:30], 1):
        tags = keywords_from_text(text)
        duration = max(3.0, min(6.0, round(len(text) / 7, 1)))
        required_object = tags["people"] or [
            item for item in tags["keywords"]
            if item not in tags["scene"] and item not in tags["era"] and item not in tags["emotion"]
        ][:2]
        required_scene = tags["scene"][:2] or ["历史档案"]
        visual_need = "、".join(required_object + required_scene) or "历史纪实画面"
        shots.append({
            "shot_index": idx,
            "voice_text": text,
            "duration_sec": duration,
            "start_time": round(cursor, 2),
            "end_time": round(cursor + duration, 2),
            "visual_need": visual_need,
            "required_object": required_object,
            "required_scene": required_scene,
            "selected_asset_id": None,
            "asset_source": None,
            "match_score": 0,
            "status": "no_match",
        })
        cursor += duration
    return shots
