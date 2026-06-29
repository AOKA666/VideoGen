from __future__ import annotations

import json
import os
import random
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
MIN_REWRITE_DIFFERENCE = 40
MAX_REWRITE_ATTEMPTS = 3
MAX_AUTO_TITLE_LENGTH = 9
RANDOM = random.SystemRandom()
GUOZHIJILIANG_STORY_SEEDS = [
    ("钱学森", "美国海关扣下他的行李，硬说里面藏着国家机密"),
    ("邓稼先", "在戈壁核试验场，他明知有危险仍走向爆心查找碎片"),
    ("黄旭华", "父亲去世不能回家奔丧，母亲多年不知道他去了哪里"),
    ("郭永怀", "飞机失事前，他和警卫员用身体护住装有绝密资料的公文包"),
    ("林俊德", "生命最后一天，他穿着病号服坐到电脑前整理资料"),
    ("王淦昌", "他放下自己的名字，化名王京在西北隐身多年"),
    ("于敏", "他从零开始转向氢弹理论研究，连家人都不知道他在做什么"),
    ("袁隆平", "他蹲在稻田里寻找那株改变无数人饭碗的天然雄性不育株"),
    ("孙家栋", "卫星发射前，他在控制大厅盯着屏幕等待最后的信号"),
    ("屠呦呦", "她翻遍古籍后，把青蒿提取实验一次次推倒重来"),
    ("王承书", "她主动要求抹掉自己的名字，隐姓埋名参与国家工程"),
    ("彭士禄", "核潜艇研制最难的时候，他带着队伍在简陋条件下啃硬骨头"),
]


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
    return first[:MAX_AUTO_TITLE_LENGTH].strip("，。！？ ") or "未命名项目"


def extract_opening_hook(raw_script: str) -> str:
    sentences = split_sentences(raw_script)
    for line in raw_script.splitlines():
        cleaned = line.strip()
        if cleaned and len(cleaned) <= 90:
            return cleaned
        if cleaned and sentences:
            return sentences[0].strip()
    return sentences[0].strip() if sentences else ""


def is_strong_opening_hook(text: str) -> bool:
    cleaned = re.sub(r"\s+", "", text or "")
    if not 8 <= len(cleaned) <= 70:
        return False
    weak_starts = (
        "今天", "大家好", "你知道吗", "提起", "说起", "在中国", "在我国",
        "他出生", "她出生", "他是", "她是", "这是一位", "有这样一位",
    )
    if cleaned.startswith(weak_starts):
        return False
    hook_keywords = (
        "但是", "却", "竟", "只因", "没想到", "直到", "最后", "临终", "牺牲",
        "失踪", "消失", "抹掉", "隐姓埋名", "不能回家", "生死", "绝密", "封锁",
        "扣下", "拒绝", "放弃", "没人知道", "再也没有", "为什么", "凭什么", "谁能想到",
    )
    return any(keyword in cleaned for keyword in hook_keywords) or bool(re.search(r"[？！?!]", cleaned))


def build_fallback_hook(raw_script: str, title: str) -> str:
    original_hook = extract_opening_hook(raw_script)
    if is_strong_opening_hook(original_hook):
        return original_hook
    short_title = title[:18].strip("，。！？、：； ")
    if short_title:
        return f"很多人记住了{short_title}，却不知道这个名字背后藏着多重的代价。"
    return "很多人只看见了结果，却不知道背后那一次几乎没人能承受的选择。"


def ensure_original_opening(raw_script: str, rewritten_script: str) -> str:
    raw_hook = extract_opening_hook(raw_script)
    if not raw_hook:
        return rewritten_script.strip()

    rewritten = rewritten_script.strip()
    if rewritten.startswith(raw_hook):
        return rewritten

    lines = [line.strip() for line in rewritten.splitlines() if line.strip()]
    if lines and is_similar_text(lines[0], raw_hook):
        body_lines = lines[1:]
    else:
        body_lines = lines
    body = "\n".join(body_lines).strip()
    return f"{raw_hook}\n{body}" if body else raw_hook


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
    hook = extract_opening_hook(raw_script) or build_fallback_hook(raw_script, title)
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


