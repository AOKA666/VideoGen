from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


ABSTRACT_WORDS = ["伟大", "震撼", "感人", "精神", "贡献", "意义", "重要", "传奇", "辉煌"]
MIN_KEYWORDS_PER_SHOT = 3
MAX_KEYWORDS_PER_SHOT = 3
MAX_KEYWORD_CHARS = 10
KNOWN_PEOPLE = [
    "钱学森", "邓稼先", "于敏", "黄旭华", "郭永怀", "袁隆平", "王淦昌", "屠呦呦",
    "孙家栋", "程开甲", "钱三强", "钱伟长", "竺可桢", "华罗庚", "李四光", "林俊德",
    "焦裕禄", "雷锋", "张富清", "黄大年", "南仁东",
]
GENERIC_VISUAL_KEYWORDS = ["历史档案", "黑白旧照", "纪实照片", "老照片", "档案照片"]
KEYWORD_FILLER_WORDS = [
    "画面", "展现", "表现", "体现", "突出", "呈现", "当前镜头", "特写画面", "历史纪实",
    "的", "与", "和", "及",
]
VISUAL_TERMS = [
    "老照片", "历史照片", "旧照", "档案", "黑白照片", "实验室", "科研人员", "会议",
    "办公室", "手稿", "文件", "火箭", "导弹", "核试验", "基地", "戈壁", "中国科学院",
    "留学生", "博士毕业", "颁奖", "火车站", "回国", "宿舍", "课堂", "工厂",
    "绝密档案", "机密文件", "两弹元勋", "两弹一星", "勋章", "血迹", "嘴角", "手帕",
    "白衬衫", "鲜花", "花束", "纪念碑", "日本国旗", "国旗", "轮船", "港口",
    "登船", "撕国旗", "黑白照", "资料照",
]


def bigmodel_endpoint() -> str:
    return os.getenv("BIGMODEL_ENDPOINT", "https://open.bigmodel.cn/api/paas/v4")


def bigmodel_model() -> str:
    return os.getenv("BIGMODEL_MODEL", "glm-5.1")


def _extract_json_object(text: str) -> str:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("AI response does not contain JSON")
    return match.group(0)


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,，、\n]+", value) if item.strip()]
    return []


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys([item for item in items if item]))


