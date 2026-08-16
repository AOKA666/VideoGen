from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from services.storyboard_style import (
    STORYBOARD_STYLE_PROMPT,
    sanitize_storyboard_visual_prompt,
)

try:
    import jieba
except ImportError:  # Keyword overlap is diagnostic only; rewriting must still work without jieba.
    jieba = None

MIN_REWRITE_DIFFERENCE = 75
MAX_REWRITE_CONTINUOUS_REUSE = 10
MAX_REWRITE_SOURCE_PHRASE_REUSE = 18
MAX_REWRITE_SENTENCE_IMITATION = 25
MAX_REWRITE_STRUCTURE_SIMILARITY = 72
MAX_REWRITE_DETAIL_DISTRIBUTION_SIMILARITY = 75
MAX_REWRITE_ATTEMPTS = 2
MIN_REWRITE_ATTRACTION_SCORE = 70
MAX_REWRITE_ANALYSIS_ATTEMPTS = 2
MAX_REWRITE_ANALYSIS_REQUEST_ATTEMPTS = 2
REWRITE_COMPRESSION_WARNING_RATIO = 0
MAX_AUTO_TITLE_LENGTH = 8


@lru_cache(maxsize=1)
def load_rewrite_creative_guidelines() -> str:
    """Load the editable rewrite brief without its standalone-use placeholders."""
    filename = "二创提示词.txt"
    candidates = (
        Path(__file__).resolve().parents[1] / filename,
        Path(__file__).resolve().parents[3] / filename,
    )
    for path in candidates:
        try:
            content = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        if not content:
            continue
        # The source file can also be pasted into a chat as a standalone prompt.
        # In the app, the source body is intentionally replaced by a fact brief,
        # and the protected opening is supplied separately below.
        content = content.split("请根据下面提供的原文进行二创：", 1)[0].rstrip()
        # The caller already supplies the writing role and task. Keep the
        # standalone introduction in the editable file, but avoid repeating it
        # in every API request.
        if "一、事实与主线" in content:
            content = "一、事实与主线" + content.split("一、事实与主线", 1)[1]
        return content
    return ""
TITLE_PUNCTUATION = re.compile(r"""[，。！？、；："'“”‘’《》【】（）—…\-.!?,;:()\[\]{}<>\s]""")
WEAK_COVER_TITLE_PATTERNS = (
    "伟大",
    "精神",
    "民族脊梁",
    "大国",
    "传奇",
    "故事",
    "感动",
    "震撼",
    "不简单",
    "值得铭记",
    "科学家",
    "人物",
    "铸就",
    "成就",
    "奉献",
    "贡献",
    "功勋",
    "报国",
    "守护中国",
    "照亮中国",
)
COVER_TITLE_ATTRACTION_WORDS = (
    "扣下",
    "炸掉",
    "抹掉",
    "坠毁",
    "病危",
    "临终",
    "不能",
    "不许",
    "被骂",
    "被拦",
    "被关",
    "被藏",
    "护住",
    "捐出",
    "消失",
    "隐姓",
    "埋名",
    "封锁",
    "回家",
    "父亲",
    "母亲",
    "最后",
    "凭什么",
    "为什么",
    "到底",
    "没人敢",
    "谁也没想到",
    "千万",
    "15块",
    "普通老太太",
    "院士",
    "回国",
    "海关",
    "箱子",
    "大桥",
    "胶鞋",
    "名单",
    "功劳簿",
    "热搜",
    "骂了多年",
    "捐出",
    "穿",
    "亲手",
    "点头",
    "女儿",
    "没回头",
    "反常",
    "离谱",
    "潜伏",
    "地下党",
    "卧底",
)
COVER_TITLE_SPOILER_COMBOS = (
    ("病危", "绝密"),
    ("病危", "图纸"),
    ("病危", "公文包"),
    ("病危", "国家机密"),
    ("去世", "绝密"),
    ("去世", "图纸"),
    ("去世", "公文包"),
    ("坠毁", "公文包"),
    ("坠毁", "国家机密"),
    ("真相", "泪目"),
    ("真相", "曝光"),
)
TITLE_OPEN_LOOP_WORDS = (
    "却", "竟", "反而", "偏偏", "不能", "不许", "没", "没有", "为何",
    "为什么", "到底", "凭什么", "谁", "真相", "最后", "消失", "扣下", "被骂",
    "被拦", "被关", "炸掉", "抹掉", "坠毁", "病危", "临终", "拒绝", "撕毁", "封锁", "普通",
)
TITLE_SUMMARY_ENDINGS = (
    "铸就", "成就", "造就", "建成", "研制成功", "创造奇迹", "为国争光", "奉献一生",
    "守护祖国", "守护中国", "改变中国", "照亮中国", "功勋卓著", "终获成功",
)
TITLE_FAKE_CONTRAST_PATTERNS = (
    "没先",
    "没有先",
    "不先",
    "并未先",
    "并没有先",
)
TITLE_FACT_SENSITIVE_MODIFIERS = (
    "独自",
    "独自一人",
    "立刻",
    "立即",
    "马上",
    "转身",
    "掉头",
)
TITLE_SEQUENCE_ACTION_PATTERN = re.compile(
    r"先(?:回|走|带|救|做|去|离|送|逃|撤|留|拿|找|赶|处理|安排|开|说|问|看|吃|睡|买|卖|给|让|把)"
)
TITLE_VAGUE_PRONOUN_DIALOGUE_PATTERN = re.compile(r"^[他她](?:说|问|喊|哭|劝|求|答|回|告诉)")
RANDOM = random.SystemRandom()
class RewriteQualityError(RuntimeError):
    def __init__(self, result: dict):
        comparison = result.get("rewrite_comparison") or {}
        difference = comparison.get("overall_difference", 0)
        super().__init__(
            f"rewrite quality rejected: difference={difference}%, "
            f"length={comparison.get('text2_length', 0)}/{comparison.get('text1_length', 0)} "
            f"({comparison.get('length_ratio', 0)}%), "
            f"continuous_reuse={comparison.get('continuous_reuse', 0)}%, "
            f"source_phrase_reuse={comparison.get('source_phrase_reuse', comparison.get('phrase_overlap', 0))}%, "
            f"sentence_imitation={comparison.get('sentence_imitation', 0)}%"
        )
        self.result = result


class RewriteGenerationError(RuntimeError):
    def __init__(self, stage: str, cause: Exception):
        self.stage = stage
        self.cause = cause
        cause_name = type(cause).__name__
        cause_detail = str(cause).strip() or cause_name
        self.detail = f"AI rewrite failed during {stage}: {cause_name}: {cause_detail}"[:1000]
        super().__init__(self.detail)


def content_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def compact_similarity_text(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text or "")


def phrase_shingles(text: str, size: int = 6) -> set[str]:
    cleaned = compact_similarity_text(text)
    if len(cleaned) < size:
        return {cleaned} if cleaned else set()
    return {cleaned[index:index + size] for index in range(len(cleaned) - size + 1)}


def keyword_terms(text: str) -> set[str]:
    stopwords = {
        "一个", "这个", "那个", "他们", "我们", "你们", "自己", "什么", "怎么", "就是",
        "不是", "没有", "已经", "后来", "当时", "因为", "所以", "但是", "而且", "如果",
        "为了", "可以", "还是", "直到", "终于", "开始", "这样", "那样", "这里", "那里",
    }
    if jieba is not None:
        tokens = jieba.cut(str(text or ""), cut_all=False)
    else:
        tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z]+|\d+(?:年|月|日|岁)?", str(text or ""))
    return {
        token.strip().lower()
        for token in tokens
        if len(token.strip()) >= 2 and token.strip() not in stopwords
    }


def sentence_imitation_rate(text1: str, text2: str) -> int:
    source_sentences = [item for item in split_sentences(text1) if content_length(item) >= 6]
    target_sentences = [item for item in split_sentences(text2) if content_length(item) >= 6]
    if not source_sentences or not target_sentences:
        return 0
    imitated = 0
    for source in source_sentences:
        source_compact = compact_similarity_text(source)
        source_terms = keyword_terms(source)
        best_score = 0.0
        for target in target_sentences:
            target_compact = compact_similarity_text(target)
            char_score = SequenceMatcher(None, source_compact, target_compact, autojunk=False).ratio()
            target_terms = keyword_terms(target)
            term_union = source_terms | target_terms
            term_score = len(source_terms & target_terms) / len(term_union) if term_union else 0.0
            best_score = max(best_score, (char_score * 0.45) + (term_score * 0.55))
        if best_score >= 0.40:
            imitated += 1
    semantic_rate = round((imitated / len(source_sentences)) * 100)

    # Detect sentence-for-sentence rewriting even when most words were replaced
    # with synonyms. A genuinely reconstructed draft should not preserve nearly
    # the same sentence count, relative positions and sentence sizes throughout.
    aligned_rate = 0
    count_ratio = len(target_sentences) / len(source_sentences)
    if 0.7 <= count_ratio <= 1.4:
        aligned = 0
        for index, source in enumerate(source_sentences):
            target_index = min(
                len(target_sentences) - 1,
                round(index * (len(target_sentences) - 1) / max(1, len(source_sentences) - 1)),
            )
            target = target_sentences[target_index]
            source_compact = compact_similarity_text(source)
            target_compact = compact_similarity_text(target)
            length_ratio = len(target_compact) / max(1, len(source_compact))
            char_score = SequenceMatcher(None, source_compact, target_compact, autojunk=False).ratio()
            shared_terms = keyword_terms(source) & keyword_terms(target)
            if 0.65 <= length_ratio <= 1.55 and (char_score >= 0.18 or bool(shared_terms)):
                aligned += 1
        aligned_rate = round((aligned / len(source_sentences)) * 100)
    return max(semantic_rate, aligned_rate)


def remove_protected_opening(text: str, protected_opening: str) -> str:
    if not protected_opening:
        return str(text or "")
    source = str(text or "").lstrip()
    opening = str(protected_opening).strip()
    if source.startswith(opening):
        return source[len(opening):].lstrip(" \t\r\n，。！？!?；;")
    return source


def remove_protected_passages(text: str, passages: list[str] | None) -> str:
    cleaned = str(text or "")
    for passage in passages or []:
        exact = str(passage or "").strip()
        if exact:
            cleaned = cleaned.replace(exact, "")
    return cleaned


def ai_outline_fragments(text: str) -> list[str]:
    body = str(text or "")
    patterns = (
        r"先(?:说|讲|看|介绍)[\s\S]{0,120}?(?:再|然后)(?:说|讲|看|介绍)[\s\S]{0,120}?(?:最后|最终)(?:再)?(?:说|讲|看|介绍)",
        r"(?:第一|首先)[：:,，][\s\S]{0,120}?(?:第二|其次)[：:,，][\s\S]{0,120}?(?:第三|最后)[：:,，]",
        r"(?:接下来我们来看|下面再讲|下面来说|接着我们来看)",
    )
    matches: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, body):
            fragment = re.sub(r"\s+", "", match.group())[:160]
            if fragment and fragment not in matches:
                matches.append(fragment)
    return matches[:6]