def ensure_min_rewrite_difference(result: dict) -> dict:
    comparison = result.get("rewrite_comparison") or {}
    difference = comparison.get("overall_difference", 0)
    if difference < MIN_REWRITE_DIFFERENCE:
        result["rewrite_error"] = (
            f"rewrite difference {difference}% below {MIN_REWRITE_DIFFERENCE}%"
        )
        raise RewriteQualityError(result)
    return result


def normalize_rewrite_result(result: dict, raw_script: str, style: str) -> dict:
    title = str(result.get("title") or infer_title(raw_script)).strip()
    hook = extract_opening_hook(raw_script) or str(result.get("hook") or build_fallback_hook(raw_script, title)).strip()
    rewritten_script = str(result.get("rewritten_script") or "").strip()
    if not rewritten_script:
        rewritten_script = fallback_rewrite_script(raw_script, style)["rewritten_script"]
    rewritten_script = clean_rewritten_script(raw_script, rewritten_script)
    rewritten_script = ensure_original_opening(raw_script, rewritten_script)
    comparison = compare_scripts(raw_script, rewritten_script)
    return {
        "title": title[:MAX_AUTO_TITLE_LENGTH] or infer_title(raw_script),
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
    prompt = f"""
你是一名视频号爆款短视频文案改写专家，擅长改写卖书类、历史人物类、大国情绪类、爱国教育类短视频口播文案。

我要你改写下面这篇文案，目标是在视频号发布，用于提高播放量、完播率和带书转化。

【最重要要求】
原文前三秒文案必须一字不改保留。
也就是说，原文开头最前面的 1～3 句话，如果已经承担前三秒钩子作用，必须完整保留，不允许改字、不允许换词、不允许调整顺序、不允许删减。
你只能从前三秒之后开始优化。
如果你判断原文前三秒不够好，也不能擅自改动，只能在正文后额外给出“【前三秒优化建议】”，但正文里必须保留原前三秒不变。
必须原样保留的前三秒开头是：{opening_hook}

【改写目标】
保留原文的短视频味道，不要改成书面文章。
改写后的文案要像一个懂视频号的人在口播，而不是像公众号社论、新闻评论、AI润色稿、学生作文。
整体风格要：口语化、有网感、有情绪、有画面、有节奏、有冲突。
不要追求文采高级，要追求用户愿意听下去、愿意点赞、愿意评论、愿意转发。

【必须保留的东西】
1. 保留原文前三秒钩子不变。
2. 保留原文的核心观点。
3. 保留原文的情绪曲线。
4. 保留原文中有流量感的短句、狠话、网感表达。
5. 保留原文中已经很顺口的金句，不要为了改写而强行换词。
6. 保留原文中能形成画面的具体细节。
7. 保留原文的带书逻辑，如果原文提到了《国之脊梁》，要保留并自然优化。

【禁止事项】
不要做简单同义词替换。
不要把口语改成书面语。
不要把“咱妈、塞铁、刷666、小鱼小虾、你可以试试、护筷子”这类短视频表达全部洗掉。
不要使用过多书面词，例如：悍然、方知、伟岸、至此、乃、赴汤蹈火、径直、再至、苍生、星光、抉择、壮烈史诗、强国气场、脱胎换骨、恩重如山。
不要频繁使用空泛大词，例如：伟大、震撼、辉煌、底蕴、史诗、精神源泉、民族脊梁、大国情怀。这些词可以少量使用，但不能堆。
不要把文案改成端着的播音腔。
不要一上来介绍背景，不要平铺直叙。
不要削弱原文的爽感、反差感和情绪冲击。

【短视频改写原则】
一、句子要短。适合真人口播。能用短句就不要用长句。能用人话就不要用书面话。
二、表达要狠。该硬的地方要硬。比如：“你可以试试敢不敢将它击落。”这种句子不要改成：“那便试试看是否敢于动用武力击落。”
三、要有画面。多保留或强化具体画面：飞机起飞、国旗铺满街道、地图包围、旧照片、病房电脑、公文包、胶鞋、行李箱、实验室灯光、戈壁风沙。少写抽象评价。
四、要有情绪递进。文案结构尽量按照：前三秒钩子不变 → 具体事件 → 背景解释 → 历史伤痛/现实困境 → 今日反转 → 情绪爆发 → 英雄群像/人物承接 → 自然带书 → 家长转化。
五、带书要自然。如果文案是为了卖《国之脊梁》，不要硬广，不要写“赶紧点击小黄车购买”。可以写：“翻开《国之脊梁》才知道，今天的底气不是凭空来的。”“如果家里有孩子，真希望他们认识这些真正值得追的星。”“他们不是热搜里的明星，却是孩子最该知道的人。”

【分段要求】
请按短视频分镜逻辑分段。
一个镜头一段。
同一个镜头内部不要换行。
每段必须能对应一个完整画面，方便后续 AI 配图、素材搜索和剪映剪辑。
不要出现只有几个字的空段。
每段建议 30～80 字左右。
换段标准是：时间变化、地点变化、人物动作变化、画面主体变化、情绪节点变化。
不要按朗读断句分段，而要按画面分段。

【改写尺度】
不是洗稿式同义替换，而是保留爆点后重新优化节奏。
可以删掉重复啰嗦的句子。
可以强化画面感和冲突感。
可以调整后半部分结构。
可以让带书更自然。
但不能改动前三秒原文。

【长度和质量约束】
原文去除空白后的长度约 {raw_len} 个中文字符。
rewritten_script 去除空白后的长度必须控制在 {min_len} 到 {max_len} 个中文字符之间。
不要压缩成摘要、提纲或短版解说，也不要省略原文中的重要事实。
事实边界：不虚构；不添加没有依据的具体时间、地点、人物关系；人物、年代、事件、因果关系必须保留。
除必须原样保留的前三秒开头、专有名词、年份、固定称谓、顺口金句、流量感短句之外，整体内容必须重新组织。
系统会用字符相似度和语义相似度自动对比，最终总体差异度必须达到 {MIN_REWRITE_DIFFERENCE}% 以上。

【本次生成信息】
文案风格：{style}。
这是第 {attempt} 次生成。{retry_instruction}

【输出要求】
只返回可解析 JSON，字段必须包含 title, hook, rewritten_script, script_style。
rewritten_script 字段里只能放改写后的完整文案正文，禁止包含原文、原始文案、对照稿、解释、标题标签或“二创口播稿：”这类前缀。
不要先输出一遍原文再输出二创稿，也不要把原文和二创内容混在一起。

<raw_script>{raw_script}</raw_script>
"""
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
        return ensure_min_rewrite_difference(fallback)
    try:
        return rewrite_script_with_glm(raw_script, style, api_key)
    except RewriteQualityError:
        raise
    except Exception as exc:
        fallback["rewrite_error"] = str(exc)[:300]
        return ensure_min_rewrite_difference(fallback)


def choose_guozhijiliang_seed(person_name: str = "", event_angle: str = "") -> tuple[str, str]:
    person = person_name.strip()
    angle = event_angle.strip()
    if person and angle:
        return person, angle
    if not person:
        person, default_angle = RANDOM.choice(GUOZHIJILIANG_STORY_SEEDS)
        return person, angle or default_angle
    default_angle = next(
        (seed_angle for seed_person, seed_angle in GUOZHIJILIANG_STORY_SEEDS if seed_person == person),
        "从这个人物真实经历中选择一个最适合短视频叙事的核心事件",
    )
    return person, angle or default_angle


def build_guozhijiliang_script_prompt(person_name: str = "", event_angle: str = "") -> str:
    person_line, event_line = choose_guozhijiliang_seed(person_name, event_angle)
    return f"""你是一名擅长视频号卖书短视频的文案策划，尤其擅长写《国之脊梁》风格的人物故事文案。

我要你围绕《国之脊梁》相关院士写一篇短视频文案，目标是在视频号发布，用来带《国之脊梁》这类人物传记/爱国教育类图书。

人物名称：{person_line}
核心事件或角度：{event_line}
目标书籍：《国之脊梁》
文案长度：适合视频号 4 到 5 分钟（1000字左右）。

核心要求：前三秒暴击、故事化、少大道理、按镜头分段、自然带书。

整体风格：
不要写成人物百科，不要平铺直叙介绍生平，不要从“某某出生于某年”开始。写成一个有画面感、有冲突、有悬念、有细节的人物故事。风格接近视频号爆款卖书文案，不是官方传记，不是新闻通稿，也不是空喊口号。核心感觉是：感动中国式叙事 + 短视频强钩子 + 家长愿意买给孩子看的价值观。

前三秒开头：
开头必须直接抓人，用强反差、强悬念、强画面，不能平铺直叙。优先使用结果反差、生死瞬间、身份反差、亲情冲突、被抹掉/消失悬念。开头要先给冲突，不要先讲背景。
第一段就是前三秒，必须像短视频开场一样把观众拽住：先写“最不正常的一幕”，再解释人物是谁。禁止用“今天我们讲”“提起某某”“他出生于”“他是我国著名”“有这样一位科学家”这类百科式开头。
第一句话控制在 12 到 32 个汉字，必须包含一个具体冲突、反差或悬念；不要只写“他很伟大”“震惊世界”“感动无数人”这种空话。
可学习这些开头逻辑但不要照搬：临终前他没有躺下，而是坐回电脑前；父亲去世那天，他连名字都不能告诉家里；飞机坠落前，他最后护住的不是自己；她主动要求，把自己的名字从工程里抹掉。

故事结构：
1. 暴击开头：先抛出最有冲突的场景或结果。
2. 留下悬念：让观众想知道“为什么会这样”。
3. 揭示人物：自然引出人物名字，不要像百科一样硬介绍。
4. 进入具体事件：只围绕一个核心事件展开，不要把人物一生全部塞进去。
5. 加入细节：必须有具体动作、物品、场景，例如病号服、旧胶鞋、抽屉、笔记、手稿、公文包、实验室的灯、戈壁风沙、病床旁的电脑。
6. 写出牺牲：不要直接说“他很伟大”，而是通过他放弃了什么、承受了什么来体现。
7. 情绪收束：用一句人物原话、一个动作、一个画面或一个结果完成情绪爆发。
8. 自然带书：结尾再自然提到《国之脊梁》，不要硬广，不要喊“赶紧购买”。

内容要求：
不要写大而空的句子，比如“他为国家做出了巨大贡献”“他是中华民族的脊梁”“他用一生诠释了伟大”“我们要永远铭记英雄”。这些意思可以通过故事和细节让观众自己感受到。不要频繁使用“震惊世界”“美国最害怕”“比核弹还恐怖”“全球第一”“举世无双”等夸张词，除非确有必要。

镜头分段要求：
文案必须按分镜分段。一个镜头一段。同一个镜头内部不要换行。每一段都必须能对应一个完整画面，方便后续 AI 配图、素材搜索、剪映剪辑。不要出现只有几个字的段落。每段建议 30 到 80 字左右。换段标准是：时间变化、地点变化、人物动作变化、画面主体变化、情绪节点变化。不要按朗读断句分段，而要按画面分段。

故事化要求：
每篇文案必须围绕一个具体故事，不要写人物一生简介。可参考但不要照搬这些角度：钱学森聚焦美国海关扣下行李；黄旭华聚焦父亲去世不能奔丧；郭永怀聚焦飞机失事前用身体护住公文包；林俊德聚焦生命最后一天穿病号服坐到电脑前整理资料；王承书聚焦主动要求抹掉自己的名字。

结尾带书方式：
结尾必须自然带《国之脊梁》，但不要硬卖。可以类似“最近读《国之脊梁》，再次看到他的故事，心里久久不能平静。”“如果家里有孩子，真希望他们认识这样的人。”不要连续喊口号。

输出格式：
只返回 JSON，不要 Markdown，不要解释写作思路，不要列大纲，不要加小标题，不要加“镜头一、镜头二”。JSON 字段必须包含 title, person, event_angle, script。script 字段里只放按镜头分段后的正文。"""


def generate_guozhijiliang_script(person_name: str = "", event_angle: str = "") -> dict:
    api_key = os.getenv("BIGMODEL_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("BIGMODEL_API_KEY is not configured")

    selected_person, selected_angle = choose_guozhijiliang_seed(person_name, event_angle)
    prompt = build_guozhijiliang_script_prompt(selected_person, selected_angle)
    payload = {
        "model": bigmodel_model(),
        "messages": [
            {"role": "system", "content": "你只输出可解析 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.85,
        "top_p": 0.9,
        "max_tokens": 4096,
        "stream": False,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{bigmodel_endpoint().rstrip('/')}/chat/completions",
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
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
    script = clean_rewritten_script("", str(result.get("script") or result.get("rewritten_script") or "")).strip()
    if not script:
        raise RuntimeError("GLM response does not contain script")
    return {
        "title": str(result.get("title") or infer_title(script)).strip()[:MAX_AUTO_TITLE_LENGTH],
        "person": str(result.get("person") or selected_person).strip(),
        "event_angle": str(result.get("event_angle") or selected_angle).strip(),
        "script": script,
        "provider": bigmodel_model(),
    }


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


def strip_title_punctuation(text: str) -> str:
    punctuation = re.compile(r"[，。！？、；：“”‘’《》【】（）—…\-.!?,;:'\"()\[\]{}<>]")
    return re.sub(r"\s+", "", punctuation.sub("", str(text or ""))).strip()


def fallback_publish_assistant(script: str) -> dict:
    sentences = split_sentences(script)
    first = sentences[0] if sentences else script[:40]
    short_title = strip_title_punctuation(first)[:22] or "这个故事值得被更多人看见"
    description_source = " ".join(sentences[:3]) if sentences else script
    description = re.sub(r"\s+", " ", description_source).strip()
    if len(description) > 140:
        description = description[:140].rstrip("，。！？、；： ")
    return {"short_title": short_title, "description": description}


def generate_publish_assistant(script: str) -> dict:
    """Generate a platform-ready description and a punctuation-free short title."""
    api_key = os.getenv("BIGMODEL_API_KEY", "").strip()
    if not api_key:
        return fallback_publish_assistant(script)

    prompt = (
        "你是短视频发布运营助手。请根据下面的中文口播文案，生成发布用内容。"
        "\n\n要求："
        "\n1. short_title 是一句话短标题，12 到 22 个汉字，不要任何标点符号。"
        "\n2. short_title 要有悬念或反差，但必须忠于文案事实，不要标题党造假。"
        "\n3. description 是视频描述，80 到 140 个汉字，适合发视频号/抖音/小红书。"
        "\n4. description 开头要吸引人，点出故事冲突和人物精神，可以自然带一点情绪价值。"
        "\n5. description 不要写成片头文案，不要写“本视频讲述”，不要堆砌空话。"
        "\n6. 只返回 JSON，不要 Markdown，不要解释。"
        "\n\n文案内容："
        f"\n{script[:1200]}"
        "\n\n返回格式："
        '\n{"short_title": "一句话短标题", "description": "吸引人的视频描述"}'
    )

    payload = {
        "model": bigmodel_model(),
        "messages": [
            {"role": "system", "content": "你只输出可解析 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.82,
        "top_p": 0.9,
        "max_tokens": 500,
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
        short_title = strip_title_punctuation(result.get("short_title", ""))[:22]
        description = re.sub(r"\s+", " ", str(result.get("description", "")).strip())[:180]
        if not short_title or not description:
            return fallback_publish_assistant(script)
        return {"short_title": short_title, "description": description}
    except Exception as exc:
        return {"short_title": "", "description": "", "error": str(exc)[:200]}


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