def _clean_text(value: str, *, max_chars: int) -> str:
    cleaned = re.sub(r"[，。！？、；：,.!?;:\n\r]+", " ", str(value))
    for word in ABSTRACT_WORDS:
        cleaned = cleaned.replace(word, "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return " ".join(cleaned.split()[:4])[:max_chars]


def _clean_keyword(keyword: str) -> str:
    cleaned = _clean_text(keyword, max_chars=40)
    for word in KEYWORD_FILLER_WORDS:
        cleaned = cleaned.replace(word, "")
    cleaned = cleaned.replace("历史照片", "旧照").replace("黑白照片", "黑白照")
    cleaned = cleaned.replace("老照片", "旧照").replace("真实影像", "影像")
    cleaned = re.sub(r"\s+", "", cleaned).strip()
    return cleaned[:MAX_KEYWORD_CHARS]


def _clean_visual_intent(visual_intent: str) -> str:
    return _clean_text(visual_intent, max_chars=32)


def _keyword_variants(person: str, term: str) -> list[str]:
    term = _clean_keyword(term)
    if not term:
        return []
    variants = [term, f"{term}旧照"]
    if person:
        variants = [f"{person}{term}", f"{person}旧照", *variants]
    if term.endswith(("档案", "文件", "手稿", "勋章", "鲜花", "轮船", "火箭", "导弹")):
        variants.append(f"{term}特写")
    return variants


def _intent_terms(visual_intent: str) -> list[str]:
    text = str(visual_intent or "")
    for word in [*ABSTRACT_WORDS, *KEYWORD_FILLER_WORDS, "历史纪实画面"]:
        text = text.replace(word, " ")
    return re.findall(r"[\u4e00-\u9fff]{2,10}", text)


def _finalize_keywords(candidates: list[str]) -> list[str]:
    keywords = [_clean_keyword(item) for item in candidates]
    keywords = [item for item in _unique(keywords) if item and item not in ABSTRACT_WORDS]
    for fallback in GENERIC_VISUAL_KEYWORDS:
        if len(keywords) >= MIN_KEYWORDS_PER_SHOT:
            break
        cleaned = _clean_keyword(fallback)
        if cleaned not in keywords:
            keywords.append(cleaned)
    return keywords[:MAX_KEYWORDS_PER_SHOT]


def find_people(text: str) -> list[str]:
    return [name for name in KNOWN_PEOPLE if name in text]


def extract_visual_terms(text: str) -> list[str]:
    explicit_terms = sorted([term for term in VISUAL_TERMS if term in text], key=lambda term: -len(term))
    derived_terms: list[str] = []
    if "撕" in text and "国旗" in text:
        derived_terms.append("撕国旗")
    if "血" in text and "嘴角" in text:
        derived_terms.append("嘴角血迹")
    if "踏上" in text and "轮船" in text:
        derived_terms.append("登船")
    regex_terms = re.findall(r"\d{4}年|\d{2}岁|上世纪\d{2}年代|20世纪\d{2}年代|19\d{2}年代", text)
    regex_terms.extend(re.findall(r"[\u4e00-\u9fff]{2,8}(?:实验室|办公室|宿舍|基地|火车站|会议|照片|档案|手稿|文件|导弹|火箭|工厂|课堂)", text))
    if "博士" in text:
        derived_terms.append("博士毕业")
    return _unique(explicit_terms + derived_terms + regex_terms)


def intent_based_keywords(people: list[str], visual_terms: list[str], visual_intent: str) -> list[str]:
    person = people[0] if people else ""
    concrete = [term for term in _unique(visual_terms) if term not in {"老照片", "历史照片", "旧照", "黑白照片"}]
    concrete = sorted(concrete, key=lambda term: bool(re.fullmatch(r"\d{2}岁|\d{4}年", term)))
    keywords: list[str] = []
    for term in concrete[:3]:
        keywords.extend(_keyword_variants(person, term))
    if person:
        keywords.extend([f"{person}旧照", f"{person}资料照"])
    keywords.extend(_intent_terms(visual_intent)[:3])
    keywords.extend(GENERIC_VISUAL_KEYWORDS)
    return _finalize_keywords(keywords)


def sanitize_intent(result: dict, shot_text: str) -> dict:
    people = _as_list(result.get("people")) or find_people(shot_text)
    visual_intent = str(result.get("visual_intent") or "").strip()
    visual_terms = extract_visual_terms(f"{shot_text} {visual_intent}")
    if not visual_intent:
        visual_intent = " ".join([*(people[:1]), *visual_terms[:3], "历史纪实画面"]).strip() or "历史纪实画面"

    ai_keywords = _as_list(result.get("search_keywords"))
    keywords = _finalize_keywords(ai_keywords + intent_based_keywords(people[:1], visual_terms, visual_intent))
    return {
        "visual_intent": _clean_visual_intent(visual_intent) or "历史纪实画面",
        "search_keywords": keywords,
        "people": people[:3],
        "provider": result.get("provider") or "ai",
        "error": "",
    }


def fallback_search_intent(shot_text: str, _full_text: str = "") -> dict:
    people = find_people(shot_text)
    visual_terms = extract_visual_terms(shot_text)
    visual_intent = " ".join([*(people[:1]), *visual_terms[:3], "历史纪实画面"]).strip() or "历史纪实画面"
    return {
        "visual_intent": _clean_visual_intent(visual_intent),
        "search_keywords": intent_based_keywords(people[:1], visual_terms, visual_intent),
        "people": people[:3],
        "provider": "local_fallback",
        "error": "",
    }


def ai_search_intent(shot_text: str, full_text: str) -> dict:
    api_key = os.getenv("BIGMODEL_API_KEY", "").strip()
    if not api_key:
        return fallback_search_intent(shot_text, full_text)

    prompt = f"""
你是短视频分镜素材搜索助手。只分析当前镜头。

要求：
1. 只输出严格 JSON，不要 Markdown。
2. visual_intent 是当前镜头的画面意图。
3. search_keywords 输出 3-5 个简短中文关键词，每个不超过 10 个汉字。
4. 关键词必须具体、可搜索、偏视觉化，适合百度图片。
5. 关键词参考“精准人物/事件、替代场景、氛围细节”的组合，例如：邓稼先旧照、嘴角血迹特写、科学家病重旧照、手帕血迹、档案照片。
6. 禁止把“伟大、震撼、感人、精神、贡献、意义”等抽象词作为关键词。
7. 当前镜头有人物名时，1-2 个关键词可以包含人物名；不要让所有关键词都变成人物通用照。

全文仅供理解背景，不要强行从全文补人物：
{full_text}

当前镜头：
{shot_text}

返回格式：
{{
  "visual_intent": "画面意图",
  "people": ["当前镜头出现的人物名"],
  "search_keywords": ["短关键词1", "短关键词2", "短关键词3", "短关键词4", "短关键词5"]
}}
""".strip()
    payload = {
        "model": bigmodel_model(),
        "messages": [
            {"role": "system", "content": "你只输出可解析 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.25,
        "top_p": 0.7,
        "max_tokens": 1000,
        "stream": False,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{bigmodel_endpoint().rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        result = json.loads(_extract_json_object(str(content)))
        result["provider"] = bigmodel_model()
        return sanitize_intent(result, shot_text)
    except (urllib.error.URLError, KeyError, ValueError, json.JSONDecodeError) as exc:
        fallback = fallback_search_intent(shot_text, full_text)
        fallback["error"] = str(exc)[:300]
        return fallback


def ai_search_intents(shots: list[dict], full_text: str) -> dict[str, dict]:
    api_key = os.getenv("BIGMODEL_API_KEY", "").strip()
    if not api_key:
        return {
            str(shot.get("id") or shot.get("shot_index")): fallback_search_intent(str(shot.get("voice_text") or ""), full_text)
            for shot in shots
        }

    shot_items = [
        {
            "id": str(shot.get("id") or shot.get("shot_index")),
            "shot_index": shot.get("shot_index"),
            "voice_text": str(shot.get("voice_text") or ""),
        }
        for shot in shots
    ]
    prompt = f"""
你是短视频分镜素材搜索助手。请一次性分析所有分镜，但每个分镜只依据自己的 voice_text 生成画面意图和搜索关键词。

要求：
1. 只输出严格 JSON，不要 Markdown。
2. 每个分镜都必须返回 visual_intent、people、search_keywords。
3. search_keywords 输出 3-5 个简短中文关键词，每个不超过 10 个汉字。
4. 关键词必须具体、可搜索、偏视觉化，适合百度图片。
5. 关键词参考“精准人物/事件、替代场景、氛围细节”的组合，例如：邓稼先旧照、嘴角血迹特写、科学家病重旧照、手帕血迹、档案照片。
6. 禁止把“伟大、震撼、感人、精神、贡献、意义”等抽象词作为关键词。
7. 当前分镜有人物名时，1-2 个关键词可以包含人物名；不要让所有关键词都变成人物通用照。
8. 全文仅供理解背景，不要强行从全文给每个分镜补人物。

全文背景：
{full_text}

分镜列表：
{json.dumps(shot_items, ensure_ascii=False)}

返回格式：
{{
  "shots": [
    {{
      "id": "分镜 id",
      "visual_intent": "画面意图",
      "people": ["当前分镜出现的人物名"],
      "search_keywords": ["短关键词1", "短关键词2", "短关键词3", "短关键词4", "短关键词5"]
    }}
  ]
}}
""".strip()
    payload = {
        "model": bigmodel_model(),
        "messages": [
            {"role": "system", "content": "你只输出可解析 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.25,
        "top_p": 0.7,
        "max_tokens": max(1200, min(8000, len(shots) * 350)),
        "stream": False,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{bigmodel_endpoint().rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        result = json.loads(_extract_json_object(str(content)))
        raw_items = result.get("shots") if isinstance(result, dict) else []
        intents: dict[str, dict] = {}
        shot_text_by_id = {str(item["id"]): item["voice_text"] for item in shot_items}
        for item in raw_items or []:
            if not isinstance(item, dict):
                continue
            shot_id = str(item.get("id") or "")
            if shot_id not in shot_text_by_id:
                continue
            item["provider"] = bigmodel_model()
            intents[shot_id] = sanitize_intent(item, shot_text_by_id[shot_id])
        for shot in shot_items:
            if shot["id"] not in intents:
                fallback = fallback_search_intent(shot["voice_text"], full_text)
                fallback["error"] = "batch AI response missing this shot"
                intents[shot["id"]] = fallback
        return intents
    except (urllib.error.URLError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {
            shot["id"]: {
                **fallback_search_intent(shot["voice_text"], full_text),
                "error": str(exc)[:300],
            }
            for shot in shot_items
        }


def apply_intent_to_shot(shot: dict, intent: dict) -> dict:
    shot["visual_intent"] = intent["visual_intent"]
    shot["visual_need"] = intent["visual_intent"]
    shot["search_keywords"] = intent["search_keywords"]
    shot["search_intent_provider"] = intent.get("provider", "")
    shot["search_intent_error"] = intent.get("error", "")
    shot["required_object"] = intent.get("people", [])
    shot["required_scene"] = extract_visual_terms(str(shot.get("voice_text") or ""))[:3] or ["历史照片"]
    return shot
