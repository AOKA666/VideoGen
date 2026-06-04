from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from difflib import SequenceMatcher

PERSON_HINTS = ["钱学森", "邓稼先", "于敏", "黄旭华", "郭永怀", "袁隆平", "王淦昌", "两弹一星"]
SCENE_HINTS = ["实验室", "会议", "档案", "照片", "火箭", "导弹", "核潜艇", "宿舍", "办公室", "手稿", "文件"]
ERA_HINTS = ["1950", "1960", "1970", "上世纪", "建国", "抗战"]
MIN_REWRITE_LENGTH_RATIO = 0.85
MAX_REWRITE_LENGTH_RATIO = 1.25
MIN_REWRITE_DIFFERENCE = 45
MAX_REWRITE_ATTEMPTS = 3


class RewriteQualityError(RuntimeError):
    def __init__(self, result: dict):
        comparison = result.get("rewrite_comparison") or {}
        difference = comparison.get("overall_difference", 0)
        super().__init__(f"rewrite difference {difference}% below {MIN_REWRITE_DIFFERENCE}%")
        self.result = result


def content_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def lcs_length(text1: str, text2: str) -> int:
    if not text1 or not text2:
        return 0
    previous = [0] * (len(text2) + 1)
    for char1 in text1:
        current = [0]
        for index2, char2 in enumerate(text2, start=1):
            if char1 == char2:
                current.append(previous[index2 - 1] + 1)
            else:
                current.append(max(previous[index2], current[-1]))
        previous = current
    return previous[-1]


