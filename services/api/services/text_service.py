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
        "原文案中如果有呼吁点赞互动的钩子，不要删除掉。"
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
    # No more "老照片"/"历史档案" fallbacks — only use tags that are actually found in text
    keywords = list(dict.fromkeys(people + scenes + eras + re.findall(r"[一-鿿]{2,6}", text)[:6]))
    return {"people": people, "scene": scenes, "era": eras, "keywords": keywords}


def is_meaningful_shot_text(text: str) -> bool:
    cleaned = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
    return bool(cleaned)


SHOT_VISUALS_BATCH_SIZE = 10


def _build_shot_visuals_prompt(shot_items: list[dict], full_script: str) -> str:
    return f"""你是短视频分镜画面设计专家。请根据每个分镜的旁白文字，生成画面描述、搜索关键词和素材匹配标签。

规则：
1. 画面描述（visual_need）应描述这个镜头应该出现什么画面，指导图片搜索方向，不要复述旁白内容。
2. 搜索关键词（search_keywords）应是可以直接用于中文图片搜索的词组，2-3个关键词，每个2-8个字。
3. 主体标签（object_tags）：画面中应该出现的人、物、建筑、标志物等核心主体，1-3个词，每个2-6字。必须是具体可识别的对象，如"钱学森""核潜艇""火车""纪念碑"。
4. 场景标签（scene_tags）：画面发生的地点、环境或氛围，1-2个词，每个2-6字。必须是具体场景，如"实验室""会议室""戈壁滩""码头"。如果无法确定具体场景，留空数组。
5. 关键词（keywords）：独立判断这个画面应该体现什么，1-3个词，每个2-8字。不要从旁白中提取或改写词汇，要站在图片搜索的角度，想想搜什么词能找到这张图。例如旁白说"他毅然放弃国外的优厚待遇回到祖国"，画面可能是"归国科学家走下飞机"，关键词应该是"归国科学家""留学回国"，而不是"优厚待遇""毅然放弃"。
6. 如果旁白明确描述某个具体人物、事件或场景，标签应聚焦该内容。
7. 禁止使用"老照片""历史档案""历史画面""纪实画面""相关画面"等泛化无意义词。
8. 画面描述要具体、可搜索，避免"相关画面""历史画面""纪实画面"等泛化描述。
9. 人物识别规则：如果画面主体是独立人物，必须尽量给出该人物的真名（如"钱学森""邓稼先"），严禁使用"女科学家""男教授""老妇人""中年男人"等泛化描述代替人名。只有确实无法确认身份的群像或路人角色才可用泛化词。
10. 人物性别（person_gender）必须根据全文和人物身份准确判断，只能填写 female、male、mixed、none、unknown。女性主体填 female，男性主体填 male，明确包含不同性别人物填 mixed，没有人物填 none，确实无法判断才填 unknown。不得根据科学家、军人、工程师等职业刻板猜测性别。
11. 人物姓名（person_names）列出画面中具体人物的姓名，仅用于系统内部识别；没有具体人物则返回空数组。
12. 匿名外貌描述（person_description）不得包含任何人物姓名，只描述性别、年龄段、脸型、发型、服装和气质，例如"八十岁左右的中国女性科学家，短灰发，清瘦脸型，戴细框眼镜，穿深色朴素外套，神情专注"。没有人物则返回空字符串。
13. visual_need 可以保留具体人物姓名以服务图片搜索，但人物性别必须与 person_gender 一致。
14. 只输出严格 JSON，不要 Markdown。

全文背景（仅用于消除歧义）：
{full_script}

分镜列表：
{json.dumps(shot_items, ensure_ascii=False)}

返回格式：
{{
  "shots": [
    {{
      "id": "分镜编号",
      "visual_need": "画面描述：描述这个镜头应该展示什么具体画面",
      "person_gender": "female|male|mixed|none|unknown",
      "person_names": ["具体人物姓名"],
      "person_description": "不含姓名的性别、年龄段和大概外貌描述",
      "search_keywords": ["搜索关键词1", "搜索关键词2"],
      "object_tags": ["主体1", "主体2"],
      "scene_tags": ["场景1"],
      "keywords": ["关键词1", "关键词2"]
    }}
  ]
}}""".strip()


