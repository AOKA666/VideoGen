from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


MIN_KEYWORD_CHARS = 2
MAX_KEYWORD_CHARS = 12
FORBIDDEN_KEYWORDS = {
    "默认",
    "空镜头",
    "模糊背景",
    "产品",
    "介绍",
    "视觉",
    "场景",
    "描述",
    "背景",
    "素材",
    "图片",
    "画面",
    "现场",
    "安全感",
    "威严",
    "强硬回击",
}
FRAGMENT_PREFIXES = ("时", "或", "的", "方", "其", "这", "那")
SENTENCE_MARKERS = ("为了", "体现", "展现", "展示", "表现", "强调", "画面")


class SearchIntentBatchError(RuntimeError):
    pass


def bigmodel_endpoint() -> str:
    return os.getenv("BIGMODEL_ENDPOINT", "https://open.bigmodel.cn/api/paas/v4")


def bigmodel_model() -> str:
    return os.getenv("BIGMODEL_MODEL", "glm-5.1")


def _extract_json_object(text: str) -> str:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("AI response does not contain JSON")
    return match.group(0)


def _raw_core_keyword(result: dict) -> str:
    value = result.get("core_keyword")
    if value is None:
        legacy = result.get("search_keywords")
        if isinstance(legacy, list) and legacy:
            value = legacy[0]
        elif isinstance(legacy, str):
            value = legacy
    return str(value or "").strip()


def normalize_core_keyword(value: str) -> str:
    keyword = re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()
    keyword = keyword.strip(" \"'“”‘’[]【】")
    keyword = re.sub(r"\s+", "", keyword)
    return keyword


def validate_core_keyword(value: str) -> str:
    keyword = normalize_core_keyword(value)
    if not (MIN_KEYWORD_CHARS <= len(keyword) <= MAX_KEYWORD_CHARS):
        raise ValueError(f"核心关键词长度必须为 {MIN_KEYWORD_CHARS}-{MAX_KEYWORD_CHARS} 个字符")
    if re.search(r"[，。！？、；：,.!?;:]", keyword):
        raise ValueError("核心关键词不能包含句子标点")
    if keyword in FORBIDDEN_KEYWORDS:
        raise ValueError("核心关键词过于抽象或缺少主体")
    if keyword.startswith(FRAGMENT_PREFIXES):
        raise ValueError("核心关键词疑似句子残片")
    if any(marker in keyword for marker in SENTENCE_MARKERS):
        raise ValueError("核心关键词不能是镜头描述或搜索意图")
    if re.fullmatch(r"\d+", keyword):
        raise ValueError("核心关键词不能只有编号")
    if re.search(r"\d{3,}$", keyword):
        raise ValueError("编号后必须包含人物或事件主体")
    return keyword


def sanitize_intent(result: dict, _shot_text: str = "") -> dict:
    keyword = validate_core_keyword(_raw_core_keyword(result))
    return {
        "core_keyword": keyword,
        "search_keywords": [keyword],
        "provider": result.get("provider") or "ai",
        "error": "",
    }


