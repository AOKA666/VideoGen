from __future__ import annotations

import re

PERSON_HINTS = ["钱学森", "邓稼先", "于敏", "黄旭华", "郭永怀", "袁隆平", "王淦昌", "两弹一星"]
SCENE_HINTS = ["实验室", "会议", "档案", "照片", "火箭", "导弹", "核潜艇", "宿舍", "办公室", "手稿", "文件"]
ERA_HINTS = ["1950", "1960", "1970", "上世纪", "建国", "抗战"]


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*", text.strip())
    return [p.strip() for p in parts if p.strip()]


def infer_title(raw_script: str) -> str:
    first = split_sentences(raw_script)[0] if raw_script.strip() else "未命名项目"
    return first[:24].strip("，。！？ ") or "未命名项目"


def rewrite_script(raw_script: str, level: str = "medium", style: str = "纪实故事型") -> dict:
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
    return {"title": title, "hook": hook, "rewritten_script": rewritten, "script_style": style}


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


def generate_shots(script: str) -> list[dict]:
    sentences = split_sentences(script.replace("\n", "。"))
    chunks: list[str] = []
    for sentence in sentences:
        if len(sentence) <= 35:
            chunks.append(sentence)
            continue
        pieces = re.split(r"[，,、]", sentence)
        buf = ""
        for piece in pieces:
            if not piece:
                continue
            candidate = f"{buf}，{piece}" if buf else piece
            if len(candidate) > 32 and buf:
                chunks.append(buf + "。")
                buf = piece
            else:
                buf = candidate
        if buf:
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