def segment_for_similarity(text: str) -> list[str]:
    cleaned = re.sub(r"[，。！？；：、“”‘’《》【】（）,.!?;:'\"<>\[\]\(\)]", " ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    words: list[str] = []
    for length in range(4, 1, -1):
        for index in range(0, max(0, len(cleaned) - length + 1)):
            word = cleaned[index:index + length]
            if not re.search(r"\s", word):
                words.append(word)
    words.extend(char for char in cleaned if not re.search(r"\s", char))
    return words


def cosine_similarity(words1: list[str], words2: list[str]) -> float:
    counter1 = Counter(words1)
    counter2 = Counter(words2)
    all_words = set(counter1) | set(counter2)
    if not all_words:
        return 0.0
    dot_product = sum(counter1[word] * counter2[word] for word in all_words)
    magnitude1 = sum(value * value for value in counter1.values()) ** 0.5
    magnitude2 = sum(value * value for value in counter2.values()) ** 0.5
    if not magnitude1 or not magnitude2:
        return 0.0
    return dot_product / (magnitude1 * magnitude2)


def compare_scripts(text1: str, text2: str) -> dict:
    text1 = text1 or ""
    text2 = text2 or ""
    total_chars = len(text1) + len(text2)
    char_similarity = round((lcs_length(text1, text2) * 2 / total_chars) * 100) if total_chars else 0

    words1 = segment_for_similarity(text1)
    words2 = segment_for_similarity(text2)
    set1 = set(words1)
    set2 = set(words2)
    intersection = set1 & set2
    union = set1 | set2
    jaccard_similarity = len(intersection) / len(union) if union else 0
    semantic_similarity = round(((jaccard_similarity + cosine_similarity(words1, words2)) / 2) * 100)
    overall_similarity = round((char_similarity + semantic_similarity) / 2)
    overall_difference = max(0, min(100, 100 - overall_similarity))

    return {
        "character_similarity": char_similarity,
        "semantic_similarity": semantic_similarity,
        "overall_difference": overall_difference,
        "text1_length": len(text1),
        "text2_length": len(text2),
        "common_keywords": [word for word in intersection if len(word) >= 2][:10],
        "unique_keywords1": [word for word in set1 - set2 if len(word) >= 2][:10],
        "unique_keywords2": [word for word in set2 - set1 if len(word) >= 2][:10],
        "passed": overall_difference >= MIN_REWRITE_DIFFERENCE,
    }


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*", text.strip())
    return [p.strip() for p in parts if p.strip()]


def paragraphize_script(text: str) -> str:
    sentences = split_sentences(text)
    if sentences:
        return "\n".join(sentences)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        return "\n".join(lines)
    return text.strip()


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
    if content_length(raw_script) and content_length(rewritten) < int(content_length(raw_script) * MIN_REWRITE_LENGTH_RATIO):
        rewritten = paragraphize_script(raw_script)
    return {
        "title": title,
        "hook": hook,
        "rewritten_script": rewritten,
        "script_style": style,
        "rewrite_provider": "local_fallback",
        "rewrite_error": "",
        "rewrite_comparison": compare_scripts(raw_script, rewritten),
    }


def normalize_rewrite_result(result: dict, raw_script: str, style: str) -> dict:
    title = str(result.get("title") or infer_title(raw_script)).strip()
    hook = str(result.get("hook") or f"{title}，为什么值得被今天的人重新看见？").strip()
    rewritten_script = str(result.get("rewritten_script") or "").strip()
    if not rewritten_script:
        rewritten_script = fallback_rewrite_script(raw_script, style)["rewritten_script"]
    rewritten_script = clean_rewritten_script(raw_script, rewritten_script)
    rewritten_script = preserve_opening_hook(raw_script, rewritten_script)
    comparison = compare_scripts(raw_script, rewritten_script)
    return {
        "title": title[:40] or infer_title(raw_script),
        "hook": hook,
        "rewritten_script": rewritten_script,
        "script_style": str(result.get("script_style") or style),
        "rewrite_provider": result.get("rewrite_provider") or bigmodel_model(),
        "rewrite_error": result.get("rewrite_error", ""),
        "rewrite_comparison": comparison,
        "rewrite_difference": comparison["overall_difference"],
    }


def build_rewrite_prompt(raw_script: str, style: str, attempt: int, previous: dict | None = None) -> str:
    opening_hook = extract_opening_hook(raw_script)
    raw_len = content_length(raw_script)
    min_len = int(raw_len * MIN_REWRITE_LENGTH_RATIO)
    max_len = int(raw_len * MAX_REWRITE_LENGTH_RATIO)
    retry_instruction = ""
    if previous:
        comparison = previous.get("rewrite_comparison") or {}
        retry_instruction = (
            f"上一版总体差异度只有 {comparison.get('overall_difference', 0)}%，未达到 {MIN_REWRITE_DIFFERENCE}%。"
            f"字符相似度 {comparison.get('character_similarity', 0)}%，语义相似度 {comparison.get('semantic_similarity', 0)}%。"
            "这说明上一版仍然太像原文。请不要继续做同义词替换，必须重新组织正文：改变叙述视角、句子顺序、铺垫方式、转折方式和情绪推进。"
        )
    prompt = (
        "你是历史纪实短视频二创编剧，不是润色编辑。你的任务是把原始文案重写成另一版完整口播稿。"
        "你只能改写 <raw_script> 标签内的内容，禁止替换主题，禁止改成其他历史事件。"
        "事实边界：不虚构；不添加没有依据的具体时间、地点、人物关系；人物、年代、事件、因果关系必须保留。"
        "改写目标：保留事实点和信息量，但重建表达方式。不要逐句翻译、不要逐句扩写、不要只做同义词替换。"
        f"原文去除空白后的长度约 {raw_len} 个中文字符；二创稿不能写得太简略，"
        f"rewritten_script 去除空白后的长度必须控制在 {min_len} 到 {max_len} 个中文字符之间，"
        "信息量、叙事层次和关键细节要和原文案接近，禁止压缩成摘要、提纲或短版解说，也不要省略原文中的重要事实。"
        "开头黄金三秒文案必须一个字、一个标点都不改，并放在 rewritten_script 最开头；"
        f"需要原样保留的开头黄金三秒是：{opening_hook}"
        f"除开头黄金三秒之外，剩余内容必须大改；系统会用字符相似度和语义相似度自动对比，最终总体差异度必须达到 {MIN_REWRITE_DIFFERENCE}% 以上。"
        "后文改写硬性规则："
        "1. 不要保留原文连续 8 个字以上的表达，专有名词、年份和固定称谓除外。"
        "2. 原文每个事实点都要覆盖，但可以换叙述顺序、换句式、换铺垫、换转折。"
        "3. 把原文的直述句尽量改成悬念句、因果句、补充说明句或镜头化描述。"
        "4. 能换表达就换表达，例如“远赴重洋赴美留学”不要照抄，可改成“踏上去美国求学的路”。"
        "5. 不要连续沿用原文的段落结构；可以把一个长句拆成两段，也可以把相邻短句重组为一段。"
        "6. 不要为了差异度删除内容，必须用新的说法把信息补回来。"
        "二创写法建议：先在心里提取事实清单，再用新的叙事路径重写；可以从人物选择、时代压力、行动细节、结果意义等角度重新组织。"
        "全面校对错别字、语病和标点错误，输出必须是通顺、干净的成稿。"
        "原文案中呼吁点赞互动的钩子不要删除掉，但必须改写成更吸引人的表达；如果原文没有明显钩子，可以自己添加一个，放在开头黄金三秒之后。"
        "必须根据语义分段，一个完整意思一段；每段就是后续一个分镜，不要把多个意思放在同一段，同样的意思也不要分段；"
        "每段尽量 20 到 50 个中文字符，适合配音；如果为了保持总字数需要，可以保留更多分段，不要删减事实细节。不要输出 Markdown。"
        f"文案风格：{style}。"
        f"这是第 {attempt} 次生成。{retry_instruction}"
        "rewritten_script 字段里只能放最终二创口播稿正文，禁止包含原文、原始文案、对照稿、解释、标题标签或“二创口播稿：”这类前缀。"
        "不要先输出一遍原文再输出二创稿，也不要把原文和二创内容混在一起。"
        "只返回 JSON，字段必须包含 title, hook, rewritten_script, script_style。"
        f"<raw_script>{raw_script}</raw_script>"
    )
    return prompt


def rewrite_script_with_glm(raw_script: str, style: str, api_key: str) -> dict:
    raw_len = content_length(raw_script)
    best_result: dict | None = None
    last_result: dict | None = None
    for attempt in range(1, MAX_REWRITE_ATTEMPTS + 1):
        prompt = build_rewrite_prompt(raw_script, style, attempt, last_result)
        result = request_glm_rewrite(prompt, raw_script, style, api_key, raw_len)
        comparison = result.get("rewrite_comparison") or {}
        if not best_result or comparison.get("overall_difference", 0) > (best_result.get("rewrite_comparison") or {}).get("overall_difference", 0):
            best_result = result
        if comparison.get("overall_difference", 0) >= MIN_REWRITE_DIFFERENCE:
            result["rewrite_attempts"] = attempt
            return result
        last_result = result

    assert best_result is not None
    comparison = best_result.get("rewrite_comparison") or {}
    best_result["rewrite_attempts"] = MAX_REWRITE_ATTEMPTS
    best_result["rewrite_error"] = (
        f"rewrite difference {comparison.get('overall_difference', 0)}% below {MIN_REWRITE_DIFFERENCE}% after {MAX_REWRITE_ATTEMPTS} attempts"
    )
    raise RewriteQualityError(best_result)


def request_glm_rewrite(prompt: str, raw_script: str, style: str, api_key: str, raw_len: int) -> dict:
    payload = {
        "model": bigmodel_model(),
        "messages": [
            {"role": "system", "content": "你只输出可解析 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.65,
        "top_p": 0.85,
        "max_tokens": max(4096, min(12000, raw_len * 4)),
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
    except RewriteQualityError:
        raise
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
    for idx, text in enumerate(chunks, 1):
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
