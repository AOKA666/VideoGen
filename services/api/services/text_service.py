from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

PERSON_HINTS = ["钱学森", "邓稼先", "于敏", "黄旭华", "郭永怀", "袁隆平", "王淦昌", "两弹一星"]
SCENE_HINTS = ["实验室", "会议", "档案", "照片", "火箭", "导弹", "核潜艇", "宿舍", "办公室", "手稿", "文件"]
ERA_HINTS = ["1950", "1960", "1970", "上世纪", "建国", "抗战"]


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*", text.strip())
    return [p.strip() for p in parts if p.strip()]


def infer_title(raw_script: str) -> str:
    first = split_sentences(raw_script)[0] if raw_script.strip() else "未命名项目"
    return first[:24].strip("，。！？ ") or "未命名项目"


def bigmodel_endpoint() -> str:
    return os.getenv("BIGMODEL_ENDPOINT", "https://open.bigmodel.cn/api/paas/v4")


def bigmodel_model() -> str:
    return os.getenv("BIGMODEL_MODEL", "glm-5.1")


def fallback_rewrite_script(raw_script: str, level: str = "medium", style: str = "纪实故事型") -> dict:
    sentences = split_sentences(raw_script)
    title = infer_title(raw_script)
    hook = f"{title}，为什么值得被今天的人重新看见？"
    body = []
    if sentences:
        body.append(hook)
        for sentence in sentences:
            cleaned = sentence.strip()
            if len(cleaned) < 8:
                continue
            body.append(cleaned)
        body.append("这些真实细节，比任何夸张的渲染都更有力量。")
    rewritten = "\n".join(body) if body else hook
    if level == "strong":
        rewritten = rewritten.replace("。", "。\n")
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
        rewritten_script = fallback_rewrite_script(raw_script, "medium", style)["rewritten_script"]
    return {
        "title": title[:40] or infer_title(raw_script),
        "hook": hook,
        "rewritten_script": rewritten_script,
        "script_style": str(result.get("script_style") or style),
        "rewrite_provider": result.get("rewrite_provider") or bigmodel_model(),
        "rewrite_error": result.get("rewrite_error", ""),
    }


def rewrite_script_with_glm(raw_script: str, level: str, style: str, api_key: str) -> dict:
    level_map = {
        "light": "轻度改写：保留原文核心信息和叙事顺序，优化表达和节奏。",
        "medium": "中度改写：重组叙事节奏，强化短视频口播感，但不得改变事实。",
        "strong": "强度改写：显著增强开头钩子、情绪推进和段落张力，但不得虚构事实。",
    }
    prompt = (
        "你是历史纪实短视频口播文案改写助手。请把原始文案改写成适合短视频旁白的中文稿。"
        "你只能改写 <raw_script> 标签内的内容，禁止替换主题，禁止改成其他历史事件。"
        "要求：事实不虚构；不添加没有依据的具体时间、地点、人物关系；语言自然、有画面感；"
        "必须根据语义分行，一个完整意思一行；每行就是后续一个分镜，不要把多个意思放在同一行；"
        "每行尽量 8 到 28 个中文字符，适合配音；不要输出 Markdown。"
        f"改写强度：{level_map.get(level, level_map['medium'])}"
        f"文案风格：{style}。"
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


def rewrite_script(raw_script: str, level: str = "medium", style: str = "纪实故事型") -> dict:
    fallback = fallback_rewrite_script(raw_script, level, style)
    api_key = os.getenv("BIGMODEL_API_KEY", "").strip()
    if not api_key:
        return fallback
    try:
        return rewrite_script_with_glm(raw_script, level, style, api_key)
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
        visual_need = "、".join(tags["people"] + tags["scene"][:2]) or "历史纪实画面"
        exact = [f"{p} 老照片" for p in tags["people"]] or tags["keywords"][:3]
        alternative = [f"中国科学家 {s}" for s in tags["scene"][:2]]
        atmosphere = [f"历史档案 {e}" for e in tags["era"][:2]] + tags["emotion"]
        shots.append({
            "shot_index": idx,
            "voice_text": text,
            "duration_sec": duration,
            "start_time": round(cursor, 2),
            "end_time": round(cursor + duration, 2),
            "visual_need": visual_need,
            "exact_keywords": exact,
            "alternative_keywords": alternative,
            "atmosphere_keywords": atmosphere,
            "selected_asset_id": None,
            "asset_source": None,
            "match_score": 0,
            "status": "no_match",
        })
        cursor += duration
    return shots