def ai_generate_shot_visuals(shots: list[dict], full_script: str) -> dict[str, dict]:
    """Use GLM to generate visual_need and search_keywords for each shot."""
    api_key = os.getenv("BIGMODEL_API_KEY", "").strip()
    if not api_key:
        return {}

    all_visuals: dict[str, dict] = {}
    for batch_start in range(0, len(shots), SHOT_VISUALS_BATCH_SIZE):
        batch = shots[batch_start:batch_start + SHOT_VISUALS_BATCH_SIZE]
        shot_items = [
            {"id": str(shot["shot_index"]), "shot_index": shot["shot_index"], "voice_text": shot["voice_text"]}
            for shot in batch
        ]
        prompt = _build_shot_visuals_prompt(shot_items, full_script)
        payload = {
            "model": bigmodel_model(),
            "messages": [
                {"role": "system", "content": "你只输出可解析 JSON。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "top_p": 0.7,
            "max_tokens": max(2000, min(8000, len(batch) * 300)),
            "stream": False,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{bigmodel_endpoint().rstrip('/')}/chat/completions",
                data=data,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
            result = json.loads(extract_json(str(content)))
            for item in result.get("shots", []):
                shot_id = str(item.get("id") or item.get("shot_index") or "")
                visual_need = str(item.get("visual_need") or "").strip()
                person_gender = str(item.get("person_gender") or "unknown").strip().lower()
                if person_gender not in {"female", "male", "mixed", "none", "unknown"}:
                    person_gender = "unknown"
                person_names = [str(k).strip() for k in (item.get("person_names") or []) if str(k).strip()]
                person_description = str(item.get("person_description") or "").strip()
                for person_name in person_names:
                    person_description = person_description.replace(person_name, "")
                person_description = re.sub(r"\s+", " ", person_description).strip(" ，,。")
                search_keywords = [str(k).strip() for k in (item.get("search_keywords") or []) if str(k).strip()]
                object_tags = [str(k).strip() for k in (item.get("object_tags") or []) if str(k).strip()]
                scene_tags = [str(k).strip() for k in (item.get("scene_tags") or []) if str(k).strip()]
                keywords = [str(k).strip() for k in (item.get("keywords") or []) if str(k).strip()]
                if (
                    visual_need or person_names or person_description or search_keywords
                    or object_tags or scene_tags or keywords or person_gender != "unknown"
                ):
                    all_visuals[shot_id] = {
                        "visual_need": visual_need,
                        "person_gender": person_gender,
                        "person_names": person_names,
                        "person_description": person_description,
                        "search_keywords": search_keywords,
                        "object_tags": object_tags,
                        "scene_tags": scene_tags,
                        "keywords": keywords,
                    }
        except Exception:
            continue

    return all_visuals


def generate_viral_title(script: str) -> dict:
    """Use GLM to generate a two-line viral short video title based on script content."""
    api_key = os.getenv("BIGMODEL_API_KEY", "").strip()
    if not api_key:
        return {"line1": "", "line2": "", "full_title": ""}

    prompt = (
        "你是爆款短视频标题专家。请根据以下文案内容，生成一个两行式爆款标题。"
        "\n\n标题规则："
        "\n1. 必须生成两行文字，每行5-9字，任何一行都不能超过9个字。"
        "\n2. 标题要制造悬念或反差，让用户忍不住点开。"
        "\n3. 以下是爆款标题示范格式，请学习其逻辑但不要照搬："
        "\n   - 第一行：飞机坠毁前 / 第二行：他用身体护住了国家机密"
        "\n   - 第一行：母亲骂他三十年不孝 / 第二行：真相曝光后全国泪目"
        "\n   - 第一行：穿15块胶鞋的老太太 / 第二行：捐出了1000万"
        "\n4. 标题必须忠于文案事实，不能编造信息。"
        "\n5. 不要用「震惊」「惊人」「不可思议」等空洞词汇。"
        "\n6. 标题中禁止出现任何标点符号，包括逗号、句号、感叹号、问号、冒号、破折号等。"
        "\n7. 只返回 JSON，不要 Markdown，不要解释。"
        "\n\n文案内容："
        f"\n{script[:600]}"
        "\n\n返回格式："
        '\n{"line1": "第一行标题", "line2": "第二行标题"}'
    )

    payload = {
        "model": bigmodel_model(),
        "messages": [
            {"role": "system", "content": "你只输出可解析 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.8,
        "top_p": 0.9,
        "max_tokens": 200,
        "stream": False,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{bigmodel_endpoint().rstrip('/')}/chat/completions",
            data=data,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        result = json.loads(extract_json(str(content)))
        line1 = str(result.get("line1", "")).strip()
        line2 = str(result.get("line2", "")).strip()
        # Strip any punctuation that the model might still produce
        _punct_pat = re.compile(r"[，。！？、；：“”‘’《》【】（）—…\-.!?,;:'\"()\[\]{}<>]")
        line1 = _punct_pat.sub("", line1).strip()
        line2 = _punct_pat.sub("", line2).strip()
        line1 = line1[:9]
        line2 = line2[:9]
        return {"line1": line1, "line2": line2, "full_title": f"{line1} {line2}"}
    except Exception as exc:
        return {"line1": "", "line2": "", "full_title": "", "error": str(exc)[:200]}


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
            if item not in tags["scene"] and item not in tags["era"]
        ][:2]
        required_scene = tags["scene"][:2]
        visual_need = "、".join(required_object + required_scene) or "待AI生成画面描述"
        shots.append({
            "shot_index": idx,
            "voice_text": text,
            "duration_sec": duration,
            "start_time": round(cursor, 2),
            "end_time": round(cursor + duration, 2),
            "visual_need": visual_need,
            "person_gender": "unknown",
            "person_names": [],
            "person_description": "",
            "required_object": required_object,
            "required_scene": required_scene,
            "object_tags": required_object,
            "scene_tags": required_scene,
            "keywords": [],
            "search_keywords": tags["keywords"][:4],
            "selected_asset_id": None,
            "asset_source": None,
            "match_score": 0,
            "status": "no_match",
        })
        cursor += duration

    # Use GLM to generate more accurate visual descriptions and search keywords
    visuals = ai_generate_shot_visuals(shots, script)
    for shot in shots:
        visual = visuals.get(str(shot["shot_index"]))
        if visual:
            if visual.get("visual_need"):
                shot["visual_need"] = visual["visual_need"]
            if visual.get("person_gender"):
                shot["person_gender"] = visual["person_gender"]
            if visual.get("person_names"):
                shot["person_names"] = visual["person_names"]
            if visual.get("person_description"):
                shot["person_description"] = visual["person_description"]
            if visual.get("search_keywords"):
                shot["search_keywords"] = visual["search_keywords"]
            if visual.get("object_tags"):
                shot["object_tags"] = visual["object_tags"]
                shot["required_object"] = visual["object_tags"]
            if visual.get("scene_tags"):
                shot["scene_tags"] = visual["scene_tags"]
                shot["required_scene"] = visual["scene_tags"]
            if visual.get("keywords"):
                shot["keywords"] = visual["keywords"]

    return shots