def _request_glm(payload: dict, timeout: int) -> dict:
    api_key = os.getenv("BIGMODEL_API_KEY", "").strip()
    if not api_key:
        raise SearchIntentBatchError("BIGMODEL_API_KEY 未配置，无法使用 GLM 生成核心关键词")
    req = urllib.request.Request(
        f"{bigmodel_endpoint().rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return json.loads(_extract_json_object(str(content)))


def ai_search_intent(shot_text: str, full_text: str = "") -> dict:
    prompt = f"""
你是短视频分镜图片搜索关键词专家。
请直接根据镜头旁白和镜头描述，选择一个最适合中文图片搜索的核心关键词。

规则：
1. 只输出严格 JSON，不要 Markdown。
2. 只返回 core_keyword，不要输出搜索意图、画面意图、人物列表或英文关键词。
3. core_keyword 必须是完整、自然、可以直接复制到图片搜索框里的词组，长度 2-12 个字符。
4. 优先选择明确人物、历史事件、地点、载具或动作画面，例如“中美阿拉斯加会谈”“王伟81192事件”“撤侨专机”“导弹命中爆炸”。
5. 禁止从镜头描述中机械截取连续几个字，禁止输出“方强硬回击”“时侨民登”这类残片。
6. 禁止输出只有编号的关键词，编号必须和人物或事件主体组合。
7. 禁止输出“安全感、威严、强硬回击、现场、画面”等抽象词。
8. 不要为了缩短而删除人物或事件主体。

全文背景仅用于消除歧义：
{full_text}

镜头旁白及描述：
{shot_text}

返回格式：
{{"core_keyword": "一个完整核心关键词"}}
""".strip()
    payload = {
        "model": bigmodel_model(),
        "messages": [
            {"role": "system", "content": "你只输出可解析 JSON，并直接选择图片搜索核心关键词。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.15,
        "top_p": 0.6,
        "max_tokens": 300,
        "stream": False,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    try:
        result = _request_glm(payload, timeout=60)
        result["provider"] = bigmodel_model()
        return sanitize_intent(result, shot_text)
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise SearchIntentBatchError(f"GLM 核心关键词生成失败：{str(exc)[:300]}") from exc


def ai_search_intents(shots: list[dict], full_text: str, *, timeout: int = 90, retries: int = 1) -> dict[str, dict]:
    shot_items = [
        {
            "id": str(shot.get("id") or shot.get("shot_index")),
            "shot_index": shot.get("shot_index"),
            "voice_text": str(shot.get("voice_text") or ""),
            "shot_description": str(shot.get("visual_need") or ""),
        }
        for shot in shots
    ]
    prompt = f"""
你是短视频分镜图片搜索关键词专家。
请直接根据每个分镜的 voice_text 和 shot_description，分别选择一个最适合中文图片搜索的核心关键词。

规则：
1. 只输出严格 JSON，不要 Markdown。
2. 每个分镜只返回 id 和 core_keyword，不要输出搜索意图、画面意图、人物列表或英文关键词。
3. core_keyword 必须是完整、自然、可以直接复制到图片搜索框里的词组，长度 2-12 个字符。
4. 优先选择明确人物、历史事件、地点、载具或动作画面，例如：
   - “你们没有资格从实力地位出发同中国谈话” -> “中美阿拉斯加会谈”
   - “王伟烈士为守海疆壮烈牺牲” -> “王伟81192事件”
   - “接同胞回国的专机” -> “撤侨专机”
5. 禁止从 shot_description 中机械截取连续几个字，禁止输出“方强硬回击”“王伟81192”“时侨民登”这类残片。
6. 编号必须和人物或事件主体组合；态度和情绪必须还原为对应的具体事件。
7. 禁止输出“安全感、威严、强硬回击、现场、画面”等抽象词。
8. 不要为了缩短而删除人物或事件主体。
9. 全文背景仅用于消除人物、事件和代词歧义。

全文背景：
{full_text}

分镜列表：
{json.dumps(shot_items, ensure_ascii=False)}

返回格式：
{{
  "shots": [
    {{"id": "分镜 id", "core_keyword": "一个完整核心关键词"}}
  ]
}}
""".strip()
    payload = {
        "model": bigmodel_model(),
        "messages": [
            {"role": "system", "content": "你只输出可解析 JSON，并直接选择每个分镜的图片搜索核心关键词。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.15,
        "top_p": 0.6,
        "max_tokens": max(800, min(4000, len(shots) * 120)),
        "stream": False,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }

    last_error: Exception | None = None
    for _attempt in range(max(1, retries + 1)):
        try:
            result = _request_glm(payload, timeout=timeout)
            raw_items = result.get("shots") if isinstance(result, dict) else []
            intents: dict[str, dict] = {}
            valid_ids = {str(item["id"]) for item in shot_items}
            for item in raw_items or []:
                if not isinstance(item, dict):
                    continue
                shot_id = str(item.get("id") or "")
                if shot_id not in valid_ids:
                    continue
                item["provider"] = bigmodel_model()
                intents[shot_id] = sanitize_intent(item)
            missing_ids = [shot["id"] for shot in shot_items if shot["id"] not in intents]
            if missing_ids:
                raise SearchIntentBatchError(f"GLM 返回缺少 {len(missing_ids)} 个分镜核心关键词")
            return intents
        except SearchIntentBatchError as exc:
            last_error = exc
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
    raise SearchIntentBatchError(f"GLM 核心关键词生成失败：{str(last_error)[:300]}")


def apply_intent_to_shot(shot: dict, intent: dict) -> dict:
    keyword = validate_core_keyword(intent.get("core_keyword") or (intent.get("search_keywords") or [""])[0])
    shot["search_keywords"] = [keyword]
    shot.pop("archive_keywords", None)
    shot.pop("visual_intent", None)
    shot["search_intent_provider"] = intent.get("provider", "")
    shot["search_intent_error"] = intent.get("error", "")
    return shot