def sentence_structure_signature(text: str) -> list[str]:
    signatures = []
    for sentence in split_sentences(text):
        compact = compact_similarity_text(sentence)
        if not compact:
            continue
        length_bucket = min(6, len(compact) // 12)
        ending = "question" if sentence.rstrip().endswith(("？", "?")) else "statement"
        opener = "plain"
        if re.match(r"^(但|然而|可是|偏偏|没想到|直到)", sentence):
            opener = "turn"
        elif re.match(r"^(因为|由于|为了|正是)", sentence):
            opener = "cause"
        elif re.match(r"^(后来|随后|此后|当时|那一年|\d{4}年)", sentence):
            opener = "time"
        quoted = "quote" if re.search(r"[“”\"‘’]", sentence) else "narration"
        signatures.append(f"{length_bucket}:{ending}:{opener}:{quoted}")
    return signatures


def sequence_structure_similarity(text1: str, text2: str) -> int | None:
    signature1 = sentence_structure_signature(text1)
    signature2 = sentence_structure_signature(text2)
    if min(len(signature1), len(signature2)) < 5:
        return None
    return round(SequenceMatcher(None, signature1, signature2, autojunk=False).ratio() * 100)


def paragraph_length_profile(text: str) -> list[float]:
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", str(text or "")) if item.strip()]
    if len(paragraphs) < 3:
        return []
    lengths = [max(1, content_length(item)) for item in paragraphs]
    total = sum(lengths)
    return [item / total for item in lengths]


def resample_profile(values: list[float], size: int = 8) -> list[float]:
    if not values:
        return []
    if len(values) == 1:
        return [values[0]] * size
    sampled = []
    for index in range(size):
        position = index * (len(values) - 1) / max(1, size - 1)
        lower = int(position)
        upper = min(len(values) - 1, lower + 1)
        weight = position - lower
        sampled.append(values[lower] * (1 - weight) + values[upper] * weight)
    total = sum(sampled) or 1
    return [item / total for item in sampled]


def detail_distribution_similarity(text1: str, text2: str) -> int | None:
    profile1 = resample_profile(paragraph_length_profile(text1))
    profile2 = resample_profile(paragraph_length_profile(text2))
    if not profile1 or not profile2:
        return None
    distance = sum(abs(left - right) for left, right in zip(profile1, profile2)) / 2
    return round(max(0.0, 1 - distance) * 100)


def rewrite_format_issues(text: str) -> list[str]:
    """Find mechanical time transitions and non-Arabic numeric expressions."""
    body = str(text or "")
    issues: list[str] = []
    stiff_time_patterns = (
        r"(?:把|将|让)?时间(?:线)?(?:回到|拨回|推回|推到|推进到|拉回|带回|来到|一推)[^，。！？\n]{0,24}",
        r"(?:时间一推|转眼到了|一转眼来到)[^，。！？\n]{0,24}",
    )
    for pattern in stiff_time_patterns:
        for match in re.finditer(pattern, body):
            fragment = match.group().strip()
            if fragment and fragment not in issues:
                issues.append(fragment)

    year_digit = "零〇一二三四五六七八九壹贰叁肆伍陆柒捌玖"
    year_unit_digit = year_digit + "十百千拾佰仟"
    year_patterns = (
        rf"(?<![{year_unit_digit}])[{year_digit}]{{4}}(?=年)",
        rf"(?<![{year_unit_digit}])[{year_unit_digit}]*[千仟][{year_unit_digit}]+(?=年)",
        rf"(?<![{year_unit_digit}])[一二三四五六七八九壹贰叁肆伍陆柒捌玖]?[十拾](?=年代)",
    )
    for pattern in year_patterns:
        for match in re.finditer(pattern, body):
            fragment = match.group().strip()
            if fragment and fragment not in issues:
                issues.append(fragment)
    return issues[:12]


def compare_scripts(
    text1: str,
    text2: str,
    protected_opening: str = "",
    protected_passages: list[str] | None = None,
) -> dict:
    source_length = content_length(text1)
    rewritten_length = content_length(text2)
    length_ratio = round((rewritten_length / source_length) * 100) if source_length else 0
    min_rewritten_length = 0
    length_passed = bool(str(text2 or "").strip())
    max_rewritten_length = 0
    # The fixed opening must remain verbatim, so it is excluded from every
    # similarity and reconstruction metric.
    body1 = remove_protected_opening(text1, protected_opening)
    body2 = remove_protected_opening(text2, protected_opening)
    body1 = remove_protected_passages(body1, protected_passages)
    body2 = remove_protected_passages(body2, protected_passages)
    outline_fragments = ai_outline_fragments(body2)
    outline_structure_passed = not outline_fragments
    format_issues = rewrite_format_issues(body2)
    rewrite_format_passed = not format_issues
    compact1 = compact_similarity_text(body1)
    compact2 = compact_similarity_text(body2)
    total_chars = len(compact1) + len(compact2)
    matcher = SequenceMatcher(None, compact1, compact2, autojunk=False)
    min_reuse_chars = min(8, max(1, min(len(compact1), len(compact2))))
    reused_blocks = [block for block in matcher.get_matching_blocks() if block.size >= min_reuse_chars]
    reused_chars = sum(block.size for block in reused_blocks)
    continuous_reuse = round((reused_chars * 2 / total_chars) * 100) if total_chars else 0

    shingles1 = phrase_shingles(compact1)
    shingles2 = phrase_shingles(compact2)
    shared_phrases = shingles1 & shingles2
    phrase_union = shingles1 | shingles2
    phrase_overlap = round((len(shared_phrases) / len(phrase_union)) * 100) if phrase_union else 0
    # Measure how much of the source survives in the rewrite. Unlike Jaccard,
    # this cannot be diluted by appending unrelated new paragraphs.
    source_phrase_reuse = round((len(shared_phrases) / len(shingles1)) * 100) if shingles1 else 0

    terms1 = keyword_terms(body1)
    terms2 = keyword_terms(body2)
    term_union = terms1 | terms2
    keyword_overlap = round((len(terms1 & terms2) / len(term_union)) * 100) if term_union else 0

    sentence_imitation = sentence_imitation_rate(body1, body2)
    structure_similarity = sequence_structure_similarity(body1, body2)
    detail_similarity = detail_distribution_similarity(body1, body2)
    structure_similarity_passed = structure_similarity is None or structure_similarity <= MAX_REWRITE_STRUCTURE_SIMILARITY
    detail_distribution_passed = detail_similarity is None or detail_similarity <= MAX_REWRITE_DETAIL_DISTRIBUTION_SIMILARITY
    overall_similarity = round(
        (continuous_reuse * 0.35)
        + (source_phrase_reuse * 0.35)
        + (sentence_imitation * 0.30)
    )
    overall_difference = max(0, min(100, 100 - overall_similarity))
    structure_difference = 100 - structure_similarity if structure_similarity is not None else overall_difference
    detail_difference = 100 - detail_similarity if detail_similarity is not None else overall_difference
    narrative_difference = round(overall_difference * 0.6 + structure_difference * 0.2 + detail_difference * 0.2)
    reused_passages = sorted(
        {compact1[block.a:block.a + block.size] for block in reused_blocks},
        key=len,
        reverse=True,
    )[:8]

    # Only direct textual dependence is a hard expression gate. Sentence
    # rhythm, paragraph shape and detail distribution remain useful
    # diagnostics, but making every stylistic metric pass simultaneously made
    # otherwise usable drafts fail unpredictably.
    non_length_quality_passed = (
        overall_difference >= MIN_REWRITE_DIFFERENCE
        and continuous_reuse <= MAX_REWRITE_CONTINUOUS_REUSE
        and source_phrase_reuse <= MAX_REWRITE_SOURCE_PHRASE_REUSE
        and rewrite_format_passed
    )
    passed = non_length_quality_passed and length_passed
    return {
        "continuous_reuse": continuous_reuse,
        "phrase_overlap": phrase_overlap,
        "source_phrase_reuse": source_phrase_reuse,
        "sentence_imitation": sentence_imitation,
        "structure_similarity": structure_similarity,
        "structure_similarity_passed": structure_similarity_passed,
        "detail_distribution_similarity": detail_similarity,
        "detail_distribution_passed": detail_distribution_passed,
        "narrative_difference": narrative_difference,
        "keyword_overlap": keyword_overlap,
        # Backward-compatible aliases for existing stored projects and API clients.
        "character_similarity": continuous_reuse,
        "semantic_similarity": keyword_overlap,
        "overall_difference": overall_difference,
        "text1_length": source_length,
        "text2_length": rewritten_length,
        "length_ratio": length_ratio,
        "length_passed": length_passed,
        "non_length_quality_passed": non_length_quality_passed,
        "outline_structure_passed": outline_structure_passed,
        "outline_structure_fragments": outline_fragments,
        "rewrite_format_passed": rewrite_format_passed,
        "rewrite_format_issues": format_issues,
        "min_rewritten_length": min_rewritten_length,
        "max_rewritten_length": max_rewritten_length,
        "protected_opening_length": content_length(protected_opening),
        "protected_passage_count": len([item for item in protected_passages or [] if str(item).strip()]),
        "reused_passages": reused_passages,
        "common_keywords": sorted(terms1 & terms2, key=len, reverse=True)[:10],
        "unique_keywords1": sorted(terms1 - terms2, key=len, reverse=True)[:10],
        "unique_keywords2": sorted(terms2 - terms1, key=len, reverse=True)[:10],
        "passed": passed,
    }


def split_sentences(text: str) -> list[str]:
    parts = re.findall(r"[^。！？!?；;]+[。！？!?；;]?", str(text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def normalized_protagonists(fact_brief: dict | None) -> list[str]:
    values = (fact_brief or {}).get("protagonists")
    if not isinstance(values, list):
        return []
    names: list[str] = []
    for value in values:
        name = str(value or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _fact_card_number(value: object) -> int | None:
    if isinstance(value, dict):
        value = value.get("card", value.get("id", value.get("index")))
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else None


def normalize_rewrite_fact_coverage(audit: dict, fact_brief: dict | None) -> dict:
    cards = (fact_brief or {}).get("material_cards")
    cards = cards if isinstance(cards, list) else []
    indexed_cards: dict[int, object] = {}
    for index, card in enumerate(cards, start=1):
        number = _fact_card_number(card) or index
        indexed_cards[number] = card
    required_cards = {
        number for number, card in indexed_cards.items()
        if not isinstance(card, dict)
        or str(card.get("priority") or "must").strip().lower() == "must"
    }
    expected = required_cards

    covered = {
        number for number in (_fact_card_number(item) for item in audit.get("covered_cards", []))
        if number in expected
    }
    partial_items = audit.get("partial_cards") if isinstance(audit.get("partial_cards"), list) else []
    missing_items = audit.get("missing_cards") if isinstance(audit.get("missing_cards"), list) else []
    partial = {number for number in (_fact_card_number(item) for item in partial_items) if number in expected}
    missing = {number for number in (_fact_card_number(item) for item in missing_items) if number in expected}
    # Only must cards are hard coverage requirements. Support and discardable
    # cards may be omitted when they do not serve the selected core thesis.
    unresolved = expected - covered
    failed = sorted(unresolved | partial | missing)

    reasons: dict[int, str] = {}
    for item in [*partial_items, *missing_items]:
        number = _fact_card_number(item)
        if number in expected and isinstance(item, dict):
            reasons[number] = str(item.get("missing") or item.get("reason") or "内容未完整写入").strip()
    missing_fact_cards = [
        {
            "card": number,
            "fact": str(indexed_cards.get(number, "")),
            "missing": reasons.get(number, "审稿未确认该素材卡已完整写入"),
        }
        for number in failed
    ]
    order_items = audit.get("out_of_order_cards")
    order_items = order_items if isinstance(order_items, list) else []
    timeline_order_passed = audit.get("timeline_order_passed") is not False and not order_items
    emotional_items = audit.get("emotional_issues")
    emotional_items = emotional_items if isinstance(emotional_items, list) else []
    emotional_quality_passed = audit.get("emotional_quality_passed") is not False and not emotional_items
    attraction_items = audit.get("attraction_issues")
    attraction_items = attraction_items if isinstance(attraction_items, list) else []
    attraction_score_available = audit.get("attraction_score") is not None
    try:
        attraction_score = max(0, min(100, int(audit.get("attraction_score") or 0)))
    except (TypeError, ValueError):
        attraction_score = 0
    unsupported_claims = audit.get("unsupported_claims")
    unsupported_claims = unsupported_claims if isinstance(unsupported_claims, list) else []
    factual_grounding_passed = (
        audit.get("factual_grounding_passed") is not False
        and not unsupported_claims
    )
    return {
        "fact_coverage_passed": bool(expected) and not failed,
        "timeline_order_passed": timeline_order_passed,
        "timeline_order_issues": order_items,
        "emotional_quality_passed": emotional_quality_passed,
        "emotional_issues": emotional_items,
        "attraction_score": attraction_score,
        "attraction_score_available": attraction_score_available,
        "attraction_quality_passed": (
            not attraction_score_available
            or attraction_score >= MIN_REWRITE_ATTRACTION_SCORE
        ),
        "attraction_issues": attraction_items,
        "factual_grounding_passed": factual_grounding_passed,
        "unsupported_claims": unsupported_claims,
        "covered_fact_cards": sorted(covered),
        "expected_fact_cards": len(expected),
        "missing_fact_cards": missing_fact_cards,
        "fact_coverage_summary": str(audit.get("summary") or "").strip(),
    }


def apply_rewrite_fact_coverage_quality(result: dict, coverage: dict) -> dict:
    comparison = result.get("rewrite_comparison") or {}
    comparison.update(coverage)
    if "attraction_quality_passed" not in comparison and comparison.get("attraction_score") is not None:
        comparison["attraction_score_available"] = True
        try:
            attraction_score = int(comparison.get("attraction_score") or 0)
        except (TypeError, ValueError):
            attraction_score = 0
        comparison["attraction_quality_passed"] = attraction_score >= MIN_REWRITE_ATTRACTION_SCORE
    if "length_passed" not in comparison:
        comparison["length_passed"] = True
    comparison["compression_warning"] = False
    comparison["passed"] = (
        bool(comparison.get("non_length_quality_passed"))
        and bool(comparison.get("fact_coverage_passed"))
        and comparison.get("factual_grounding_passed") is not False
        and comparison.get("timeline_order_passed") is not False
        and comparison.get("emotional_quality_passed") is not False
        and comparison.get("attraction_quality_passed") is not False
    )
    result["rewrite_comparison"] = comparison
    return result


def paragraphize_script(text: str) -> str:
    sentences = split_sentences(text)
    if sentences:
        return "\n".join(sentences)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        return "\n".join(lines)
    return text.strip()


def clean_auto_title(text: str) -> str:
    title = re.sub(r"^(标题|项目名称|短标题|片名)[:：]", "", str(text or "").strip())
    return TITLE_PUNCTUATION.sub("", title).strip()


def looks_like_truncated_sentence(title: str, raw_script: str) -> bool:
    if not title or not raw_script:
        return False
    sentences = split_sentences(raw_script)
    cleaned_sentences = [clean_auto_title(sentence) for sentence in sentences]
    if not cleaned_sentences:
        cleaned_sentences = [clean_auto_title(raw_script)]

    # Reject an unfinished prefix such as “他是中国最专情” as before, but inspect
    # every sentence instead of only the opening sentence.
    if any(len(sentence) > len(title) + 3 and sentence.startswith(title) for sentence in cleaned_sentences):
        return True

    # A model may satisfy a length instruction by cutting through the first word
    # of a source phrase (for example “最专情的地下党员” -> “情的地下党员”).
    # Detect that broken left boundary.  jieba gives us word boundaries when it is
    # available; the small fallback covers the common “X的...” fragment shape.
    compact_source = clean_auto_title(raw_script)
    start = compact_source.find(title)
    if start > 0:
        if jieba is not None:
            boundaries = {0}
            cursor = 0
            for token in jieba.cut(compact_source, cut_all=False):
                cursor += len(token)
                boundaries.add(cursor)
            if start not in boundaries:
                return True
        elif re.match(r"^[\u4e00-\u9fff]{1,2}的", title):
            return True
    return False


def extract_title_subject(raw_script: str) -> str:
    text = re.sub(r"\s+", "", raw_script or "")
    if not text:
        return ""
    opening_subject = re.match(r"^([\u4e00-\u9fff]{2,6})(?=[，。])", text)
    if opening_subject and any(
        marker in opening_subject.group(1)
        for marker in ("三姐妹", "两兄弟", "父子", "母女", "夫妻", "团队")
    ):
        return opening_subject.group(1)
    patterns = [
        r"(?:这个人叫|这个人就是|他叫|她叫|名叫|名字叫)([\u4e00-\u9fff]{2,4})",
        r"(?:这个(?:地下党员|党员|科学家|作家|院士|专家|工程师|英雄)叫)([\u4e00-\u9fff]{2,4})",
        r"(?:科学家|作家|院士|专家|工程师|英雄)([\u4e00-\u9fff]{2,4})(?=[，。！？])",
        r"^([\u4e00-\u9fff]{2,6})(?=[，。])",
    ]
    weak_subjects = {"一个年轻人", "一个中国人", "很多人", "你知道吗", "谁能想到"}
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1)
            if candidate not in weak_subjects and not candidate.startswith(("一个", "这位", "这个")):
                return candidate
    return ""


def auto_title_is_grounded(title: str, raw_script: str) -> bool:
    cleaned = clean_auto_title(title)
    text = re.sub(r"\s+", "", raw_script or "")
    if not cleaned or not text:
        return False
    subject = extract_title_subject(raw_script)
    # When the document identifies a central person, a project title must name
    # that person.  This prevents a locally copied identity/emotion fragment from
    # being mistaken for a title that represents the whole document.
    if subject:
        return subject in cleaned
    generic_pairs = {"故事", "背后", "往事", "人生", "命运", "选择", "时刻", "传奇"}
    title_pairs = {
        cleaned[index:index + 2]
        for index in range(max(0, len(cleaned) - 1))
        if cleaned[index:index + 2] not in generic_pairs
    }
    return any(pair in text for pair in title_pairs)


def extract_leading_title(raw_script: str) -> str:
    """Use at most the first eight script characters, stopping at punctuation."""
    source = re.sub(r"\s+", "", str(raw_script or ""))
    source = re.sub(r"^[，。！？、；：\"'“”‘’《》【】（）—…\-.!?,;:()\[\]{}<>]+", "", source)
    if not source:
        return "未命名项目"

    title_chars: list[str] = []
    for char in source:
        if TITLE_PUNCTUATION.fullmatch(char):
            break
        title_chars.append(char)
        if len(title_chars) == MAX_AUTO_TITLE_LENGTH:
            break
    return "".join(title_chars) or "未命名项目"


def fallback_infer_title(raw_script: str) -> str:
    return extract_leading_title(raw_script)


def normalize_auto_title(title: str, raw_script: str) -> str:
    return extract_leading_title(raw_script)


def infer_title(raw_script: str) -> str:
    return extract_leading_title(raw_script)


def extract_opening_hook(raw_script: str, preserve_rule: str = "auto") -> str:
    chars_match = re.fullmatch(r"chars_(\d+)", preserve_rule)
    if chars_match:
        char_count = max(1, min(int(chars_match.group(1)), 500))
        return str(raw_script or "")[:char_count]
    if preserve_rule == "first_paragraph":
        paragraphs = re.split(r"\n\s*\n", str(raw_script or "").strip())
        return paragraphs[0].strip() if paragraphs else ""
    sentences = split_sentences(raw_script)
    if not sentences:
        return ""
    if preserve_rule == "first_sentence":
        return sentences[0].strip()

    # Automatic protection keeps exactly the first sentence. Explicit user
    # selections (chars_N or first_paragraph) are handled above.
    return sentences[0].strip()


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


def ensure_original_opening(raw_script: str, rewritten_script: str, preserve_rule: str = "auto") -> str:
    raw_hook = extract_opening_hook(raw_script, preserve_rule)
    if not raw_hook:
        return rewritten_script.strip()

    rewritten = rewritten_script.strip()
    # Paragraph normalization changes blank lines to single newlines before this
    # check. Compare the opening without formatting so an already preserved
    # multi-paragraph hook is not prepended a second time.
    if compact_text(rewritten).startswith(compact_text(raw_hook)):
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


def add_blank_lines_between_paragraphs(text: str) -> str:
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n\n".join(paragraphs)


def merge_short_script_paragraphs(text: str, max_chars: int = 40) -> str:
    """Merge adjacent short paragraphs without changing their text or order."""
    paragraphs = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(paragraphs) < 2:
        return str(text or "").strip()
    merged: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}{paragraph}" if current else paragraph
        if current and content_length(candidate) > max_chars:
            merged.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        merged.append(current)
    return "\n\n".join(merged)


def minimax_endpoint() -> str:
    return os.getenv("MINIMAX_ENDPOINT", "https://api.minimaxi.com/v1")


def minimax_model() -> str:
    return os.getenv("MINIMAX_MODEL", "MiniMax-M3")


def deepseek_endpoint() -> str:
    return os.getenv("DEEPSEEK_ENDPOINT", "https://api.deepseek.com")


def deepseek_model() -> str:
    return os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


STORYBOARD_MODEL_PROVIDERS = {"minimax", "deepseek", "openai"}


def normalize_storyboard_model_provider(value: object) -> str:
    provider = str(value or "deepseek").strip().lower()
    if provider not in STORYBOARD_MODEL_PROVIDERS:
        raise ValueError("storyboard model provider must be minimax, deepseek, or openai")
    return provider


def _storyboard_model_config(provider: object) -> tuple[str, str, str, str]:
    normalized = normalize_storyboard_model_provider(provider)
    if normalized == "openai":
        return (
            os.getenv("OPENAI_API_KEY", "").strip(),
            os.getenv("OPENAI_ENDPOINT", "https://api.openai.com/v1").strip()
            or "https://api.openai.com/v1",
            os.getenv("OPENAI_MODEL", "gpt-5.6").strip() or "gpt-5.6",
            "OpenAI",
        )
    if normalized == "minimax":
        return (
            os.getenv("MINIMAX_API_KEY", "").strip(),
            minimax_endpoint(),
            minimax_model(),
            "MiniMax",
        )
    return (
        os.getenv("DEEPSEEK_API_KEY", "").strip(),
        deepseek_endpoint(),
        deepseek_model(),
        "DeepSeek",
    )


def normalize_sales_book_title(title: str) -> tuple[str, str]:
    bare = str(title or "").strip().strip("《》").strip() or "国之脊梁"
    return bare, f"《{bare}》"


@lru_cache(maxsize=8)
def load_book_promotion_guidelines(book_title: str) -> str:
    bare, formatted = normalize_sales_book_title(book_title)
    filename = "带书提示词.txt"
    candidates = (
        Path(__file__).resolve().parents[1] / filename,
        Path(__file__).resolve().parents[3] / filename,
    )
    for path in candidates:
        try:
            content = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        if not content:
            continue
        match = re.search(
            rf"(?ms)^{re.escape(formatted)}\s*$\n(.*?)(?=^《[^》]+》\s*$|\Z)",
            content,
        )
        if match:
            return match.group(1).strip()
    return ""


def fallback_book_promotion(book_title: str) -> str:
    bare, formatted = normalize_sales_book_title(book_title)
    if bare == "女性人物传记":
        return (
            "她真正让人放不下的，不只是经历过什么，而是在爱情、婚姻、事业和人生选择面前，始终要为自己的决定承担代价。\n\n"
            "很多女性都会走到相似的路口：既想保有自己，也要面对关系、现实和内心的拉扯。看懂别人的选择，有时也是在替自己寻找答案。"
            "\n\n"
            "这套女性人物传记写的是杨绛、陆小曼、张爱玲、林徽因和三毛，五个女人，五种命运，里面有爱情与婚姻，也有才华、自由、清醒、孤独和重新开始。"
            "读她们，不是为了照搬谁的人生，而是从别人的得失里看清选择、理解代价，少走一些弯路。"
            "如果你也正站在人生的路口，不妨把这套书带回去慢慢读，也许你想不明白的答案，她们早已用一生替你走过。"
        )
    if bare == "历史深处的民国":
        return (
            "这个人的命运，不能只用成败、忠奸或好坏来概括。个人每一次选择的背后，都站着一个剧烈变化的时代。\n\n"
            "从晚清走向共和，再到军阀混战、改革救亡和全面抗战，许多看似矛盾的人和事，只有放回当时的处境才能真正看懂。"
            "\n\n"
            f"{formatted}以时间和人物为线索，把晚清崩塌、民国人物的不同选择，以及军阀、革命、改革和抗战之间的关系讲得通俗清楚。"
            "它能帮你把课本里零散的人名和事件连成完整脉络，也看见简单结论之外更复杂、更真实的历史。"
            f"想真正看懂这段历史，可以把{formatted}带回去慢慢读。读懂那个时代，才能理解这些人物为什么走上不同的道路。"
        )
    return (
        "这个人最打动人的，不只是最后取得了什么成就，而是在国家最需要的时候，把个人前途、家庭生活和漫长岁月交给了一件必须有人完成的事。\n\n"
        "在他身后，还有一代中国科学家和科技工作者做过同样的选择。他们甘坐冷板凳、突破技术封锁，让中国科技从一穷二白一步步走到今天。"
        "\n\n"
        f"{formatted}记录的正是这些院士和科学家的成长经历、科研人生、家国情怀与精神传承，也让那些长期被成就遮住的名字重新被看见。"
        "这本书不仅能让人理解责任、信仰、坚持和家国担当，也能让孩子认识什么才是真正值得追逐的榜样。"
        f"想了解更多国之脊梁背后的故事，可以把{formatted}带回去慢慢读，尤其适合家长和孩子一起读。"
    )


def ensure_rewrite_book_promotion(script: str, enabled: bool, book_title: str) -> str:
    rewritten = str(script or "").strip()
    if not enabled:
        return rewritten
    bare, formatted = normalize_sales_book_title(book_title)
    if bare in rewritten[-240:]:
        return rewritten
    promotion = fallback_book_promotion(formatted)
    return f"{rewritten}\n\n{promotion}" if rewritten else promotion


def fallback_rewrite_script(
    raw_script: str,
    style: str = "纪实故事型",
    preserve_rule: str = "auto",
    append_book_promotion: bool = False,
    promotion_book_title: str = "国之脊梁",
) -> dict:
    sentences = split_sentences(raw_script)
    title = infer_title(raw_script)
    hook = extract_opening_hook(raw_script, preserve_rule) or build_fallback_hook(raw_script, title)
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
    rewritten = ensure_rewrite_book_promotion(rewritten, append_book_promotion, promotion_book_title)
    return {
        "title": title,
        "hook": hook,
        "rewritten_script": rewritten,
        "script_style": style,
        "rewrite_provider": "local_fallback",
        "rewrite_error": "",
        "rewrite_comparison": compare_scripts(
            raw_script,
            rewritten,
            protected_opening=hook,
        ),
    }


def ensure_min_rewrite_difference(result: dict) -> dict:
    comparison = result.get("rewrite_comparison") or {}
    if not comparison.get("passed", False):
        result["rewrite_warning"] = build_rewrite_quality_warning(comparison)
        result["rewrite_quality_status"] = "quality_warning"
        result["rewrite_error"] = ""
    return result


def build_rewrite_quality_warning(comparison: dict) -> str:
    issues: list[str] = []
    if comparison.get("outline_structure_passed") is False:
        fragments = "；".join(comparison.get("outline_structure_fragments") or [])
        issues.append(f"存在提纲式、步骤式 AI 表达：{fragments or '先说、再说、最后说等结构'}")
    if comparison.get("rewrite_format_passed") is False:
        fragments = "；".join(comparison.get("rewrite_format_issues") or [])
        issues.append(
            "数字或时间转场格式不合格："
            f"{fragments or '正文应使用阿拉伯数字，并直接以年份或事件进入新阶段'}"
        )
    if comparison.get("fact_coverage_passed") is False:
        missing_cards = comparison.get("missing_fact_cards") or []
        card_summaries = []
        for item in missing_cards[:8]:
            if isinstance(item, dict):
                card_summaries.append(f"素材卡 {item.get('card')}：{item.get('missing') or item.get('fact')}")
        issues.append("重要事实覆盖不完整：" + ("；".join(card_summaries) or "存在未写入的素材卡"))
    if comparison.get("factual_grounding_passed") is False:
        unsupported = comparison.get("unsupported_claims") or []
        summaries = []
        for item in unsupported[:6]:
            if isinstance(item, dict):
                summaries.append(str(item.get("claim") or item.get("reason") or item))
            else:
                summaries.append(str(item))
        issues.append("存在资料卡无法支持的新增事实：" + ("；".join(summaries) or "成稿加入了无依据事实"))
    if comparison.get("timeline_order_passed") is False:
        order_issues = comparison.get("timeline_order_issues") or []
        summaries = []
        for item in order_issues[:6]:
            if isinstance(item, dict):
                summaries.append(str(item.get("reason") or item.get("issue") or item))
            else:
                summaries.append(str(item))
        issues.append("正文时间线乱序：" + ("；".join(summaries) or "素材卡没有按真实时间顺序出现"))
    if comparison.get("emotional_quality_passed") is False:
        emotional_issues = comparison.get("emotional_issues") or []
        summaries = []
        for item in emotional_issues[:6]:
            if isinstance(item, dict):
                summaries.append(str(item.get("reason") or item.get("issue") or item))
            else:
                summaries.append(str(item))
        issues.append("情感递进不足或失真：" + ("；".join(summaries) or "关键代价和关系变化没有形成情绪落点"))
    if comparison.get("attraction_quality_passed") is False:
        attraction_issues = comparison.get("attraction_issues") or []
        issues.append(
            f"吸引力仅 {comparison.get('attraction_score', 0)} 分，低于 "
            f"{MIN_REWRITE_ATTRACTION_SCORE} 分："
            + ("；".join(str(item) for item in attraction_issues[:3]) or "冲突、悬念或情绪推进不足")
        )
    if comparison.get("compression_warning"):
        issues.append(
            f"成稿约为原文的 {comparison.get('length_ratio', 0)}%，低于 "
            f"最低 {REWRITE_COMPRESSION_WARNING_RATIO}% 的篇幅要求"
        )
    expression_problems = []
    if int(comparison.get("overall_difference") or 0) < MIN_REWRITE_DIFFERENCE:
        expression_problems.append("整体表达仍接近原文")
    if int(comparison.get("continuous_reuse") or 0) > MAX_REWRITE_CONTINUOUS_REUSE:
        expression_problems.append("存在连续复用")
    source_phrase_reuse = int(comparison.get("source_phrase_reuse") or 0)
    if source_phrase_reuse > MAX_REWRITE_SOURCE_PHRASE_REUSE:
        expression_problems.append("原文短语偏多")
    sentence_imitation = int(comparison.get("sentence_imitation") or 0)
    if sentence_imitation > MAX_REWRITE_SENTENCE_IMITATION:
        expression_problems.append("句子推进方式相似")
    if comparison.get("structure_similarity_passed") is False:
        expression_problems.append("句式节奏相似")
    if comparison.get("detail_distribution_passed") is False:
        expression_problems.append("详略分配相似")
    if expression_problems:
        issues.append("独立表达不足：" + "、".join(expression_problems))
    detail = "；".join(issues) or "部分质量指标未达到建议值"
    return f"二创稿已生成并保留，但质量检查未完全达标：{detail}。你可以直接编辑、复制或再次改写。"


def normalize_rewrite_result(
    result: dict,
    raw_script: str,
    style: str,
    preserve_rule: str = "auto",
    append_book_promotion: bool = False,
    promotion_book_title: str = "国之脊梁",
    verified_quotes: list[str] | None = None,
) -> dict:
    title = str(result.get("title") or infer_title(raw_script)).strip()
    hook = extract_opening_hook(raw_script, preserve_rule) or str(result.get("hook") or build_fallback_hook(raw_script, title)).strip()
    rewritten_script = str(result.get("rewritten_script") or "").strip()
    if not rewritten_script:
        rewritten_script = fallback_rewrite_script(
            raw_script, style, preserve_rule, append_book_promotion, promotion_book_title
        )["rewritten_script"]
    rewritten_script = clean_rewritten_script(raw_script, rewritten_script)
    rewritten_script = ensure_original_opening(raw_script, rewritten_script, preserve_rule)
    rewritten_script = add_blank_lines_between_paragraphs(rewritten_script)
    rewritten_script = ensure_rewrite_book_promotion(
        rewritten_script, append_book_promotion, promotion_book_title
    )
    comparison = compare_scripts(
        raw_script,
        rewritten_script,
        protected_opening=hook,
        protected_passages=verified_quotes,
    )
    return {
        "title": normalize_auto_title(title, raw_script),
        "hook": hook,
        "rewritten_script": rewritten_script,
        "script_style": str(result.get("script_style") or style),
        "rewrite_provider": result.get("rewrite_provider") or minimax_model(),
        "rewrite_error": result.get("rewrite_error", ""),
        "rewrite_comparison": comparison,
        "rewrite_difference": comparison["overall_difference"],
    }


def rewrite_minimum_fact_items(raw_length: int) -> int:
    if raw_length <= 240:
        return 1
    if raw_length <= 600:
        return 2
    if raw_length <= 1000:
        return 3
    return 4


def build_rewrite_analysis_prompt(raw_script: str, preserve_rule: str = "auto") -> str:
    raw_len = content_length(raw_script)
    protected_opening = extract_opening_hook(raw_script, preserve_rule)
    return f"""你是事实编辑。完整通读原文，给写作模型整理一份中性事实底稿，不写文案，不保留原文修辞和段落结构。

规则：
1. 每个独立事件一张 material_card，按真实时间排序。删除后会破坏核心命题、主线、因果、人物关系、关键选择或结果的标为 must；能帮助理解主线但可以压缩合并的标为 support；重复背景、旁支履历、同类成就和与核心命题无关的内容标为 discardable。
2. must 卡写清人物、事件、原因、动作、结果、代价和关键数字；support 卡保持简短并说明它如何服务主线；discardable 卡只记录事实方向。must 必须完整进入成稿，support 仅在服务核心命题时保留，discardable 允许写作模型删除。不要为了凑数量拆卡，也不要把所有卡默认标成 must。
3. fact 和 details 使用“主体｜动作｜对象｜结果｜数字”式短数据，不写成可直接用于口播的完整句子，不复制原文的四字短语、比喻、排比、设问和转折。
4. 只记录原文明示的事实，不推测心理，不补写眼泪、台词或评价。资料卡不是摘要。直接引语只进入 verified_quotes，不得同时复制到卡片内容。
5. 只有原文明确包含人物处境、选择、牺牲、实际代价、关系变化或他人反应时，才把 emotion_focus 设为 true，并在 emotional_stakes 中记录可核实依据。全篇通常标记1到3张最有情绪价值的卡，不得靠主观评价凑数。
6. protagonists 列出主要人物完整姓名；多人关系写入 protagonist_relationship。
7. 只有可确认说话者和语境的直接引语才能进入 verified_quotes。
8. 原文含图书推荐时记录原始意图、卖点、读者和承接角度；没有则 present=false。
9. JSON 字符串内部需要引号时使用中文引号“”。

只返回以下 JSON，不使用 Markdown：
{{
  "core_subject": "本篇主人公概括",
  "protagonists": ["主要人物完整姓名1", "主要人物完整姓名2"],
  "protagonist_relationship": "人物关系或最简身份",
  "core_conflict": "核心冲突",
  "timeline_verified": true,
  "material_cards": [{{"id": 1, "priority": "must", "emotion_focus": true, "time": "时间阶段", "person": "人物姓名", "fact": "中性事实", "details": "原因、动作、结果、代价、数字", "emotional_stakes": "原文明示的处境、选择、代价或关系变化"}}, {{"id": 2, "priority": "support", "emotion_focus": false, "time": "时间阶段", "person": "人物姓名", "fact": "服务主线的背景", "details": "必要信息"}}, {{"id": 3, "priority": "discardable", "emotion_focus": false, "time": "时间阶段", "person": "人物姓名", "fact": "重复或旁支事实", "details": "可删除原因"}}],
  "must_preserve_terms": ["人名地名年份数字专名"],
  "verified_quotes": ["可核实的原文直接引语"],
  "book_promotion": {{"present": false, "original_intent": "", "selling_points": [], "target_readers": [], "transition_angle": ""}}
}}

<source_length>{raw_len}</source_length>
<protected_opening>{protected_opening}</protected_opening>
<raw_script>{raw_script}</raw_script>
"""


def nested_value_content_length(value: object) -> int:
    if isinstance(value, dict):
        return sum(nested_value_content_length(item) for item in value.values())
    if isinstance(value, list):
        return sum(nested_value_content_length(item) for item in value)
    return content_length(str(value or ""))


def normalize_rewrite_fact_brief(result: dict, raw_length: int = 0) -> dict:
    if not isinstance(result, dict):
        raise ValueError("Rewrite analysis must return a JSON object")
    timeline = result.get("timeline") if isinstance(result.get("timeline"), list) else []
    facts = result.get("facts") if isinstance(result.get("facts"), list) else []
    material_cards = result.get("material_cards") if isinstance(result.get("material_cards"), list) else []
    if not (timeline or facts or material_cards):
        raise ValueError("Rewrite analysis did not return usable material cards")
    promotion = result.get("book_promotion")
    if not isinstance(promotion, dict):
        promotion = {"present": False, "facts": []}
    viral_analysis = dict(result.get("viral_analysis")) if isinstance(result.get("viral_analysis"), dict) else {}
    if not str(viral_analysis.get("opening_continuation") or "").strip() and viral_analysis.get("hook"):
        viral_analysis["opening_continuation"] = viral_analysis.get("hook")
    viral_analysis.pop("hook", None)
    structure_summary = result.get("structure_summary") if isinstance(result.get("structure_summary"), dict) else {}
    raw_section_plan = result.get("section_plan") if isinstance(result.get("section_plan"), list) else []
    section_plan = []
    legacy_emotional_nodes = []
    for item in raw_section_plan:
        if not isinstance(item, dict):
            section_plan.append(item)
            continue
        normalized_item = dict(item)
        legacy_beat = str(normalized_item.pop("emotional_beat", "") or "").strip()
        cards_for_stage = normalized_item.get("cards")
        if legacy_beat and isinstance(cards_for_stage, list) and cards_for_stage:
            legacy_emotional_nodes.append({"card": cards_for_stage[0], "beat": legacy_beat})
        section_plan.append(normalized_item)
    protagonists = normalized_protagonists(result)
    protagonist_relationship = str(result.get("protagonist_relationship") or "").strip()
    normalized_material_cards = []
    for item in material_cards:
        if not isinstance(item, dict):
            normalized_material_cards.append(item)
            continue
        normalized_item = dict(item)
        priority = str(normalized_item.get("priority") or "must").strip().lower()
        if priority == "mergeable":
            priority = "support"
        if priority not in {"must", "support", "discardable"}:
            priority = "must"
        normalized_item["priority"] = priority
        legacy_beat = str(normalized_item.pop("emotional_beat", "") or "").strip()
        if legacy_beat:
            legacy_emotional_nodes.append({"card": normalized_item.get("id"), "beat": legacy_beat})
        for optional_field in ("emotional_stakes", "relationship_change"):
            if not str(normalized_item.get(optional_field) or "").strip():
                normalized_item.pop(optional_field, None)
        expansion_level = str(normalized_item.get("expansion_level") or "").strip().lower()
        if expansion_level not in {"focus", "support", "brief"}:
            expansion_level = "support" if priority == "must" else "brief"
        normalized_item["expansion_level"] = expansion_level
        normalized_material_cards.append(normalized_item)
    material_cards = normalized_material_cards
    fact_item_count = len(timeline) + len(facts) + len(material_cards)
    minimum_fact_items = rewrite_minimum_fact_items(raw_length) if raw_length else 1
    material_content = {
        "timeline": timeline,
        "facts": facts,
        "material_cards": material_cards,
        "relationships": result.get("relationships") or [],
        "protagonists": protagonists,
        "protagonist_relationship": protagonist_relationship,
        "viral_analysis": viral_analysis,
        "structure_summary": structure_summary,
        "section_plan": section_plan,
    }
    material_length = nested_value_content_length(material_content)
    material_density_ratio = round((material_length / raw_length) * 100) if raw_length else 0
    minimum_section_count = min(4, max(1, (fact_item_count + 1) // 2))
    section_plan_passed = len(section_plan) >= minimum_section_count
    protagonist_identity_passed = bool(protagonists)
    structured_cards = [item for item in material_cards if isinstance(item, dict)]
    non_must_count = sum(
        1 for item in structured_cards
        if str(item.get("priority") or "").strip().lower() in {"support", "discardable"}
    )
    priority_balance_passed = len(structured_cards) < 6 or non_must_count > 0
    focus_count = sum(1 for item in structured_cards if item.get("expansion_level") == "focus")
    maximum_focus_count = max(1, (len(structured_cards) * 3 + 4) // 5)
    expansion_balance_passed = len(structured_cards) < 4 or focus_count <= maximum_focus_count
    # A usable brief needs facts and identifiable subjects. Section planning,
    # Focus balance and non-must-card ratios are writing aids, not reasons to
    # call the source analysis unusable and request it again.
    coverage_passed = fact_item_count > 0 and protagonist_identity_passed
    verified_quotes = result.get("verified_quotes")
    verified_quotes = [str(item).strip() for item in verified_quotes] if isinstance(verified_quotes, list) else []
    emotional_arc = result.get("emotional_arc")
    emotional_arc = emotional_arc if isinstance(emotional_arc, list) else []
    normalized_emotional_arc = []
    for item in emotional_arc:
        if isinstance(item, dict):
            beat = str(item.get("beat") or "").strip()
            if beat:
                normalized_emotional_arc.append({"card": item.get("card"), "beat": beat})
        else:
            beat = str(item or "").strip()
            if beat:
                normalized_emotional_arc.append(beat)
    if not normalized_emotional_arc:
        seen_legacy_cards = set()
        for item in legacy_emotional_nodes:
            card = item.get("card")
            if not item.get("beat") or card in seen_legacy_cards:
                continue
            seen_legacy_cards.add(card)
            normalized_emotional_arc.append(item)
    raw_narrative_angles = result.get("narrative_angles")
    raw_narrative_angles = raw_narrative_angles if isinstance(raw_narrative_angles, list) else []
    narrative_angles = []
    for item in raw_narrative_angles:
        if isinstance(item, dict):
            strategy = str(item.get("strategy") or "").strip()
            guidance = str(item.get("guidance") or "").strip()
            focus_cards = item.get("focus_cards") if isinstance(item.get("focus_cards"), list) else []
        else:
            strategy, guidance, focus_cards = str(item or "").strip(), "", []
        if strategy and strategy not in {angle["strategy"] for angle in narrative_angles}:
            narrative_angles.append({"strategy": strategy, "focus_cards": focus_cards[:6], "guidance": guidance})
    return {
        "source_length": raw_length,
        "fact_item_count": fact_item_count,
        "minimum_fact_items": minimum_fact_items,
        "material_length": material_length,
        "minimum_material_length": 0,
        "material_density_ratio": material_density_ratio,
        "timeline_verified": result.get("timeline_verified") is not False,
        "minimum_section_count": minimum_section_count,
        "section_plan_passed": section_plan_passed,
        "protagonist_identity_passed": protagonist_identity_passed,
        "priority_balance_passed": priority_balance_passed,
        "expansion_balance_passed": expansion_balance_passed,
        "fact_coverage_passed": coverage_passed,
        "core_subject": str(result.get("core_subject") or "").strip(),
        "protagonists": protagonists,
        "protagonist_relationship": protagonist_relationship,
        "core_conflict": str(result.get("core_conflict") or "").strip(),
        "key_choice": str(result.get("key_choice") or "").strip(),
        "story_outcome": str(result.get("story_outcome") or "").strip(),
        "timeline": timeline,
        "facts": facts,
        "material_cards": material_cards,
        "relationships": result.get("relationships") if isinstance(result.get("relationships"), list) else [],
        "must_preserve_terms": result.get("must_preserve_terms") if isinstance(result.get("must_preserve_terms"), list) else [],
        "verified_quotes": [item for item in verified_quotes if item],
        "viral_analysis": viral_analysis,
        "narrative_angles": narrative_angles[:3],
        "emotional_arc": normalized_emotional_arc[:5],
        "structure_summary": structure_summary,
        "section_plan": section_plan,
        "book_promotion": {
            "present": bool(promotion.get("present")),
            "facts": promotion.get("facts") if isinstance(promotion.get("facts"), list) else [],
            "original_intent": str(promotion.get("original_intent") or "").strip(),
            "selling_points": promotion.get("selling_points") if isinstance(promotion.get("selling_points"), list) else [],
            "target_readers": promotion.get("target_readers") if isinstance(promotion.get("target_readers"), list) else [],
            "transition_angle": str(promotion.get("transition_angle") or "").strip(),
        },
    }


def parse_rewrite_analysis_json(content: str) -> dict:
    """Parse analysis JSON and repair common MiniMax punctuation mistakes."""
    candidate = extract_json(content)
    for _ in range(20):
        try:
            parsed = json.loads(candidate)
            if not isinstance(parsed, dict):
                raise ValueError("Rewrite analysis must return a JSON object")
            return parsed
        except json.JSONDecodeError as exc:
            if exc.msg != "Expecting ',' delimiter":
                raise
            position = exc.pos
            if position >= len(candidate):
                raise

            # A missing comma before the next object key. The decoder normally
            # points at that key, but some provider outputs include whitespace.
            key_match = re.match(r'\s*("(?:[^"\\]|\\.)*"\s*:)', candidate[position:])
            if key_match:
                key_position = position + len(key_match.group(0)) - len(key_match.group(1))
                previous = key_position - 1
                while previous >= 0 and candidate[previous].isspace():
                    previous -= 1
                if previous >= 0 and candidate[previous] in {'"', ']', '}'} | set("0123456789"):
                    candidate = candidate[:key_position] + "," + candidate[key_position:]
                    continue

            # Missing comma between array items or adjacent objects.
            previous = position - 1
            while previous >= 0 and candidate[previous].isspace():
                previous -= 1
            if (
                previous >= 0
                and candidate[previous] in {'"', ']', '}'} | set("0123456789")
                and candidate[position] in {'"', '[', '{'}
            ):
                candidate = candidate[:position] + "," + candidate[position:]
                continue

            # An ASCII quote was used inside a JSON string without escaping,
            # for example: "details":"他说"马上回家"". The decoder treats
            # the first inner quote as the end of the value and points at the
            # following text. Escape that quote and let the next pass repair
            # any matching inner quote in the same value.
            previous = position - 1
            while previous >= 0 and candidate[previous].isspace():
                previous -= 1
            if previous >= 0 and candidate[previous] == '"' and candidate[position] not in ",}]":
                backslashes = 0
                cursor = previous - 1
                while cursor >= 0 and candidate[cursor] == "\\":
                    backslashes += 1
                    cursor -= 1
                if backslashes % 2 == 0:
                    candidate = candidate[:previous] + "\\" + candidate[previous:]
                    continue
            raise
    raise ValueError("Rewrite analysis JSON needs too many repairs")


def fallback_rewrite_fact_brief(
    raw_script: str,
    error: Exception,
    protected_opening: str = "",
) -> dict:
    """Keep rewriting available when the provider repeatedly emits bad JSON."""
    original_source = str(raw_script or "").strip()
    source = original_source
    if protected_opening and source.startswith(protected_opening):
        source = source[len(protected_opening):].lstrip()
    if not source:
        source = original_source
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？!?；;])\s*|\n+", source)
        if item.strip()
    ]
    if not sentences and source:
        sentences = [source]

    # Merge very short sentences while keeping cards small enough for the
    # writing model to reorganize instead of treating the source as one block.
    cards: list[str] = []
    buffer = ""
    for sentence in sentences:
        if buffer and content_length(buffer + sentence) > 180:
            cards.append(buffer)
            buffer = sentence
        else:
            buffer += sentence
    if buffer:
        cards.append(buffer)

    if len(cards) == 1 and content_length(cards[0]) > 240:
        text = cards[0]
        cards = [text[index:index + 160] for index in range(0, len(text), 160)]

    inferred_subject = extract_title_subject(original_source)
    protagonists = [inferred_subject] if inferred_subject else []
    card_count = len(cards)
    fallback_material_cards = []
    must_signal = re.compile(
        r"(决定|拒绝|选择|回国|离开|被捕|牺牲|死亡|去世|离婚|成功|完成|发明|研制|解决|结果|真相|获奖)"
    )
    for index, card in enumerate(cards, 1):
        is_boundary = index == 1 or index == card_count
        priority = "must" if is_boundary or must_signal.search(card) else "support"
        fallback_material_cards.append({
            "id": index,
            "priority": priority,
            "expansion_level": "focus" if is_boundary else ("support" if priority == "must" else "brief"),
            "time": "时间待核",
            "person": "按原文事实交代",
            "fact": card,
            "details": "保底资料卡按原文出现顺序生成，编号不代表真实时间",
        })

    task_sets = {
        1: ("完整讲述核心事件并收束",),
        2: ("交代人物与事件起点", "推进结果并收束"),
        3: ("交代人物与事件起点", "推进冲突与选择", "交代结果并收束"),
        4: ("承接开头并交代人物", "推进核心事件", "展开选择与代价", "交代结果并收束"),
    }
    stage_count = min(4, max(1, (card_count + 1) // 2))
    tasks = task_sets[stage_count]
    section_plan = []
    for index, task in enumerate(tasks):
        start = (card_count * index) // len(tasks)
        end = (card_count * (index + 1)) // len(tasks)
        assigned = list(range(start + 1, end + 1))
        if not assigned and card_count:
            assigned = [min(index + 1, card_count)]
        section_plan.append({"task": task, "cards": assigned})

    brief = normalize_rewrite_fact_brief({
        "core_subject": "、".join(protagonists[:4]) or "原文人物与事件",
        "protagonists": protagonists,
        "protagonist_relationship": "按原文事实交代",
        "core_conflict": "根据资料卡提炼核心冲突",
        "timeline_verified": False,
        "material_cards": fallback_material_cards,
        "section_plan": section_plan,
        "viral_analysis": {},
        "narrative_angles": [
            {"strategy": "核心冲突", "focus_cards": [1], "guidance": "围绕主要阻力及其解决过程推进"},
            {"strategy": "关键选择", "focus_cards": [max(1, card_count // 2)], "guidance": "突出人物选择以及选择带来的后果"},
            {"strategy": "行动与结果", "focus_cards": [max(1, card_count)], "guidance": "用关键行动串联过程并落到真实结果"},
        ],
        "emotional_arc": [],
        "book_promotion": {
            "present": bool(re.search(
                r"(《[^》]+》|这本书|书里|书中|翻开|读完|买给孩子|下单|购买|小黄车|带书|卖书)",
                original_source,
            )),
            "original_intent": "",
            "selling_points": [],
            "target_readers": [],
            "transition_angle": "",
        },
        "verified_quotes": [],
    }, content_length(source))
    brief["analysis_warning"] = (
        "MiniMax 两次返回的资料卡 JSON 均无法解析，系统已按原文段落生成保底资料卡继续二创；"
        f"建议生成后重点核对事实。错误：{type(error).__name__}: {str(error)[:160]}"
    )
    return brief


def request_minimax_rewrite_analysis(
    raw_script: str,
    api_key: str,
    preserve_rule: str = "auto",
) -> dict:
    protected_opening = extract_opening_hook(raw_script, preserve_rule)
    base_prompt = build_rewrite_analysis_prompt(raw_script, preserve_rule)
    raw_len = content_length(raw_script)
    analysis_timeout = max(
        30,
        min(300, int(os.getenv("MINIMAX_ANALYSIS_TIMEOUT_SECONDS", "120"))),
    )
    last_brief: dict | None = None
    last_analysis_error: Exception | None = None
    for attempt in range(1, MAX_REWRITE_ANALYSIS_ATTEMPTS + 1):
        retry_note = ""
        if last_analysis_error:
            retry_note = (
                "\n\n上一版返回的 JSON 无法解析，错误为："
                f"{type(last_analysis_error).__name__}: {str(last_analysis_error)[:240]}。"
                "请重新输出完整且严格合法的 JSON；检查所有逗号、引号、括号，"
                "不要使用 Markdown 代码块，不要在 JSON 前后添加解释；字符串内容中的引号改用中文引号“”，"
                "不要直接写未转义的英文双引号；通过去掉重复措辞控制 JSON 长度，不能压缩或省略素材卡事实。"
            )
        elif last_brief:
            retry_note = (
                "\n\n上一版事实底稿缺少可识别的核心事实或主要人物。"
                "请重新通读原文，补齐主要人物姓名、人物关系以及会影响主线、因果和结果的 must 事件；"
                "能帮助理解主线的背景标记 support，重复或无关旁支标记 discardable；不要增加传播分析、情绪设计或叙事计划。"
            )
        payload = {
            "model": minimax_model(),
            "messages": [
                {"role": "system", "content": "你只做原文事实拆解，只输出可解析 JSON，不写二创稿。"},
                {"role": "user", "content": base_prompt + retry_note},
            ],
            "temperature": 0.1,
            "top_p": 0.8,
            "max_tokens": max(2000, min(7000, round(raw_len * 1.4) + 1200)),
            "stream": False,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{minimax_endpoint().rstrip('/')}/chat/completions",
            data=data,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        body: dict | None = None
        for request_attempt in range(1, MAX_REWRITE_ANALYSIS_REQUEST_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(req, timeout=analysis_timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"MiniMax analysis API {exc.code}: {error_body}") from exc
            except (TimeoutError, urllib.error.URLError) as exc:
                is_timeout = isinstance(exc, TimeoutError) or isinstance(
                    getattr(exc, "reason", None), TimeoutError
                )
                if not is_timeout:
                    raise
                if request_attempt >= MAX_REWRITE_ANALYSIS_REQUEST_ATTEMPTS:
                    raise RuntimeError(
                        "MiniMax source analysis timed out after "
                        f"{MAX_REWRITE_ANALYSIS_REQUEST_ATTEMPTS} requests "
                        f"({analysis_timeout}s timeout each)"
                    ) from exc
        if body is None:
            raise RuntimeError("MiniMax source analysis returned no response")
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        try:
            parsed_analysis = parse_rewrite_analysis_json(str(content))
            last_brief = normalize_rewrite_fact_brief(parsed_analysis, raw_len)
            last_analysis_error = None
        except (json.JSONDecodeError, ValueError) as exc:
            last_analysis_error = exc
            continue
        if last_brief["fact_coverage_passed"]:
            return last_brief
        if attempt < MAX_REWRITE_ANALYSIS_ATTEMPTS:
            continue
        last_brief["analysis_warning"] = "事实底稿仍不完整，已使用当前版本继续写作。"
        return last_brief
    if last_brief:
        last_brief["analysis_warning"] = "事实底稿未完全解析，已使用当前最完整版本继续写作。"
        return last_brief
    if last_analysis_error:
        return fallback_rewrite_fact_brief(raw_script, last_analysis_error, protected_opening)
    raise RuntimeError("Rewrite analysis did not return a usable fact brief")


def rewrite_narrative_strategies(fact_brief: dict | None) -> list[dict]:
    brief = fact_brief or {}
    cards = [item for item in brief.get("material_cards") or [] if isinstance(item, dict)]
    emotional_ids = [
        item.get("id") for item in cards
        if item.get("emotion_focus") is True and item.get("id") is not None
    ]
    must_ids = [
        item.get("id") for item in cards
        if str(item.get("priority") or "must").lower() == "must" and item.get("id") is not None
    ]
    focus_ids = emotional_ids or must_ids or [
        item.get("id") for item in cards
        if item.get("expansion_level") == "focus" and item.get("id") is not None
    ]
    all_ids = [item.get("id") for item in cards if item.get("id") is not None]
    return [
        {
            "strategy": "冲突悬念",
            "focus_cards": focus_ids[:2] or all_ids[:2],
            "guidance": (
                "从资料卡最反常的真实结果或最大阻力建立主悬念，把背景压进动作发生的当下；"
                "每揭开一部分答案，就接上新的处境、问题或后果，不作生平铺陈。"
            ),
        },
        {
            "strategy": "选择代价",
            "focus_cards": focus_ids[-2:] or all_ids[-3:],
            "guidance": (
                "以几次不可回避的选择为支点，先让观众看见人物可以失去什么，再写行动与实际代价；"
                "用短暂希望和更大困难形成情绪起伏，最终落到真实结果。"
            ),
        },
    ]


def rewrite_writing_brief(fact_brief: dict | None, attempt: int) -> dict:
    """Expose the complete neutral fact set without legacy planning metadata."""
    brief = fact_brief or {}
    cards = [item for item in brief.get("material_cards") or [] if isinstance(item, dict)]
    allowed_keys = (
        "core_subject", "protagonists", "protagonist_relationship", "core_conflict",
        "timeline_verified", "timeline", "facts", "relationships",
        "must_preserve_terms", "verified_quotes", "book_promotion",
    )
    writing_brief = {key: brief.get(key) for key in allowed_keys if key in brief}
    writing_brief["material_cards"] = cards
    return writing_brief


def rewrite_expression_profiles() -> list[dict]:
    """Return deliberately incompatible prose blueprints for candidate diversity."""
    return [
        {
            "name": "现场推进",
            "guidance": "段落从时间、地点、动作或可见处境进入；长短句交替，以动作后的结果收段；少用设问和评价句。",
        },
        {
            "name": "冷静纪实",
            "guidance": "先给可核实事实，再补原因或影响；语气克制，段落疏密不均；不用排比、感叹和拔高式收尾。",
        },
        {
            "name": "关系变化",
            "guidance": "通过人物之间的距离、回应和责任变化推进；事实仍按时间顺序，但段落边界不得照搬资料卡或原文。",
        },
        {
            "name": "证据链条",
            "guidance": "用数字、文件、物件、工作步骤等事实证据串联；避免先评价后举例，也避免每段都以人物姓名开头。",
        },
        {
            "name": "后果递进",
            "guidance": "每段只追问上一事件造成了什么新处境，再进入下一行动；结论延后，禁止连续使用转折词制造节奏。",
        },
        {
            "name": "口述回忆",
            "guidance": "像知情者平实复述，句子自然但不松散；重要处放慢，履历快速带过；不用网络热词和模板金句。",
        },
    ]


def build_rewrite_prompt(
    raw_script: str,
    style: str,
    attempt: int,
    previous: dict | None = None,
    preserve_rule: str = "auto",
    fact_brief: dict | None = None,
    append_book_promotion: bool = False,
    promotion_book_title: str = "国之脊梁",
    expression_profile: dict | None = None,
) -> str:
    opening_hook = extract_opening_hook(raw_script, preserve_rule)
    narrative_strategies = rewrite_narrative_strategies(fact_brief)
    narrative_strategy = narrative_strategies[(max(1, attempt) - 1) % len(narrative_strategies)]
    expression_profile = expression_profile or rewrite_expression_profiles()[
        (max(1, attempt) - 1) % len(rewrite_expression_profiles())
    ]
    source_has_book_promotion = bool(re.search(
        r"(《[^》]+》|这本书|书里|书中|翻开|读完|买给孩子|下单|购买|小黄车|带书|卖书|推荐给家长)",
        raw_script,
    ))
    brief_promotion = (fact_brief or {}).get("book_promotion") or {}
    has_book_promotion = bool(brief_promotion.get("present", source_has_book_promotion))
    promotion_details = {
        "original_intent": str(brief_promotion.get("original_intent") or "").strip(),
        "selling_points": brief_promotion.get("selling_points") or [],
        "target_readers": brief_promotion.get("target_readers") or [],
        "transition_angle": str(brief_promotion.get("transition_angle") or "").strip(),
    }
    _, formatted_book_title = normalize_sales_book_title(promotion_book_title)
    if append_book_promotion:
        selected_book_guidelines = load_book_promotion_guidelines(formatted_book_title)
        conversion_instruction = (
            f"用户已开启结尾带书，选中的商品是{formatted_book_title}。"
            "先完成二创故事正文，再在文末续写带书结尾；带书内容不得反过来改变、删减或重写故事正文。"
            "严格执行下面这本书的专属规则，其他书的主题、人物、卖点和购买理由不得混入：\n"
            f"<selected_book_promotion_rules>\n{selected_book_guidelines or fallback_book_promotion(formatted_book_title)}\n"
            "</selected_book_promotion_rules>"
        )
    else:
        conversion_instruction = (
            "原文含带书内容：依据 book_promotion 中的 original_intent、selling_points、target_readers 和 transition_angle 保留原有转化意图并重新表达，不扩大篇幅，不写成硬广。"
            f"原始转化资料：{json.dumps(promotion_details, ensure_ascii=False)}。"
            if has_book_promotion else
            "原文不含带书内容：禁止主动添加书名、阅读感受、购买或推荐话术，按故事主题自然收束。"
        )
    timeline_verified = (fact_brief or {}).get("timeline_verified") is not False
    chronology_instruction = (
        "资料卡按真实发生时间排列。事件的实际先后和因果不得写反；允许先预告资料卡已有的真实结果、代价或反差，再回到起点解释，但预告不算事件提前发生，也不要反复跳跃。"
        if timeline_verified else
        "这是保底资料卡，卡片 id 仅代表原文出现顺序。先依据 time、人物年龄、事件因果和明确年份恢复真实发生顺序；允许预告有依据的结果，但不能把事件先后或因果写反。"
    )
    retry_instruction = ""
    if previous:
        comparison = previous.get("rewrite_comparison") or {}
        reused_passages = [
            str(item).strip()
            for item in comparison.get("reused_passages", [])
            if str(item).strip()
        ]
        reused_summary = "；".join(reused_passages[:8]) or "系统未定位到单一长句，请全面检查正文表达"
        structure_issues = []
        if int(comparison.get("sentence_imitation") or 0) > MAX_REWRITE_SENTENCE_IMITATION:
            structure_issues.append("句子推进和信息出现位置与原文过于接近")
        if int(comparison.get("continuous_reuse") or 0) > MAX_REWRITE_CONTINUOUS_REUSE:
            structure_issues.append("存在连续复用原文表达")
        if int(comparison.get("source_phrase_reuse") or 0) > MAX_REWRITE_SOURCE_PHRASE_REUSE:
            structure_issues.append("原文短语复用过多")
        if comparison.get("structure_similarity_passed") is False:
            structure_issues.append("句长、转折位置和句式节奏与原文过于接近")
        if comparison.get("detail_distribution_passed") is False:
            structure_issues.append("各阶段篇幅和详略分配与原文过于接近")
        structure_summary = "；".join(structure_issues) or "需要进一步提高独立表达程度"
        retry_instruction = (
            f"上一版没有通过真正重写验收：总体重构度 {comparison.get('overall_difference', 0)}%，"
            f"固定开头之外的连续照抄率 {comparison.get('continuous_reuse', comparison.get('character_similarity', 0))}%，"
            f"原文短语复用率 {comparison.get('source_phrase_reuse', comparison.get('phrase_overlap', 0))}%，"
            f"逐句模仿率 {comparison.get('sentence_imitation', 0)}%。"
            f"重点重复片段：{reused_summary}。"
            f"结构问题摘要：{structure_summary}。"
            "固定开头仍须原样保留。只回到事实资料卡重新独立写作，不提供也不得猜测上一版全文。"
            "本轮仍须完整覆盖 must 卡；support 卡按核心命题取舍，discardable 卡允许删除。不能恢复原文的句子和段落。"
            "上列重复片段不能只换同义词：将同类履历合并概括；独立事件则改换叙述主体、拆并句子并改变信息落点。"
            "不得按原文段落一一对应；保持真实时间顺序即可，不需要恢复原文的段落和事件密度。"
        )
        if comparison.get("outline_structure_passed") is False:
            fragments = "；".join(comparison.get("outline_structure_fragments") or [])
            retry_instruction += (
                f"\n【本轮必须删除提纲腔】上一版出现了：{fragments or '步骤式过渡'}。"
                "不得使用“先说、再说、最后说”“第一、第二、第三”或“接下来我们来看”等写作框架，"
                "必须让事件通过人物、时间、动作和因果自然衔接。"
            )
        if comparison.get("rewrite_format_passed") is False:
            fragments = "；".join(comparison.get("rewrite_format_issues") or [])
            retry_instruction += (
                f"\n【本轮必须修正数字与时间转场】上一版出现了：{fragments or '中文数字或生硬时间转场'}。"
                "除固定开头和 verified_quotes 外，具体年份使用阿拉伯数字；年龄、数量、金额、比例、序号和自然量词不作限制；"
                "进入新阶段时直接写“1956年，……”或直接写事件，"
                "禁止“时间回到”“时间拨回到”“把时间推到”“时间一推，就到了”“时间来到”等表达。"
            )
        if comparison.get("timeline_order_passed") is False:
            issues = comparison.get("timeline_order_issues") or []
            issue_text = "；".join(
                str(item.get("reason") or item.get("issue") or item)
                for item in issues[:8]
                if isinstance(item, dict)
            )
            retry_instruction += (
                f"\n【本轮必须修正时间线】{issue_text or '上一版把事件的真实先后或因果写反了'}。"
                "可以先预告资料卡已有的真实结果、代价或反差，但必须明确它是预告；"
                "解释过程时保持事件实际发生顺序和因果，不得把后发生的行动写成先发生。"
            )
        if comparison.get("emotional_quality_passed") is False:
            issues = comparison.get("emotional_issues") or []
            issue_text = "；".join(
                str(item.get("reason") or item.get("issue") or item)
                for item in issues[:6]
                if isinstance(item, dict)
            )
            retry_instruction += (
                f"\n【本轮必须写透情绪重点】{issue_text or '上一版没有写出事实中的处境、选择和实际代价'}。"
                "只展开 emotion_focus 卡已有的 emotional_stakes、动作、关系变化和他人反应；"
                "不得添加哭泣、心理活动、台词或资料卡没有的情节。"
            )
        if comparison.get("factual_grounding_passed") is False:
            unsupported = comparison.get("unsupported_claims") or []
            unsupported_text = "\n".join(
                f"- {item.get('claim') or item.get('reason') or item}"
                if isinstance(item, dict) else f"- {item}"
                for item in unsupported[:8]
            )
            retry_instruction += (
                "\n【本轮必须删除无依据新增事实】上一版出现以下资料卡无法支持的事实性陈述：\n"
                f"{unsupported_text or '- 审稿发现存在无法对应资料卡的新增事实'}\n"
                "删除这些内容，或只改写成不新增人物、时间、地点、事件、数字、因果和结果的概括性转场。"
                "不得为了增强戏剧性补造动作、物件、现场、心理、评价来源或他人反应。"
            )
        if comparison.get("attraction_quality_passed") is False:
            attraction_issues = comparison.get("attraction_issues") or []
            issue_text = "；".join(str(item) for item in attraction_issues[:3])
            retry_instruction += (
                f"\n【本轮必须提升吸引力】上一版吸引力 {comparison.get('attraction_score', 0)} 分，"
                f"低于 {MIN_REWRITE_ATTRACTION_SCORE} 分。{issue_text or '真实冲突、悬念或情绪推进不足'}。"
                "回到 must 与 emotion_focus 卡重建前段留人、阶段性问题和情绪起伏；"
                "只能增强信息安排与表达，不能新增事实或假悬念。"
            )
        if comparison.get("fact_coverage_passed") is False:
            missing_cards = comparison.get("missing_fact_cards") or []
            missing_text = "\n".join(
                f"- 素材卡 {item.get('card')}：{item.get('fact')}；缺少：{item.get('missing')}"
                for item in missing_cards
                if isinstance(item, dict)
            )
            retry_instruction += (
                "\n【本轮首要修正：补齐事实】上一版没有完整覆盖以下资料卡：\n"
                f"{missing_text or '- 审稿发现存在未完整写入的资料卡'}\n"
                "必须把这些事件的原因、结果、动作、人物代价和关键画面自然写回正文。"
                "允许重新组织和合并场景，但不能只补一句抽象结论，也不要复制素材卡原句。"
            )
    writing_brief = rewrite_writing_brief(fact_brief, attempt)
    fact_brief_json = json.dumps(writing_brief, ensure_ascii=False, indent=2)
    creative_guidelines = load_rewrite_creative_guidelines()
    prompt = f"""
你是一名短视频口播文案编剧。请只依据事实资料卡，独立创作一篇适合视频号发布的完整文案。你看不到原文正文，也不要猜测原文句式；系统会在生成后检查事实覆盖、篇幅和重复率。

【资料卡约定】
- 用 core_subject 和 core_conflict 确定主线。
- 时间线：{chronology_instruction}信息揭晓顺序可以调整，事件实际发生顺序和因果不能篡改。
- must 卡完整展开；support 卡只在服务核心命题时保留并允许合并；discardable 卡允许删除。不得因为删减旁支而破坏主线、因果、人物关系、关键选择和结果。
- emotion_focus=true 的卡是全篇最有情绪价值的事实节点。围绕 emotional_stakes 展开人物处境、选择、动作、实际代价、关系变化和他人反应；只能使用卡片依据，不得虚构煽情。
- 不设目标字数和篇幅上下限。篇幅服从主线和事实表达：关键节点写透，普通履历简洁合并，既不为压缩而删事实，也不为凑字数填空话。

【核心创作规则】
{creative_guidelines}

【本稿传播目标】
- 正确只是底线，成稿还必须让普通观众愿意继续听。先用一句话确定本篇唯一核心命题，再让保留的资料卡为它服务，不能写成履历汇总。
- 固定开头与紧接的前两段共同完成留人：只使用资料卡中最异常的结果、最强反差、最大困难或最重代价，建立一个主悬念；不要另起模板钩子。
- 不在前三句一次交代全部答案。除主悬念外，从 must 或 emotion_focus 卡中安排至少两个阶段性问题；答案逐步释放，每次释放都带出新的困难、选择或后果。
- 形成有起伏的情绪波形，避免从头到尾一直赞美或卖惨。允许的动力来自真实的受阻、希望、重击、选择和结果，不得为了戏剧性改变时间线。
- 优先使用资料卡已有的具体物件、动作、数字、工作步骤和他人反应承载情绪。专业贡献必须翻译成普通人能理解的实际变化，但不得夸大因果。
- 段尾优先落在尚未解决的新处境、新问题或不可逆后果上；不用“更震惊的是”“真正可怕的是”等空话制造假悬念。
- 具体年份使用阿拉伯数字；年龄、数量、金额、比例、序号和自然量词不作格式限制。时间切换直接写年份或事件，禁止“时间回到”“时间拨回到”“把时间推到”“时间一推，就到了”“时间来到”等机械转场。受保护内容仍须逐字保留。

【本次任务】
- 文案风格：{style}
- 候选稿 {attempt}/{MAX_REWRITE_ATTEMPTS}，本稿叙事策略：{narrative_strategy['strategy']}
- 策略说明：{narrative_strategy['guidance'] or '按该观察角度重新分配详略，但保持真实时间顺序。'}
- 本稿表达指纹：{expression_profile['name']}。{expression_profile['guidance']}
- 优先展开情绪与主线素材卡：{json.dumps(narrative_strategy['focus_cards'], ensure_ascii=False)}。support 内容只在服务核心命题时保留，discardable 内容允许删除。
- 不得把资料卡机械写成自然段，也不得让段落数量、段落长短和信息落点与原文形成一一对应。
- verified_quotes 之外的对话、演讲和遗言只能转成间接叙述，不得保留或仿写引号内原话。
- 固定开头必须一字不改并单独成段：{opening_hook}
- 后续正文必须自然承接固定开头，不能另起钩子；固定开头不参与重复率和总体重构度计算。
- 带书规则：{conversion_instruction}
{retry_instruction}

【输出】
只返回可解析 JSON，字段为 title、hook、rewritten_script、script_style。
rewritten_script 只能包含完整成稿正文，不得混入原文、分析、说明、标题标签或段落序号。
输出前只检查：固定开头是否原样保留；must 是否完整；support 取舍是否服务核心命题；情绪重点是否来自卡片依据；事件先后和因果是否真实；是否存在资料卡以外的事实。不要检查字数。

【事实资料卡】
<fact_brief>{fact_brief_json}</fact_brief>
"""
    return prompt


def request_minimax_rewrite_fact_coverage(
    fact_brief: dict,
    rewritten_script: str,
    api_key: str,
    protected_opening: str = "",
    allowed_book_promotion: str = "",
) -> dict:
    cards = fact_brief.get("material_cards")
    if not isinstance(cards, list) or not cards:
        return {
            "fact_coverage_passed": True,
            "factual_grounding_passed": True,
            "unsupported_claims": [],
            "timeline_order_passed": True,
            "timeline_order_issues": [],
            "emotional_quality_passed": True,
            "emotional_issues": [],
            "attraction_score": 0,
            "attraction_score_available": False,
            "attraction_quality_passed": True,
            "attraction_issues": [],
            "covered_fact_cards": [],
            "expected_fact_cards": 0,
            "missing_fact_cards": [],
            "fact_coverage_summary": "资料卡没有可审核的素材卡",
        }
    audit_input = {
        "core_subject": fact_brief.get("core_subject", ""),
        "core_conflict": fact_brief.get("core_conflict", ""),
        "protagonists": fact_brief.get("protagonists", []),
        "protagonist_relationship": fact_brief.get("protagonist_relationship", ""),
        "material_cards": cards,
        "timeline_verified": fact_brief.get("timeline_verified") is not False,
        "must_preserve_terms": fact_brief.get("must_preserve_terms", []),
        "verified_quotes": fact_brief.get("verified_quotes", []),
        "book_promotion": fact_brief.get("book_promotion", {}),
        "allowed_book_promotion": allowed_book_promotion,
    }
    prompt = f"""你主要检查资料卡内容是否被完整保留以及时间顺序，同时独立评估短视频吸引力。不要因为换了说法就判定事实缺失。

审核标准：
1. 检查完整成稿，包括 protected_opening。逐张审核 must 卡，不能漏号；must 必须完整展开。support 卡只在服务核心命题时保留，discardable 卡允许删除，二者未出现都不算缺失。
2. must 卡的核心事件、人物、原因、结果和关键数字已表达即为 covered；只有会改变事实方向的缺失才标 partial，整项未出现才标 missing。covered_cards、partial_cards 和 missing_cards 只填写 must 卡。
3. 反向检查成稿中的每个事实性陈述。人物、身份、时间、地点、关系、动作、事件、物件、数字、先后、因果和结果，必须能由 material_cards、verified_quotes 或 protected_opening 直接支持。无法对应的写入 unsupported_claims，并令 factual_grounding_passed=false。普通概括、价值评价、非事实转场和合规带书话术不算新增事实。
4. 对 emotion_focus=true 的卡，检查成稿是否突出该卡已有的处境、选择、动作、实际代价、关系变化或他人反应，形成情绪重点。
5. 不以字数、措辞或细节多少判定，不要求文案复述资料卡原句。
6. 只检查事件实际发生顺序和因果是否被写反。允许成稿先预告资料卡已有的真实结果、代价或反差，再回到起点解释；这种信息提前揭晓不算乱序。只有把后发生的行动写成先发生，或改变因果关系时才判 timeline_order_passed=false。
7. attraction_score 按 0 到 100 独立评分，不影响事实覆盖结论：前段是否迅速出现具体冲突或未解问题；是否有主悬念和至少两个阶段性推进；情绪是否有起伏；是否用真实细节而非空洞评价；专业贡献是否通俗；段落是否不断产生新处境、选择或后果；是否避免“时间拨回到”“时间一推”等机械转场。固定开头不可修改，不因它本身较弱而处罚，重点评价正文如何承接。
8. attraction_issues 最多列出三条最影响停留率或完播的问题。不要因为语言克制而扣分，也不要奖励无依据夸张、假悬念、虚构细节、重复设问和模板金句。
9. 只输出 JSON，不使用 Markdown。

输出格式：
{{
  "covered_cards": [1, 2],
  "partial_cards": [{{"card": 3, "missing": "缺少的原因、结果、动作或关键细节"}}],
  "missing_cards": [{{"card": 4, "missing": "整项事件未出现"}}],
  "factual_grounding_passed": true,
  "unsupported_claims": [],
  "emotional_quality_passed": true,
  "emotional_issues": [],
  "attraction_score": 85,
  "attraction_issues": ["前段过早说完全部答案"],
  "timeline_order_passed": true,
  "out_of_order_cards": [],
  "summary": "一句话总结"
}}

<fact_brief>{json.dumps(audit_input, ensure_ascii=False)}</fact_brief>
<protected_opening>{protected_opening}</protected_opening>
<rewritten_script>{rewritten_script}</rewritten_script>
"""
    payload = {
        "model": minimax_model(),
        "messages": [
            {"role": "system", "content": "你检查核心事实覆盖、反向事实依据、事件顺序和短视频吸引力，只输出可解析 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "top_p": 0.8,
        "max_tokens": max(1200, min(3500, len(cards) * 180)),
        "stream": False,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{minimax_endpoint().rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    timeout = max(30, min(180, int(os.getenv("MINIMAX_COVERAGE_TIMEOUT_SECONDS", "90"))))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"MiniMax coverage audit API {exc.code}: {error_body}") from exc
    content = body["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    audit = json.loads(extract_json(str(content)))
    if not isinstance(audit, dict):
        raise ValueError("Rewrite fact coverage audit must return a JSON object")
    return normalize_rewrite_fact_coverage(audit, fact_brief)


def rewrite_script_with_minimax(
    raw_script: str,
    style: str,
    api_key: str,
    preserve_rule: str = "auto",
    append_book_promotion: bool = False,
    promotion_book_title: str = "国之脊梁",
) -> dict:
    raw_len = content_length(raw_script)
    try:
        fact_brief = request_minimax_rewrite_analysis(raw_script, api_key, preserve_rule)
    except RewriteGenerationError:
        raise
    except Exception as exc:
        raise RewriteGenerationError("source analysis", exc) from exc
    best_result: dict | None = None
    last_failed_result: dict | None = None
    generation_errors: list[str] = []
    successful_candidates = 0
    narrative_strategies = rewrite_narrative_strategies(fact_brief)
    expression_profiles = RANDOM.sample(rewrite_expression_profiles(), MAX_REWRITE_ATTEMPTS)

    def candidate_rank(candidate: dict) -> tuple:
        metrics = candidate.get("rewrite_comparison") or {}
        return (
            int(bool(metrics.get("passed"))),
            int(metrics.get("fact_coverage_passed") is not False),
            int(metrics.get("timeline_order_passed") is not False),
            int(metrics.get("emotional_quality_passed") is not False),
            int(metrics.get("attraction_score") or 0),
            int(metrics.get("narrative_difference") or metrics.get("overall_difference") or 0),
            int(metrics.get("overall_difference") or 0),
            -int(metrics.get("continuous_reuse") or 0),
            -int(metrics.get("source_phrase_reuse") or 0),
        )

    for attempt in range(1, MAX_REWRITE_ATTEMPTS + 1):
        prompt = build_rewrite_prompt(
            raw_script, style, attempt, last_failed_result, preserve_rule, fact_brief,
            append_book_promotion, promotion_book_title,
            expression_profile=expression_profiles[attempt - 1],
        )
        try:
            result = request_minimax_rewrite(
                prompt, raw_script, style, api_key, raw_len, preserve_rule,
                append_book_promotion, promotion_book_title,
                fact_brief.get("verified_quotes") or [],
            )
        except Exception as exc:
            generation_errors.append(f"候选稿 {attempt}：{type(exc).__name__}: {str(exc)[:180]}")
            continue
        successful_candidates += 1
        result["rewrite_narrative_strategy"] = narrative_strategies[attempt - 1]
        result["rewrite_fact_brief"] = fact_brief
        result["rewrite_analysis_warning"] = fact_brief.get("analysis_warning", "")
        try:
            coverage = request_minimax_rewrite_fact_coverage(
                fact_brief,
                str(result.get("rewritten_script") or ""),
                api_key,
                extract_opening_hook(raw_script, preserve_rule),
                (
                    load_book_promotion_guidelines(promotion_book_title)
                    or fallback_book_promotion(promotion_book_title)
                ) if append_book_promotion else "",
            )
        except Exception as exc:
            result["rewrite_audit_warning"] = (
                f"事实审稿暂不可用，已保留本地篇幅与重复率检查：{type(exc).__name__}: {str(exc)[:180]}"
            )
            coverage = {
                "fact_coverage_passed": True,
                "factual_grounding_passed": True,
                "unsupported_claims": [],
                "timeline_order_passed": True,
                "timeline_order_issues": [],
                "attraction_score": 0,
                "attraction_score_available": False,
                "attraction_quality_passed": True,
                "attraction_issues": [],
                "audit_status": "unavailable",
                "covered_fact_cards": [],
                "expected_fact_cards": 0,
                "missing_fact_cards": [],
                "fact_coverage_summary": "事实审稿暂不可用，未作为质量失败处理",
            }
        apply_rewrite_fact_coverage_quality(result, coverage)
        comparison = result.get("rewrite_comparison") or {}
        if comparison.get("compression_warning"):
            result["rewrite_compression_warning"] = (
                f"成稿约为原文的 {comparison.get('length_ratio', 0)}%，低于 "
                f"最低 {REWRITE_COMPRESSION_WARNING_RATIO}% 的篇幅要求；"
                "本候选未通过篇幅验收，系统会继续比较另一篇候选。"
            )
            result["rewrite_warning"] = result["rewrite_compression_warning"]
            result["rewrite_quality_status"] = "compression_warning"
        if not best_result or candidate_rank(result) > candidate_rank(best_result):
            best_result = result
        last_failed_result = None if comparison.get("passed", False) else result

    if best_result is None:
        detail = "；".join(generation_errors) or "没有候选稿成功返回"
        raise RewriteGenerationError("draft generation candidates", RuntimeError(detail))
    comparison = best_result.get("rewrite_comparison") or {}
    best_result["rewrite_attempts"] = MAX_REWRITE_ATTEMPTS
    best_result["rewrite_candidates_generated"] = successful_candidates
    if comparison.get("passed", False):
        if generation_errors:
            best_result["rewrite_candidate_warning"] = "；".join(generation_errors)
        return best_result
    # A quality threshold miss should not discard a complete draft. Return the
    # best attempt with actionable metrics; reserve request failures for cases
    # where no draft could be generated at all.
    best_result["rewrite_warning"] = build_rewrite_quality_warning(comparison)
    if generation_errors:
        best_result["rewrite_warning"] += " 候选生成异常：" + "；".join(generation_errors)
    best_result["rewrite_quality_status"] = "quality_warning"
    best_result["rewrite_error"] = ""
    return best_result


def request_minimax_rewrite(
    prompt: str,
    raw_script: str,
    style: str,
    api_key: str,
    raw_len: int,
    preserve_rule: str = "auto",
    append_book_promotion: bool = False,
    promotion_book_title: str = "国之脊梁",
    verified_quotes: list[str] | None = None,
) -> dict:
    payload = {
        "model": minimax_model(),
        "messages": [
            {"role": "system", "content": "你是短视频口播文案编剧。严格执行用户提供的事实资料卡、固定开头和创作规则，只输出可解析 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.8,
        "top_p": 0.92,
        "max_tokens": max(4096, min(12000, raw_len * 4)),
        "stream": False,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{minimax_endpoint().rstrip('/')}/chat/completions",
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
        raise RuntimeError(f"MiniMax API {exc.code}: {error_body}") from exc

    content = body["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    result = json.loads(extract_json(str(content)))
    result["rewrite_provider"] = minimax_model()
    return normalize_rewrite_result(
        result, raw_script, style, preserve_rule, append_book_promotion, promotion_book_title,
        verified_quotes,
    )


def rewrite_script(
    raw_script: str,
    style: str = "纪实故事型",
    preserve_rule: str = "auto",
    append_book_promotion: bool = False,
    promotion_book_title: str = "国之脊梁",
) -> dict:
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        fallback = fallback_rewrite_script(
            raw_script, style, preserve_rule, append_book_promotion, promotion_book_title
        )
        return ensure_min_rewrite_difference(fallback)
    try:
        return rewrite_script_with_minimax(
            raw_script, style, api_key, preserve_rule, append_book_promotion, promotion_book_title
        )
    except (RewriteQualityError, RewriteGenerationError):
        raise
    except Exception as exc:
        raise RewriteGenerationError("rewrite pipeline", exc) from exc


def extract_json(text: str) -> str:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("Model response does not contain JSON")
    return match.group(0)


def keywords_from_text(text: str) -> dict[str, list[str]]:
    # Storyboard subjects and scenes must come from AI visual analysis, never
    # from hard-coded people, scenes, eras, or narration-fragment fallbacks.
    return {
        "people": [],
        "scene": [],
        "era": [],
        "keywords": [],
    }


def is_meaningful_shot_text(text: str) -> bool:
    cleaned = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
    return bool(cleaned)


SHOT_VISUALS_BATCH_SIZE = 9
LOGGER = logging.getLogger(__name__)

STORYBOARD_STYLE_GUIDANCE = STORYBOARD_STYLE_PROMPT


NESTED_STORYBOARD_MARKER = re.compile(
    r"(?:【\s*)?分镜\s*\d+\s*[-—–_]\s*\d+(?:\s*】)?",
    flags=re.IGNORECASE,
)
STORYBOARD_LABEL_MARKER = re.compile(
    r"(?:【\s*)?(?:分镜|镜头)\s*(?:\d+|[一二三四五六七八九十]+)(?:\s*】)?",
    flags=re.IGNORECASE,
)


def _contains_nested_storyboards(prompt: str) -> bool:
    """Return whether a supposed single-image prompt contains sub-storyboards."""
    text = str(prompt or "")
    return bool(NESTED_STORYBOARD_MARKER.search(text)) or len(
        STORYBOARD_LABEL_MARKER.findall(text)
    ) > 1


def _build_storyboard_plan_prompt(full_script: str) -> str:
    return f"""请像专业分镜导演一样处理下面的完整文案：先根据内容、转折和重点把文案划分成6至9个分镜，再为每个分镜选择一个能描述该段内容或最重要、最有画面感的瞬间，并写成可直接用于AI绘画的中文图片提示词。

统一画面风格如下，由程序在出图时自动添加。你需要据此规划兼容的具体画面，但不要在每条图片提示词里重复这段风格词：
{STORYBOARD_STYLE_GUIDANCE}

所有分镜画面中都禁止出现任何可读文字，包括标题、字幕、书名、牌匾文字、奏章文字、纸张文字、屏幕文字、印章文字、标语、logo和水印。需要出现书籍、匾额、信件、奏章或屏幕时，只表现无字外观，不要描述任何文字内容。

具体图片提示词只描述人物、场景、动作、表情、时代物件和构图，不要另加画风、媒介、色调或光影风格。尤其禁止写入“写实油画风”“写实历史画风”“冷灰色调”“电影感”“强烈光影”“高对比度”“真实摄影质感”等与统一画风冲突的描述。

每个分镜的图片提示词只能描述一个明确瞬间和一个构图，禁止在一条提示词中继续拆分子分镜，禁止输出“分镜6-1”“镜头一/镜头二”或多幅画面方案。

完整文案：
{full_script}

请先在内部完成分镜规划，然后按自然语言逐镜输出。不要返回JSON。每个分镜使用阿拉伯数字，并包含以下内容：

分镜1：简短概括
文案对应：简要说明这一镜覆盖的内容
结束原句：
<结束句>逐字复制这一镜在原文中的最后一个完整句子</结束句>
画面描述：说明为什么选择这个重要画面
图片提示词：
<提示词>只写具体人物、场景、动作、表情和构图，不要复述统一风格词</提示词>

结束原句必须来自原文、保持顺序且不能重复；最后一个分镜的结束原句必须是全文最后一句。程序会根据这些结束原句从原文无损切分旁白。不要在分镜之外添加另一版提示词。""".strip()


def _storyboard_number(value: str) -> int | None:
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    chinese_numbers = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9,
    }
    return chinese_numbers.get(text)


def _parse_storyboard_plan(content: str) -> list[dict[str, str]]:
    raw = str(content or "").strip().replace("**", "")
    raw = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    marker = r"(?:【)?分镜\s*([1-9]\d*|[一二三四五六七八九])(?:】)?[^\n]*"
    blocks = re.compile(
        rf"(?ms)^\s*(?:#{{1,6}}\s*)?{marker}\s*\n(.*?)"
        rf"(?=^\s*(?:#{{1,6}}\s*)?{marker}\s*\n|\Z)"
    )
    parsed: list[dict[str, str]] = []
    for match in blocks.finditer(raw):
        shot_index = _storyboard_number(match.group(1))
        block = match.group(2).strip()
        narration_match = re.search(r"(?ms)<旁白>\s*(.*?)\s*</旁白>", block)
        end_quote_match = re.search(r"(?ms)<结束句>\s*(.*?)\s*</结束句>", block)
        prompt_match = re.search(r"(?ms)<提示词>\s*(.*?)\s*</提示词>", block)
        if not narration_match:
            narration_match = re.search(
                r"(?ms)^\s*文案对应\s*[：:]\s*(.*?)"
                r"(?=^\s*(?:画面描述|图片提示词|提示词)\s*[：:]|\Z)",
                block,
            )
        if not prompt_match:
            prompt_match = re.search(
                r"(?ms)^\s*(?:图片提示词|提示词)\s*[：:]\s*(.*?)\Z",
                block,
            )
        narration = str(narration_match.group(1) if narration_match else "").strip()
        narration = narration.strip("“”\"'")
        end_quote = str(end_quote_match.group(1) if end_quote_match else "").strip()
        end_quote = end_quote.strip("“”\"'")
        image_prompt = sanitize_storyboard_visual_prompt(re.sub(
            r"\s+", " ", str(prompt_match.group(1) if prompt_match else "")
        ))
        if _contains_nested_storyboards(image_prompt):
            image_prompt = ""
        if shot_index and (narration or end_quote):
            parsed.append({
                "shot_index": shot_index,
                "voice_text": narration,
                "end_quote": end_quote,
                "visual_need": image_prompt,
            })
    return sorted(parsed, key=lambda item: item["shot_index"])


def _materialize_storyboard_narration(
    full_script: str,
    plan: list[dict[str, str]],
) -> list[dict[str, str]]:
    if plan and all(item.get("voice_text") for item in plan):
        return plan
    cursor = 0
    materialized: list[dict[str, str]] = []
    for plan_index, item in enumerate(plan):
        end_quote = str(item.get("end_quote") or "").strip()
        if not end_quote:
            return []
        if plan_index == len(plan) - 1:
            quote_end = len(full_script)
        else:
            quote_start = full_script.find(end_quote, cursor)
            if quote_start >= 0:
                quote_end = quote_start + len(end_quote)
            else:
                searchable = full_script[cursor:]
                normalized_chars: list[str] = []
                original_positions: list[int] = []
                for position, char in enumerate(searchable, cursor):
                    if char.isalnum():
                        normalized_chars.append(char)
                        original_positions.append(position)
                normalized_quote = "".join(char for char in end_quote if char.isalnum())
                normalized_source = "".join(normalized_chars)
                normalized_start = normalized_source.find(normalized_quote)
                if normalized_start >= 0 and normalized_quote:
                    quote_end = original_positions[normalized_start + len(normalized_quote) - 1] + 1
                else:
                    sentence_candidates = list(re.finditer(r"[^。！？!?]+[。！？!?]?", searchable))
                    scored_candidates = [
                        (
                            SequenceMatcher(
                                None,
                                normalized_quote,
                                "".join(char for char in candidate.group(0) if char.isalnum()),
                            ).ratio(),
                            candidate,
                        )
                        for candidate in sentence_candidates
                        if any(char.isalnum() for char in candidate.group(0))
                    ]
                    if not scored_candidates:
                        return []
                    score, candidate = max(scored_candidates, key=lambda value: value[0])
                    if score < 0.5:
                        return []
                    quote_end = cursor + candidate.end()
        while (
            quote_end < len(full_script)
            and full_script[quote_end] in "，。！？；：…—,.!?;:、”’\"'）)]】"
        ):
            quote_end += 1
        narration = full_script[cursor:quote_end].strip()
        if not narration:
            return []
        materialized.append({**item, "voice_text": narration})
        cursor = quote_end
    if any(char.isalnum() for char in full_script[cursor:]):
        return []
    return materialized


def ai_generate_storyboard_plan(
    full_script: str,
    model_provider: str = "deepseek",
) -> list[dict[str, str]]:
    """Let the selected model decide shot boundaries and concrete image prompts."""
    provider = normalize_storyboard_model_provider(model_provider)
    api_key, endpoint, model, provider_label = _storyboard_model_config(provider)
    if not api_key:
        variable = {
            "openai": "OPENAI_API_KEY",
            "minimax": "MINIMAX_API_KEY",
        }.get(provider, "DEEPSEEK_API_KEY")
        raise RuntimeError(f"{variable} is required to generate storyboard prompts")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是专业分镜导演，负责划分文案并确定每镜最重要的具体画面。"},
            {"role": "user", "content": _build_storyboard_plan_prompt(full_script)},
        ],
        "stream": False,
    }
    if provider == "openai":
        payload.update({
            "max_completion_tokens": 5000,
            "reasoning_effort": "medium",
        })
    else:
        payload.update({
            "temperature": 0.4,
            "top_p": 0.85,
            "max_tokens": 5000,
            "thinking": {"type": "disabled"},
        })
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(2):
        request = urllib.request.Request(
            f"{endpoint.rstrip('/')}/chat/completions",
            data=data,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            parsed_plan = _parse_storyboard_plan(str(content))
            plan = _materialize_storyboard_narration(full_script, parsed_plan)
            if not plan and 6 <= len(parsed_plan) <= 9:
                fallback_chunks = split_script_into_storyboards(
                    full_script,
                    target_count=len(parsed_plan),
                )
                if len(fallback_chunks) == len(parsed_plan):
                    plan = [
                        {**item, "voice_text": fallback_chunks[index]}
                        for index, item in enumerate(parsed_plan)
                    ]
            source_text = re.sub(r"\s+", "", full_script)
            planned_text = re.sub(r"\s+", "", "".join(item["voice_text"] for item in plan))
            indexes = [item["shot_index"] for item in plan]
            if (
                6 <= len(plan) <= 9
                and indexes == list(range(1, len(plan) + 1))
                and planned_text == source_text
            ):
                return plan
            last_error = RuntimeError(
                f"{provider_label} storyboard plan contained {len(plan)} readable shots or did not preserve the full script"
            )
        except Exception as exc:
            last_error = exc
        if attempt == 0:
            time.sleep(1.5)
    raise RuntimeError(f"{provider_label} storyboard planning failed: {last_error}")


def _build_shot_visuals_prompt(shot_items: list[dict], full_script: str) -> str:
    return f"""请根据完整文案和已经拆分好的旁白，为每个分镜生成一条可直接用于 AI 绘画的中文图片提示词。画面内容由你结合文案自行确定，并注意前后镜头的连续性。

完整文案：
{full_script}

分镜旁白：
{json.dumps(shot_items, ensure_ascii=False)}

每条图片提示词必须只描绘同一编号“voice_text”中出现的人物、事件或核心意象，不得挪用前一镜或后一镜的内容。先逐条核对编号与旁白，再输出对应提示词。

所有画面都禁止出现任何可读文字。书籍、匾额、信件、奏章、纸张和屏幕只能呈现无字外观，不要在图片提示词中设计标题、字幕、书名、标语、logo或水印。

统一画面风格由程序自动添加：{STORYBOARD_STYLE_GUIDANCE}。每条提示词只写具体人物、场景、动作、表情、时代物件和构图，不要重复或另加画风、媒介、色调、光影风格。尤其禁止写入“写实油画风”“写实历史画风”“冷灰色调”“电影感”“强烈光影”“高对比度”“真实摄影质感”等冲突描述。

每条图片提示词只能描述一个明确瞬间和一个构图，禁止继续拆分子分镜，禁止输出“分镜6-1”“镜头一/镜头二”或多幅画面方案。

按分镜编号顺序逐条写出图片提示词，能清楚区分每个分镜即可。直接输出结果，不要返回 JSON，也不要解释创作过程。""".strip()


def _parse_storyboard_prompt_lines(
    content: str,
    expected_ids: list[str] | None = None,
) -> dict[str, str]:
    """Extract prompts from ordinary numbered model output without requiring JSON."""
    parsed: dict[str, str] = {}
    raw = str(content or "").strip()
    raw = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    raw = raw.replace("**", "")
    pattern = re.compile(
        r"(?ms)^\s*(?:分镜\s*)?(\d+)\s*(?:\|\|\||[：:、.．）)]|\r?\n)\s*"
        r"(.+?)(?=^\s*(?:分镜\s*)?\d+\s*(?:\|\|\||[：:、.．）)]|\r?\n)|\Z)"
    )
    for match in pattern.finditer(raw):
        shot_id = match.group(1)
        image_prompt = re.sub(
            r"^\s*(?:图片)?提示词\s*[：:]\s*",
            "",
            match.group(2),
        )
        image_prompt = sanitize_storyboard_visual_prompt(image_prompt)
        if image_prompt and not _contains_nested_storyboards(image_prompt):
            parsed[shot_id] = image_prompt
    if parsed:
        ids = [str(item) for item in (expected_ids or [])]
        if len(ids) == 1 and ids[0] not in parsed and len(parsed) == 1:
            return {ids[0]: next(iter(parsed.values()))}
        return parsed

    ids = [str(item) for item in (expected_ids or [])]
    if len(ids) == 1 and raw:
        prompt = re.sub(r"^\s*(?:图片)?提示词\s*[：:]\s*", "", raw)
        prompt = sanitize_storyboard_visual_prompt(prompt)
        if prompt and not _contains_nested_storyboards(prompt):
            return {ids[0]: prompt}
        return {}

    blocks = [
        re.sub(r"^\s*[-•]\s*", "", item).strip()
        for item in re.split(r"\n\s*\n|\r?\n", raw)
        if item.strip()
    ]
    if ids and len(blocks) == len(ids):
        return {
            shot_id: sanitize_storyboard_visual_prompt(prompt)
            for shot_id, prompt in zip(ids, blocks)
            if not _contains_nested_storyboards(prompt)
        }
    return parsed


def ai_generate_shot_visuals(
    shots: list[dict],
    full_script: str,
    model_provider: str = "deepseek",
    _semantic_retries: int = 1,
) -> dict[str, dict]:
    """Ask the selected model for one image prompt per storyboard shot."""
    provider = normalize_storyboard_model_provider(model_provider)
    api_key, endpoint, model, provider_label = _storyboard_model_config(provider)
    if not api_key:
        variable = {
            "openai": "OPENAI_API_KEY",
            "minimax": "MINIMAX_API_KEY",
        }.get(provider, "DEEPSEEK_API_KEY")
        raise RuntimeError(f"{variable} is required to generate storyboard prompts")

    all_visuals: dict[str, dict] = {}
    for batch_start in range(0, len(shots), SHOT_VISUALS_BATCH_SIZE):
        batch = shots[batch_start:batch_start + SHOT_VISUALS_BATCH_SIZE]
        shot_items = [
            {"id": str(shot["shot_index"]), "voice_text": shot["voice_text"]}
            for shot in batch
        ]
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是分镜导演。直接逐条给出每个分镜的图片提示词。"},
                {"role": "user", "content": _build_shot_visuals_prompt(shot_items, full_script)},
            ],
            "stream": False,
        }
        if provider == "openai":
            payload.update({
                "max_completion_tokens": max(2000, min(8000, len(batch) * 300)),
                "reasoning_effort": "medium",
            })
        else:
            payload.update({
                "temperature": 0.5,
                "top_p": 0.9,
                "max_tokens": max(2000, min(8000, len(batch) * 300)),
                "thinking": {"type": "disabled"},
            })
        data = json.dumps(payload).encode("utf-8")
        body = None
        last_error: Exception | None = None
        for attempt in range(3):
            req = urllib.request.Request(
                f"{endpoint.rstrip('/')}/chat/completions",
                data=data,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=90) as response:
                    body = json.loads(response.read().decode("utf-8"))
                break
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        if body is None:
            raise RuntimeError(f"{provider_label} storyboard request failed: {last_error}")

        try:
            content = body["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            parsed_prompts = _parse_storyboard_prompt_lines(
                str(content),
                [item["id"] for item in shot_items],
            )
            for shot_id, image_prompt in parsed_prompts.items():
                if shot_id and image_prompt:
                    all_visuals[shot_id] = {
                        "visual_need": image_prompt,
                        "object_tags": [],
                        "scene_tags": [],
                        "keywords": [],
                    }
        except Exception as exc:
            raise RuntimeError(f"{provider_label} storyboard response could not be read: {exc}") from exc

        missing_shots = [
            shot for shot in batch
            if str(shot["shot_index"]) not in all_visuals
        ]
        if missing_shots and len(batch) > 1:
            LOGGER.warning(
                "%s omitted storyboard prompts for shots %s; requesting them individually",
                provider_label,
                [shot["shot_index"] for shot in missing_shots],
            )
            for missing_shot in missing_shots:
                all_visuals.update(ai_generate_shot_visuals(
                    [missing_shot],
                    full_script,
                    model_provider=provider,
                ))
        elif missing_shots:
            if _semantic_retries > 0:
                LOGGER.warning(
                    "%s returned an invalid prompt for shot %s; requesting it again",
                    provider_label,
                    missing_shots[0]["shot_index"],
                )
                all_visuals.update(ai_generate_shot_visuals(
                    missing_shots,
                    full_script,
                    model_provider=provider,
                    _semantic_retries=_semantic_retries - 1,
                ))
                continue
            raise RuntimeError(
                f"{provider_label} returned an empty prompt for shot {missing_shots[0]['shot_index']}"
            )

    return all_visuals


def strip_title_punctuation(text: str) -> str:
    punctuation = re.compile(r"[，。！？、；：“”‘’《》【】（）—…\-.!?,;:'\"()\[\]{}<>]")
    return re.sub(r"\s+", "", punctuation.sub("", str(text or ""))).strip()


def title_evidence_is_valid(line1: str, line2: str, evidence_quote: str, script: str) -> bool:
    evidence = compact_similarity_text(evidence_quote)
    source = compact_similarity_text(script)
    if not 6 <= len(evidence) <= 80 or evidence not in source:
        return False
    evidence_bigrams = phrase_shingles(evidence, size=2)
    line1_supported = bool(phrase_shingles(line1, size=2) & evidence_bigrams)
    line2_is_question = any(word in line2 for word in ("为何", "为什么", "到底", "凭什么", "谁", "真相"))
    line2_supported = line2_is_question or bool(phrase_shingles(line2, size=2) & evidence_bigrams)
    return line1_supported and line2_supported


def cover_title_rejection_reasons(
    line1: str,
    line2: str,
    script: str = "",
    evidence_quote: str | None = None,
) -> list[str]:
    combined = f"{line1}{line2}"
    reasons: list[str] = []
    evidence_valid = (
        title_evidence_is_valid(line1, line2, evidence_quote, script)
        if evidence_quote is not None else False
    )
    if not combined:
        return ["标题为空"]
    if TITLE_VAGUE_PRONOUN_DIALOGUE_PATTERN.search(line1) or TITLE_VAGUE_PRONOUN_DIALOGUE_PATTERN.search(line2):
        reasons.append("包含主体不明的代词对话")
    # “没先做某事”通常只是把一个正常选择硬包装成反常行为，而且隐藏了
    # 真正发生的动作。封面标题应直接写实际选择及其代价，而不是虚构预期。
    if any(pattern in combined for pattern in TITLE_FAKE_CONTRAST_PATTERNS):
        reasons.append("使用没先等词制造虚假反差")
    # 时间顺序、独自行动等修饰语会实质改变事实；原文没有时不能为制造冲突添加。
    if script:
        if any(modifier in combined and modifier not in script for modifier in TITLE_FACT_SENSITIVE_MODIFIERS):
            reasons.append("加入了原文没有的时间或行动修饰语")
        if TITLE_SEQUENCE_ACTION_PATTERN.search(combined) and "先" not in script:
            reasons.append("加入了原文没有的先后顺序")
    if any(pattern in combined for pattern in WEAK_COVER_TITLE_PATTERNS):
        reasons.append("包含空洞总结或泛情绪反应")
    if any(left in combined and right in combined for left, right in COVER_TITLE_SPOILER_COMBOS):
        reasons.append("标题直接说完了爆点")
    if any(ending in line2 for ending in TITLE_SUMMARY_ENDINGS):
        reasons.append("第二行是总结式收束")
    if len(combined) >= 8 and not any(word in combined for word in TITLE_OPEN_LOOP_WORDS) and not evidence_valid:
        reasons.append("没有形成有效悬念或认知落差")
    if evidence_quote is not None and not evidence_valid:
        reasons.append("原文依据无效或不能支持标题核心内容")
    return list(dict.fromkeys(reasons))


def cover_title_needs_rewrite(line1: str, line2: str, script: str = "") -> bool:
    return bool(cover_title_rejection_reasons(line1, line2, script))


def _title_candidates_from_json(value: object) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []

    if any(key in value for key in ("first_line", "second_line", "line1", "line2")):
        return [value]

    for key in ("candidates", "titles", "items", "data", "result", "output"):
        nested = value.get(key)
        candidates = _title_candidates_from_json(nested)
        if candidates:
            return candidates
        if isinstance(nested, str):
            try:
                candidates = parse_title_candidates(nested)
            except ValueError:
                continue
            if candidates:
                return candidates
    return []


def parse_title_candidates(content: str) -> list[dict]:
    """Read title candidates from strict JSON or a JSON fragment in model output."""
    text = str(content or "").strip().lstrip("\ufeff")
    if not text:
        raise ValueError("MiniMax returned empty title content")

    decoder = json.JSONDecoder()
    parsed_values: list[object] = []

    try:
        parsed_values.append(json.loads(text))
    except json.JSONDecodeError:
        pass

    fenced_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.I)
    for block in fenced_blocks:
        try:
            parsed_values.append(json.loads(block.strip()))
        except json.JSONDecodeError:
            pass

    for index, character in enumerate(text):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        parsed_values.append(value)

    for value in parsed_values:
        candidates = _title_candidates_from_json(value)
        if candidates:
            return candidates
    raise ValueError("MiniMax response does not contain usable title candidates")


def generate_viral_title(script: str) -> dict:
    """Generate all two-line cover title candidates for the user to choose."""
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        return {"candidates": [], "error": "MINIMAX_API_KEY is not configured"}

    base_prompt = (
        "你是擅长提高点击率和停留率的短视频爆款标题策划。"
        "请通读整篇文案，直接为“标题封面与配乐”页面生成有冲击力的两行式封面标题。"
        "\n\n【唯一创作依据】"
        "\n只根据这篇文案判断什么最吸引人。先找出人物最特殊的身份、最强冲突、最大反差、关键数字、反常选择、沉重代价、意外结果和最有情绪张力的瞬间，再选择最能让普通观众立刻想点开的角度。"
        "\n不要为了稳妥写成人物简介、事迹概括或正确但平淡的总结。每个候选都必须有明确爆点，让观众产生“为什么”“后来怎样”“他到底做了什么”的观看欲望。"
        "\n标题可以制造悬念、冲突、反差、心疼、愤怒、震惊、爽感或认知颠覆，也可以直接抛出文案里最不可思议的事实。"
        "\n\n【不设词库限制】"
        "\n不使用任何标题词库、禁词表、优先词表或固定模板来限制表达。任何词、语气和句式都可以使用，只看它是否适合当前文案、是否足够吸引人。"
        "\n不要因为某个词常见就排除它，也不要为了命中所谓爆款词而硬塞词。允许大胆、口语化、有情绪、有悬念的表达。"
        "\n\n【事实底线】"
        "\n可以强化文案中真实存在的冲突和情绪，但不能编造原文没有的人物、数字、动作、关系、先后顺序、因果、台词或结局。"
        "\n两行合起来要让人能看懂，主体可以根据语境省略，但不能造成事实指向错误。"
        "\n\n【候选要求】"
        "\n一次生成12组不同角度、不同句式的候选，不要把同一个标题只换几个词重复输出。"
        "\n两行都完整输出，不设字数上限，不截断句意。"
        "\n每组包含 first_line、second_line、style、evidence_quote。style 可自由概括该标题的吸引点，不限制类别。"
        "\nevidence_quote 从原文逐字复制6到100字，用来证明标题核心事实确实来自文案。"
        "\n\n【输出前自检】"
        "\n逐个问自己：这个标题是否比普通人物介绍更想让人点开？是否一眼就有冲突、疑问、反差、情绪或惊人事实？如果只是正确但平淡，必须重写得更有爆点。"
        "\n只返回JSON数组，不要Markdown或解释。"
        '\n返回格式：[{"first_line":"第一行","second_line":"第二行","style":"吸引点说明","evidence_quote":"原文直接依据"}]'
        "\n\n下面是文案内容："
        f"\n{script[:6000]}"
    )

    last_error = ""
    try:
        for attempt in range(1, 4):
            retry_note = f"\n\n上一版不合格，具体原因：{last_error}。请针对这些原因重新生成，不要重复上一版的问题。" if last_error else ""
            if attempt == 3:
                retry_note += (
                    "\n\n这是最后一次生成。请重新通读文案，抓住最强冲突、最意外的事实或最重的情绪代价。"
                    "不要保守，不要写平淡概括；在不编造事实的前提下，把每个候选都写到能激起点击欲望。"
                )
            payload = {
        "model": minimax_model(),
                "messages": [
                    {"role": "system", "content": "你只输出可解析JSON。"},
                    {"role": "user", "content": base_prompt + retry_note},
                ],
                "temperature": 0.95,
                "top_p": 0.95,
                "max_tokens": 2200,
                "stream": False,
                "thinking": {"type": "disabled"},
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
            f"{minimax_endpoint().rstrip('/')}/chat/completions",
                data=data,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
            try:
                candidates = parse_title_candidates(str(content))
            except ValueError as exc:
                last_error = str(exc)[:200]
                continue
            output_candidates = []
            seen_titles: set[tuple[str, str]] = set()
            for item in candidates:
                line1 = str(item.get("first_line") or item.get("line1") or "").strip()
                line2 = str(item.get("second_line") or item.get("line2") or "").strip()
                evidence_quote = str(item.get("evidence_quote") or "").strip()
                title_key = (line1, line2)
                if not line1 or not line2 or title_key in seen_titles:
                    continue
                seen_titles.add(title_key)
                output_candidates.append({
                    "line1": line1,
                    "line2": line2,
                    "full_title": f"{line1} {line2}",
                    "style": str(item.get("style") or "").strip(),
                    "evidence_quote": evidence_quote,
                })
            if output_candidates:
                return {"candidates": output_candidates}
            last_error = "AI 没有返回包含完整两行内容的标题候选"
        return {"candidates": [], "error": last_error or "Title generation failed"}
    except Exception as exc:
        return {"candidates": [], "error": str(exc)[:200]}


def clean_publish_description(text: str, limit: int = 140) -> str:
    description = re.sub(r"\s+", " ", str(text or "")).strip()
    description = re.sub(r"《[^》]{1,30}》", "", description)
    description = re.sub(
        r"(这本书|那本书|本书|书里|书中|翻开|读完|买给孩子|下单|购买|小黄车|带书|卖书|推荐给家长)",
        "",
        description,
    )
    description = re.sub(r"\s+", " ", description).strip(" ，。！？、；： ")
    return description


def append_publish_hashtags(description: str, tags: object = None) -> str:
    """Append exactly four unique hashtags to a publish description."""
    description_tags = re.findall(r"#([^#\s，。！？、；：,.!?;:]+)", str(description or ""))
    body = re.sub(r"#([^#\s，。！？、；：,.!?;:]+)", "", str(description or ""))
    body = clean_publish_description(body)

    candidates: list[str] = []
    raw_tags = tags if isinstance(tags, list) else []
    for raw_tag in [*raw_tags, *description_tags, "人物故事", "历史故事", "家国情怀", "值得铭记"]:
        tag = re.sub(r"^[#＃]+", "", str(raw_tag or "").strip())
        tag = re.sub(r"[\s#＃，。！？、；：,.!?;:]+", "", tag)
        if tag and tag not in candidates:
            candidates.append(tag)
        if len(candidates) == 4:
            break

    hashtag_line = " ".join(f"#{tag}" for tag in candidates)
    return f"{body}\n{hashtag_line}" if body else hashtag_line


def fallback_publish_assistant(script: str) -> dict:
    sentences = split_sentences(script)
    first = sentences[0] if sentences else script[:40]
    short_title = (
        strip_title_punctuation(first)
        or "这个故事值得被看见"
    )
    description_source = " ".join(sentences[:3]) if sentences else script
    description = append_publish_hashtags(description_source)
    return {"short_title": short_title, "description": description}


def generate_publish_assistant(script: str) -> dict:
    """Generate a platform-ready description with four hashtags and a short title."""
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        return fallback_publish_assistant(script)

    prompt = (
        "你是短视频发布运营助手。请根据下面的中文口播文案，生成发布用内容。"
        "\n\n要求："
        "\n1. short_title 是一句语义完整的短标题，建议 8 到 24 个汉字，不要任何标点符号。"
        "\n2. short_title 必须表达完整，不能为了控制字数截断词语、人物、事件或句意。"
        "\n3. short_title 要有悬念或反差，但必须忠于文案事实，不要标题党造假。"
        "\n4. description 是视频描述正文，80 到 140 个汉字，适合发视频号/抖音/小红书；正文中不要写标签。"
        "\n5. description 开头要吸引人，点出故事冲突、反差、情绪爆点或评论点，让人想点开看完。"
        "\n6. description 只写视频内容本身，不要介绍书，不要提书名，不要写读书感受，不要出现买书、带书、小黄车、家长购买等表达。"
        "\n7. description 不要写成片头文案，不要写“本视频讲述”，不要堆砌空话。"
        "\n8. tags 是根据视频主题提炼的 4 个适合发布平台的中文标签；每项不带 #，不得重复。"
        "\n9. 只返回 JSON，不要 Markdown，不要解释。"
        "\n\n文案内容："
        f"\n{script[:1200]}"
        "\n\n返回格式："
        '\n{"short_title": "一句话短标题", "description": "吸引人的视频描述", "tags": ["标签一", "标签二", "标签三", "标签四"]}'
    )

    payload = {
        "model": minimax_model(),
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
            f"{minimax_endpoint().rstrip('/')}/chat/completions",
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
        short_title = strip_title_punctuation(result.get("short_title", ""))
        raw_description = clean_publish_description(result.get("description", ""))
        if not short_title or not raw_description:
            return fallback_publish_assistant(script)
        description = append_publish_hashtags(raw_description, result.get("tags"))
        return {"short_title": short_title, "description": description}
    except Exception as exc:
        return {"short_title": "", "description": "", "error": str(exc)[:200]}


def _storyboard_target_count(script: str) -> int:
    length = content_length(script)
    if length < 12:
        return max(1, min(6, length))
    if length <= 360:
        return 6
    if length <= 540:
        return 7
    if length <= 720:
        return 8
    return 9


def _split_storyboard_unit(text: str) -> tuple[str, str] | None:
    text = str(text or "").strip()
    if content_length(text) < 4:
        return None
    midpoint = len(text) // 2
    boundaries = [match.end() for match in re.finditer(r"[，,、；;：:]|\s+", text)]
    boundaries = [position for position in boundaries if 2 <= position <= len(text) - 2]
    split_at = min(boundaries, key=lambda position: abs(position - midpoint)) if boundaries else midpoint
    left, right = text[:split_at].strip(), text[split_at:].strip()
    if not is_meaningful_shot_text(left) or not is_meaningful_shot_text(right):
        return None
    return left, right


def _group_storyboard_units(units: list[str], target: int) -> list[str]:
    groups: list[str] = []
    start = 0
    for group_index in range(target):
        groups_left = target - group_index
        if groups_left == 1:
            end = len(units)
        else:
            remaining = units[start:]
            desired = sum(max(1, content_length(item)) for item in remaining) / groups_left
            running = 0
            end = start
            max_end = len(units) - (groups_left - 1)
            while end < max_end:
                next_weight = max(1, content_length(units[end]))
                if end > start and abs(running - desired) <= abs(running + next_weight - desired):
                    break
                running += next_weight
                end += 1
            end = max(start + 1, end)
        groups.append("".join(units[start:end]).strip())
        start = end
    return [group for group in groups if is_meaningful_shot_text(group)]


def split_script_into_storyboards(
    script: str,
    target_count: int | None = None,
) -> list[str]:
    """Return 6-9 narration chunks while retaining order and all source text."""
    units: list[str] = []
    for line in script.splitlines() or [script]:
        line = line.strip()
        if not is_meaningful_shot_text(line):
            continue
        sentences = [item.strip() for item in split_sentences(line) if is_meaningful_shot_text(item)]
        units.extend(sentences or [line])

    if not units:
        return []
    target = (
        max(1, min(9, int(target_count)))
        if target_count is not None
        else _storyboard_target_count(script)
    )
    while len(units) < target:
        candidates = sorted(range(len(units)), key=lambda index: content_length(units[index]), reverse=True)
        split_result = None
        split_index = -1
        for index in candidates:
            split_result = _split_storyboard_unit(units[index])
            if split_result:
                split_index = index
                break
        if not split_result:
            break
        units[split_index:split_index + 1] = list(split_result)

    if len(units) <= target:
        return units
    return _group_storyboard_units(units, target)


def generate_shots(
    script: str,
    model_provider: str = "deepseek",
) -> list[dict]:
    provider = normalize_storyboard_model_provider(model_provider)
    try:
        storyboard_plan = ai_generate_storyboard_plan(script, model_provider=provider)
    except Exception as exc:
        LOGGER.warning(
            "%s one-pass storyboard planning failed; using narration-preserving fallback: %s",
            provider,
            exc,
        )
        chunks = split_script_into_storyboards(script)
        storyboard_plan = [
            {"shot_index": idx, "voice_text": text, "visual_need": ""}
            for idx, text in enumerate(chunks, 1)
        ]

    shots = []
    cursor = 0.0
    for planned_shot in storyboard_plan:
        idx = int(planned_shot["shot_index"])
        text = str(planned_shot["voice_text"])
        duration = max(3.0, min(6.0, round(len(text) / 7, 1)))
        shots.append({
            "shot_index": idx,
            "voice_text": text,
            "duration_sec": duration,
            "start_time": round(cursor, 2),
            "end_time": round(cursor + duration, 2),
            "visual_need": str(planned_shot.get("visual_need") or "").strip(),
            "required_object": [],
            "required_scene": [],
            "object_tags": [],
            "scene_tags": [],
            "keywords": [],
            "selected_asset_id": None,
            "asset_source": None,
            "match_score": 0,
            "status": "no_match",
        })
        cursor += duration

    # The planning response decides the narration boundaries. Generate each
    # visual in an isolated request containing only that shot's final narration.
    # Sending the whole script and all shot rows in one request allowed models
    # to shift a good visual into the following row and accumulate visible lag.
    visuals: dict[str, dict] = {}
    for shot in shots:
        shot_visuals = ai_generate_shot_visuals(
            [shot],
            str(shot["voice_text"]),
            model_provider=provider,
        )
        visuals.update(shot_visuals)
    for shot in shots:
        visual = visuals.get(str(shot["shot_index"]))
        if not visual or not visual.get("visual_need"):
            raise RuntimeError(f"{provider} did not return shot {shot['shot_index']}")
        shot["visual_need"] = visual["visual_need"]
        shot["object_tags"] = visual.get("object_tags") or []
        shot["required_object"] = shot["object_tags"]
        shot["scene_tags"] = visual.get("scene_tags") or []
        shot["required_scene"] = shot["scene_tags"]
        shot["keywords"] = visual.get("keywords") or []
        LOGGER.info(
            "storyboard visual aligned shot=%s voice=%r prompt=%r",
            shot["shot_index"],
            str(shot["voice_text"])[:100],
            str(shot["visual_need"])[:140],
        )

    return shots
