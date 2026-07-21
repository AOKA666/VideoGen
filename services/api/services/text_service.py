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

try:
    import jieba
except ImportError:  # Keyword overlap is diagnostic only; rewriting must still work without jieba.
    jieba = None

GUOZHIJILIANG_PEOPLE = [
    "李四光", "竺可桢", "茅以升", "叶企孙", "俞大绂", "林巧稚", "周培源", "裴文中",
    "王淦昌", "赵九章", "王应睐", "郭永怀", "汪猷", "华罗庚", "钱学森", "侯祥麟",
    "王承书", "钱三强", "罗沛霖", "何泽慧", "王大珩", "彭桓武", "卢嘉锡", "任新民",
    "叶笃正", "陈芳允", "吴征镒", "黄纬禄", "刘东生", "屠守锷", "吴自良", "林兰英",
    "程开甲", "吴文俊", "杨嘉墀", "黄昆", "谢家麟", "徐光宪", "师昌绪", "朱光亚",
]
PERSON_HINTS = GUOZHIJILIANG_PEOPLE + ["邓稼先", "于敏", "黄旭华", "袁隆平", "孙家栋", "屠呦呦", "彭士禄", "两弹一星"]
SCENE_HINTS = ["实验室", "会议", "档案", "照片", "火箭", "导弹", "核潜艇", "宿舍", "办公室", "手稿", "文件"]
ERA_HINTS = ["1950", "1960", "1970", "上世纪", "建国", "抗战"]
MIN_REWRITE_LENGTH_RATIO = 0.90
MAX_REWRITE_LENGTH_RATIO = 1.10
MIN_REWRITE_DIFFERENCE = 70
MAX_REWRITE_CONTINUOUS_REUSE = 12
MAX_REWRITE_SOURCE_PHRASE_REUSE = 20
MAX_REWRITE_SENTENCE_IMITATION = 35
MAX_REWRITE_ATTEMPTS = 3
MAX_REWRITE_ANALYSIS_ATTEMPTS = 2
MAX_REWRITE_ANALYSIS_REQUEST_ATTEMPTS = 2
REWRITE_ANALYSIS_MATERIAL_RATIO = 0.30
REWRITE_ANALYSIS_CHARS_PER_FACT_ITEM = 200
MAX_AUTO_TITLE_LENGTH = 8
MAX_PUBLISH_SHORT_TITLE_LENGTH = 16


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
        return content
    return ""
OPENING_HOOK_MIN_CHARS = 20
OPENING_HOOK_MAX_CHARS = 35
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
    "不敢抬头",
    "低下了头",
    "低下头",
    "红了眼眶",
    "红了眼",
    "流下眼泪",
    "泪流满面",
    "沉默不语",
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
GUOZHIJILIANG_STORY_SEEDS = [
    ("李四光", "从一块让少年困惑的大石头，写到他后来用地质力学为中国寻找石油"),
    ("竺可桢", "在战火和迁徙中坚持记录气象与物候，把科学判断看得比人情压力更重"),
    ("茅以升", "亲手建成钱塘江大桥，又在战火逼近时含泪参与炸桥"),
    ("叶企孙", "他培养出一批改变中国科学命运的学生，自己却长期站在光环背后"),
    ("俞大绂", "他放下书斋里的安稳，走进田间地头研究作物病害"),
    ("林巧稚", "她一生没有自己的孩子，却在产房里守护了无数新生命"),
    ("周培源", "从流体力学到教育现场，他在国家最需要基础科学时撑住一张书桌"),
    ("裴文中", "他在周口店发现北京人头盖骨，让中国古人类研究有了关键证据"),
    ("钱学森", "美国海关扣下他的行李，硬说里面藏着国家机密"),
    ("钱三强", "他在海外实验室握住前沿成果，却选择回到一穷二白的中国原子能事业"),
    ("程开甲", "他隐姓埋名走进戈壁，把自己的名字藏在一次次核试验背后"),
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
    ("赵九章", "他把目光投向高空和太空，为中国第一颗人造卫星铺路"),
    ("王应睐", "他带队攻关人工合成胰岛素，在一次次失败里守住实验室的灯"),
    ("汪猷", "他在有机化学深处长期耕耘，把基础研究变成后人继续攀登的台阶"),
    ("华罗庚", "他从小店学徒走向数学高峰，又把优选法带到工厂和车间"),
    ("侯祥麟", "他把一生交给中国石油炼制，让工业血脉不再处处受制于人"),
    ("罗沛霖", "他在电子学和通信工程的关键处埋头搭桥，让技术真正服务国家工程"),
    ("何泽慧", "她在核物理实验中一次次校准轨迹，把名字留在中国原子科学起步处"),
    ("王大珩", "他在中国光学玻璃最薄弱的时候，带人从零搭起精密光学的根基"),
    ("彭桓武", "他从海外回国后投身理论物理和核事业，把个人荣誉放到国家需求之后"),
    ("卢嘉锡", "他做科研不怕先估算再验证，用朴素办法把复杂问题往前推"),
    ("任新民", "他在航天型号一线统筹攻关，被称为中国航天通信卫星的总总师"),
    ("叶笃正", "他研究大气环流和气候变化，把天地万象变成可追问的科学问题"),
    ("陈芳允", "他参与卫星测控，让东方红的声音真正从太空传回中国"),
    ("吴征镒", "他一生跋山涉水采集植物，把中国植物志写进世界科学版图"),
    ("黄纬禄", "他盯着导弹和潜射系统的每个细节，等待巨浪腾空的那一刻"),
    ("刘东生", "他踩着黄土高原的风尘做研究，从层层黄土里读懂地球历史"),
    ("屠守锷", "他带队研制洲际导弹，在无数图纸和试验里托起大国长剑"),
    ("吴自良", "他为关键材料和分离膜技术攻关，让国家工程装上可靠的心脏"),
    ("林兰英", "她在半导体材料最艰难的时候往前顶，把单晶材料做成中国底气"),
    ("吴文俊", "他把中国古代数学思想和现代数学连接起来，走出机器证明的新路"),
    ("杨嘉墀", "他把自动控制和空间技术拧在一起，为中国卫星追星探路"),
    ("黄昆", "他在固体物理和半导体理论深处扎根，托起中国半导体的一代基础"),
    ("谢家麟", "他三十年投身加速器，把看不见的粒子轨迹变成国家大科学装置"),
    ("徐光宪", "他在稀土分离难题前反复拆解，把中国稀土优势真正做硬"),
    ("师昌绪", "他盯住高温合金和关键材料，让中国装备有了更硬的金属翅膀"),
    ("朱光亚", "他写信召回留学生，又把自己从功劳簿上悄悄往后放"),
]
RECENT_GUOZHIJILIANG_PEOPLE: list[str] = []
MAX_RECENT_GUOZHIJILIANG_PEOPLE = 12
GUOZHIJILIANG_OPENING_GUIDES = [
    "物件开场：先写一个能入镜的物件，比如行李箱、公文包、病号服、饭盘、算盘、桥梁图纸、野外采样袋，再揭示它背后的国家命运。",
    "选择开场：先写这个人物放弃了什么，比如署名、回家、高薪、安稳、荣誉、健康、家庭时间，再写为什么这个选择反常识。",
    "结果倒放：先写后来发生的巨大结果，比如大桥通车又被炸、卫星传回信号、导弹升空、稀土分离突破，再倒回最不起眼的那一刻。",
    "身份反差：先写观众最容易误判的普通身份或生活画面，再揭示他/她真正托住的国家工程。",
    "沉默代价：先写这个人物没有说出口、不能说出口、没人知道的一件事，让悬念来自沉默而不是口号。",
    "历史误解：先写一段亲人、同事、公众当年无法理解的误会，再用事实反转。",
    "现场危机：先写一个具体危机现场，比如风沙、病房、实验室深夜、银行柜台、桥边爆破、试验场倒计时。",
    "名字消失：先写名单、档案、论文、工程记录里看不见的名字，再解释为什么这个人主动或被迫站到背后。",
]
RECENT_GUOZHIJILIANG_OPENINGS: list[str] = []
MAX_RECENT_GUOZHIJILIANG_OPENINGS = 4
MIN_GUOZHIJILIANG_SCRIPT_CHARS = 1000
MAX_GUOZHIJILIANG_SCRIPT_CHARS = 1300
MIN_GUOZHIJILIANG_SCRIPT_PARAGRAPHS = 20
MAX_GUOZHIJILIANG_SCRIPT_PARAGRAPHS = 30
WEAK_GUOZHIJILIANG_OPENING_PATTERNS = (
    "出生于",
    "是我国",
    "是一位",
    "有这样一位",
    "提起",
    "说到",
    "在中国科学史上",
    "在那个年代",
    "那个年代",
    "在当时",
    "故事要从",
    "今天讲",
    "他很伟大",
    "她很伟大",
    "民族脊梁",
    "做出了巨大贡献",
)


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


def compare_scripts(
    text1: str,
    text2: str,
    protected_opening: str = "",
    extra_min_length: int = 0,
    extra_max_length: int = 0,
) -> dict:
    source_length = content_length(text1)
    rewritten_length = content_length(text2)
    min_rewritten_length = int(source_length * MIN_REWRITE_LENGTH_RATIO) + max(0, extra_min_length)
    max_rewritten_length = int(source_length * MAX_REWRITE_LENGTH_RATIO) + max(0, extra_max_length)
    length_ratio = round((rewritten_length / source_length) * 100) if source_length else 0
    length_passed = bool(source_length) and min_rewritten_length <= rewritten_length <= max_rewritten_length
    # The fixed opening is intentionally included in every reuse metric. The
    # optional argument remains for call compatibility with older integrations.
    body1 = text1
    body2 = text2
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
    overall_similarity = round(
        (continuous_reuse * 0.35)
        + (source_phrase_reuse * 0.35)
        + (sentence_imitation * 0.30)
    )
    overall_difference = max(0, min(100, 100 - overall_similarity))
    reused_passages = sorted(
        {compact1[block.a:block.a + block.size] for block in reused_blocks},
        key=len,
        reverse=True,
    )[:8]

    non_length_quality_passed = (
        overall_difference >= MIN_REWRITE_DIFFERENCE
        and continuous_reuse <= MAX_REWRITE_CONTINUOUS_REUSE
        and source_phrase_reuse <= MAX_REWRITE_SOURCE_PHRASE_REUSE
        and sentence_imitation <= MAX_REWRITE_SENTENCE_IMITATION
    )
    passed = length_passed and non_length_quality_passed
    return {
        "continuous_reuse": continuous_reuse,
        "phrase_overlap": phrase_overlap,
        "source_phrase_reuse": source_phrase_reuse,
        "sentence_imitation": sentence_imitation,
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
        "min_rewritten_length": min_rewritten_length,
        "max_rewritten_length": max_rewritten_length,
        "protected_opening_length": 0,
        "reused_passages": reused_passages,
        "common_keywords": sorted(terms1 & terms2, key=len, reverse=True)[:10],
        "unique_keywords1": sorted(terms1 - terms2, key=len, reverse=True)[:10],
        "unique_keywords2": sorted(terms2 - terms1, key=len, reverse=True)[:10],
        "passed": passed,
    }


def split_sentences(text: str) -> list[str]:
    parts = re.findall(r"[^。！？!?；;]+[。！？!?；;]?", str(text or "").strip())
    return [p.strip() for p in parts if p.strip()]


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
    known_people = [
        item for item in PERSON_HINTS
        if item not in {"两弹一星"}
    ] + ["巴金", "鲁迅", "茅盾", "宋庆龄", "宋美龄", "宋霭龄"]
    person = next((item for item in sorted(set(known_people), key=len, reverse=True) if item in text), "")
    if person:
        return person
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
    source = re.sub(r"\s+", "", str(raw_script or "").strip())
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

    selected = ""
    for sentence in sentences:
        candidate = f"{selected}{sentence.strip()}"
        if len(candidate) <= OPENING_HOOK_MAX_CHARS:
            selected = candidate
            if len(selected) >= OPENING_HOOK_MIN_CHARS:
                return selected
            continue
        break

    if len(selected) >= OPENING_HOOK_MIN_CHARS:
        return selected

    # An unusually long first sentence still needs a deterministic boundary.
    # Prefer a complete clause in the 20-35 character window; only fall back to
    # a hard cap when the source contains no usable punctuation there.
    window = source[:OPENING_HOOK_MAX_CHARS]
    clause_ends = [
        match.end() for match in re.finditer(r"[，,：:；;。！？!?]", window)
        if match.end() >= OPENING_HOOK_MIN_CHARS
    ]
    if clause_ends:
        return window[:clause_ends[-1]]
    return window


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


def normalize_sales_book_title(title: str) -> tuple[str, str]:
    bare = str(title or "").strip().strip("《》").strip() or "国之脊梁"
    return bare, f"《{bare}》"


def ensure_rewrite_book_promotion(script: str, enabled: bool, book_title: str) -> str:
    rewritten = str(script or "").strip()
    if not enabled:
        return rewritten
    bare, formatted = normalize_sales_book_title(book_title)
    if bare in rewritten[-240:]:
        return rewritten
    promotion = (
        "一个人的故事讲完了，可撑起今天这份底气的，从来不止一个名字。"
        f"如果你也想看见更多人在国家最需要的时候做过怎样的选择，可以读一读{formatted}。"
        "它真正值得留下的，不只是人物经历，更是人在利益、亲情和责任面前如何作答。"
        "尤其家里有孩子，与其只告诉他什么叫榜样，不如让他从这些真实人生里自己找到答案。"
        "这不是读完就放下的一本书，而是值得放在家里，值得和孩子一起慢慢看。"
    )
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
    if content_length(raw_script) and content_length(rewritten) < int(content_length(raw_script) * MIN_REWRITE_LENGTH_RATIO):
        rewritten = paragraphize_script(raw_script)
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
            extra_min_length=140 if append_book_promotion else 0,
            extra_max_length=220 if append_book_promotion else 0,
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
    if not comparison.get("length_passed", False):
        issues.append(
            f"字数为原文的 {comparison.get('length_ratio', 0)}%"
            f"（建议 {round(MIN_REWRITE_LENGTH_RATIO * 100)}%-{round(MAX_REWRITE_LENGTH_RATIO * 100)}%）"
        )
    if int(comparison.get("overall_difference") or 0) < MIN_REWRITE_DIFFERENCE:
        issues.append(
            f"总体重构度 {comparison.get('overall_difference', 0)}%"
            f"（建议至少 {MIN_REWRITE_DIFFERENCE}%）"
        )
    if int(comparison.get("continuous_reuse") or 0) > MAX_REWRITE_CONTINUOUS_REUSE:
        issues.append(
            f"连续复用率 {comparison.get('continuous_reuse', 0)}%"
            f"（建议不超过 {MAX_REWRITE_CONTINUOUS_REUSE}%）"
        )
    source_phrase_reuse = int(comparison.get("source_phrase_reuse") or 0)
    if source_phrase_reuse > MAX_REWRITE_SOURCE_PHRASE_REUSE:
        issues.append(
            f"短语复用率 {source_phrase_reuse}%"
            f"（建议不超过 {MAX_REWRITE_SOURCE_PHRASE_REUSE}%）"
        )
    sentence_imitation = int(comparison.get("sentence_imitation") or 0)
    if sentence_imitation > MAX_REWRITE_SENTENCE_IMITATION:
        issues.append(
            f"逐句模仿率 {sentence_imitation}%"
            f"（建议不超过 {MAX_REWRITE_SENTENCE_IMITATION}%）"
        )
    detail = "；".join(issues) or "部分质量指标未达到建议值"
    return f"二创稿已生成并保留，但质量检查未完全达标：{detail}。你可以直接编辑、复制或再次改写。"


def normalize_rewrite_result(
    result: dict,
    raw_script: str,
    style: str,
    preserve_rule: str = "auto",
    append_book_promotion: bool = False,
    promotion_book_title: str = "国之脊梁",
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
        extra_min_length=140 if append_book_promotion else 0,
        extra_max_length=220 if append_book_promotion else 0,
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


def build_rewrite_analysis_prompt(raw_script: str) -> str:
    raw_len = content_length(raw_script)
    minimum_fact_items = max(4, round(raw_len / REWRITE_ANALYSIS_CHARS_PER_FACT_ITEM))
    minimum_material_length = int(raw_len * REWRITE_ANALYSIS_MATERIAL_RATIO)
    maximum_analysis_length = max(1400, min(2800, int(raw_len * 1.1)))
    return f"""你是短视频事实编辑。完整通读原文，制作一份供第二个模型重新写作的紧凑素材包。本阶段只做内容理解和事实拆解，不写二创文案。

原文去除空白后约 {raw_len} 字，后续二创稿仍要写到约 {raw_len} 字。素材包有效内容约 {minimum_material_length} 字即可，不追求长篇分析。

【爆款分析】总计不超过200字，只写4条短结论：
1. 钩子结构：最主要的停留原因和视频结构。
2. 完播机制：最有效的细节密度、信息释放和情绪递进。
3. 转发机制：这是核心。最主要的转发理由、用户痛点和情绪点。
4. 互动机制：评论、点赞、关注的真实理由；原文没有就写“无明显设计”。

【紧凑事实卡】资料卡不是摘要，但禁止重复：
1. material_cards 至少 {minimum_fact_items} 条，每条用一至两句话合并写清人物、时间地点、动作、原因、结果及关键数字；同一事件只写一次。
2. 保留支撑同等篇幅写作所需的重要事实、人物关系、转折、代价和画面，不复制原文完整句子，不保留原文修辞和段落结构。
3. 设计4至6个新的写作阶段，target_chars 合计接近 {raw_len}，不得照搬原文段落顺序。
4. 判断原文是否包含图书推荐；没有就标记 false。

整个 JSON 去除空白后不得超过约 {maximum_analysis_length} 字符。只返回以下单层紧凑 JSON，不得增加字段，不得使用 Markdown：
{{
  "core_subject": "主体",
  "core_conflict": "冲突",
  "key_choice": "选择",
  "story_outcome": "结果",
  "viral_analysis": {{"hook": "短句", "completion": "短句", "share": "短句", "interaction": "短句"}},
  "material_cards": ["紧凑事实卡1", "紧凑事实卡2"],
  "must_preserve_terms": ["人名地名年份数字专名"],
  "section_plan": [{{"task": "段落任务", "cards": [1, 2], "target_chars": 500}}],
  "book_promotion": {{"present": false}}
}}

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
    viral_analysis = result.get("viral_analysis") if isinstance(result.get("viral_analysis"), dict) else {}
    structure_summary = result.get("structure_summary") if isinstance(result.get("structure_summary"), dict) else {}
    section_plan = result.get("section_plan") if isinstance(result.get("section_plan"), list) else []
    fact_item_count = len(timeline) + len(facts) + len(material_cards)
    minimum_fact_items = (
        max(4, round(raw_length / REWRITE_ANALYSIS_CHARS_PER_FACT_ITEM))
        if raw_length else 1
    )
    material_content = {
        "timeline": timeline,
        "facts": facts,
        "material_cards": material_cards,
        "relationships": result.get("relationships") or [],
        "viral_analysis": viral_analysis,
        "structure_summary": structure_summary,
        "section_plan": section_plan,
    }
    material_length = nested_value_content_length(material_content)
    minimum_material_length = int(raw_length * REWRITE_ANALYSIS_MATERIAL_RATIO) if raw_length else 1
    material_density_ratio = round((material_length / raw_length) * 100) if raw_length else 0
    section_target_chars = sum(
        int(item.get("target_chars") or 0)
        for item in section_plan
        if isinstance(item, dict) and str(item.get("target_chars") or "").isdigit()
    )
    section_target_ratio = round((section_target_chars / raw_length) * 100) if raw_length else 0
    section_plan_passed = (
        len(section_plan) >= 4
        and (not raw_length or 85 <= section_target_ratio <= 115)
    )
    coverage_passed = (
        fact_item_count >= minimum_fact_items
        and material_length >= minimum_material_length
        and section_plan_passed
    )
    return {
        "source_length": raw_length,
        "target_rewrite_length": raw_length,
        "min_rewrite_length": int(raw_length * MIN_REWRITE_LENGTH_RATIO) if raw_length else 0,
        "max_rewrite_length": int(raw_length * MAX_REWRITE_LENGTH_RATIO) if raw_length else 0,
        "fact_item_count": fact_item_count,
        "minimum_fact_items": minimum_fact_items,
        "material_length": material_length,
        "minimum_material_length": minimum_material_length,
        "material_density_ratio": material_density_ratio,
        "section_target_chars": section_target_chars,
        "section_target_ratio": section_target_ratio,
        "section_plan_passed": section_plan_passed,
        "fact_coverage_passed": coverage_passed,
        "core_subject": str(result.get("core_subject") or "").strip(),
        "core_conflict": str(result.get("core_conflict") or "").strip(),
        "key_choice": str(result.get("key_choice") or "").strip(),
        "story_outcome": str(result.get("story_outcome") or "").strip(),
        "timeline": timeline,
        "facts": facts,
        "material_cards": material_cards,
        "relationships": result.get("relationships") if isinstance(result.get("relationships"), list) else [],
        "must_preserve_terms": result.get("must_preserve_terms") if isinstance(result.get("must_preserve_terms"), list) else [],
        "viral_analysis": viral_analysis,
        "structure_summary": structure_summary,
        "section_plan": section_plan,
        "book_promotion": {
            "present": bool(promotion.get("present")),
            "facts": promotion.get("facts") if isinstance(promotion.get("facts"), list) else [],
        },
    }


def request_minimax_rewrite_analysis(raw_script: str, api_key: str) -> dict:
    base_prompt = build_rewrite_analysis_prompt(raw_script)
    raw_len = content_length(raw_script)
    minimum_fact_items = max(4, round(raw_len / REWRITE_ANALYSIS_CHARS_PER_FACT_ITEM))
    minimum_material_length = int(raw_len * REWRITE_ANALYSIS_MATERIAL_RATIO)
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
                "不要使用 Markdown 代码块，不要在 JSON 前后添加解释，并严格压缩内容长度。"
            )
        elif last_brief:
            retry_note = (
                f"\n\n上一版分析未达到写作素材密度要求：事实/素材卡 {last_brief.get('fact_item_count', 0)} 条，"
                f"最低 {minimum_fact_items} 条；有效分析与素材约 {last_brief.get('material_length', 0)} 字，"
                f"最低 {minimum_material_length} 字；结构计划 {len(last_brief.get('section_plan') or [])} 段，最低4段。"
                "请重新通读原文，补齐爆点、完播、转发、评论、点赞、关注机制，"
                "把遗漏的动作、原因、结果、人物关系、时间节点、画面与情绪细节补齐，"
                "并重新给出目标字数接近原文的分段写作计划。"
            )
        payload = {
            "model": minimax_model(),
            "messages": [
                {"role": "system", "content": "你只做原文事实拆解，只输出可解析 JSON，不写二创稿。"},
                {"role": "user", "content": base_prompt + retry_note},
            ],
            "temperature": 0.2,
            "top_p": 0.8,
            "max_tokens": max(1100, min(2500, round(raw_len * 0.70))),
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
            parsed_analysis = json.loads(extract_json(str(content)))
            last_brief = normalize_rewrite_fact_brief(parsed_analysis, raw_len)
            last_analysis_error = None
        except (json.JSONDecodeError, ValueError) as exc:
            last_analysis_error = exc
            continue
        if last_brief["fact_coverage_passed"]:
            return last_brief
        last_brief["analysis_warning"] = (
            f"已使用精简素材包继续写作：素材卡共 {last_brief.get('fact_item_count', 0)} 条，"
            f"有效素材约 {last_brief.get('material_length', 0)} 字。"
        )
        return last_brief
    if last_brief:
        last_brief["analysis_warning"] = (
            f"素材分析密度未完全达标：事实 {last_brief.get('fact_item_count', 0)}/{minimum_fact_items}，"
            f"有效素材 {last_brief.get('material_length', 0)}/{minimum_material_length} 字，"
            f"结构计划 {len(last_brief.get('section_plan') or [])}/4 段。已使用当前最完整资料卡继续写作。"
        )
        return last_brief
    if last_analysis_error:
        raise RuntimeError(
            "MiniMax source analysis returned invalid JSON after "
            f"{MAX_REWRITE_ANALYSIS_ATTEMPTS} analysis attempts: "
            f"{type(last_analysis_error).__name__}: {last_analysis_error}"
        ) from last_analysis_error
    raise RuntimeError("Rewrite analysis did not return a usable fact brief")


def build_rewrite_prompt(
    raw_script: str,
    style: str,
    attempt: int,
    previous: dict | None = None,
    preserve_rule: str = "auto",
    fact_brief: dict | None = None,
    append_book_promotion: bool = False,
    promotion_book_title: str = "国之脊梁",
) -> str:
    opening_hook = extract_opening_hook(raw_script, preserve_rule)
    raw_len = content_length(raw_script)
    min_len = int(raw_len * MIN_REWRITE_LENGTH_RATIO) + (140 if append_book_promotion else 0)
    max_len = int(raw_len * MAX_REWRITE_LENGTH_RATIO) + (220 if append_book_promotion else 0)
    source_has_book_promotion = bool(re.search(
        r"(《[^》]+》|这本书|书里|书中|翻开|读完|买给孩子|下单|购买|小黄车|带书|卖书|推荐给家长)",
        raw_script,
    ))
    brief_promotion = (fact_brief or {}).get("book_promotion") or {}
    has_book_promotion = bool(brief_promotion.get("present", source_has_book_promotion))
    _, formatted_book_title = normalize_sales_book_title(promotion_book_title)
    if append_book_promotion:
        conversion_instruction = (
            f"用户已主动开启结尾带书。最后一个自然段必须完成行动收束；在此之前，故事讲完后必须用最后 2 到 3 个自然段完成对{formatted_book_title}的价值塑造，"
            "带书部分总计写 140 到 220 个中文字符，不能只用一两句提到书名就草草结束。"
            "第一步先承接本篇人物最具体的选择、代价或精神落点，把观众对这个人的情绪自然转到“还有更多这样的名字值得被看见”，不能突然切广告。"
            "第二步塑造产品价值：说清楚这本书能帮助读者看见什么、理解什么、记住什么；重点可以是课本来不及展开的真实人生、关键时刻的选择、一个国家底气背后的人，"
            "但必须使用概括性表达，不能虚构书中具体人物、章节、故事数量、作者背书或装帧信息。"
            "第三步给出明确的读者理由和阅读场景，例如家长希望孩子建立榜样认知、成年人想补上对这些人物的了解、全家一起读并讨论选择与责任。"
            "最后用一句克制但有行动力的话收束，让人自然产生想把书带回家、自己读或和孩子一起读的冲动。"
            "语言要像真诚推荐，不要喊口号，不要连续使用空泛的爱国大词；不要写“赶紧买”“点击小黄车”，也不要编造价格、优惠、稀缺性、赠品或购买渠道。"
        )
        book_rewrite_permission = "用户已明确开启结尾带书，因此允许并必须重新设计完整的产品价值塑造与转化收束。"
    else:
        conversion_instruction = (
            "原文包含带书或图书推荐内容，可以保留原有转化意图并重新表达，但不要扩大篇幅、不要改成硬广。"
            if has_book_promotion else
            "原文不包含带书或图书推荐内容，改写稿也禁止主动添加书名、翻书、读书感受、买书、小黄车、家长购买或推荐给孩子等转化内容。结尾必须跟随原文主题自然收束。"
        )
        book_rewrite_permission = "只有原文包含带书内容时，才可以重新设计其表达方式。"
    retry_instruction = ""
    if previous:
        comparison = previous.get("rewrite_comparison") or {}
        previous_script = str(previous.get("rewritten_script") or "").strip()
        previous_length = comparison.get("text2_length", content_length(previous_script))
        reused_passages = [
            str(item).strip()
            for item in comparison.get("reused_passages", [])
            if str(item).strip()
        ]
        reused_summary = "；".join(reused_passages[:8]) or "系统未定位到单一长句，请全面检查正文表达"
        retry_instruction = (
            f"上一版没有通过真正重写验收：总体重构度 {comparison.get('overall_difference', 0)}%，"
            f"字数 {comparison.get('text2_length', 0)}，原文 {comparison.get('text1_length', 0)} 字，"
            f"合格范围 {comparison.get('min_rewritten_length', min_len)} 到 {comparison.get('max_rewritten_length', max_len)} 字，"
            f"固定开头之外的连续照抄率 {comparison.get('continuous_reuse', comparison.get('character_similarity', 0))}%，"
            f"原文短语复用率 {comparison.get('source_phrase_reuse', comparison.get('phrase_overlap', 0))}%，"
            f"逐句模仿率 {comparison.get('sentence_imitation', 0)}%。"
            f"重点重复片段：{reused_summary}。"
            "固定开头仍须原样保留。放弃上一版的段落、句序和叙事路径，回到事实素材重新构思。"
            "不得逐句找同义词，不得按原文段落一一对应；应重新选择主冲突、重新编排信息出现顺序、重新组织场景和情绪递进。"
            f"\n<previous_rewrite>{previous_script}</previous_rewrite>"
        )
        if not comparison.get("length_passed", True):
            if previous_length < min_len:
                missing_chars = max(raw_len - previous_length, min_len - previous_length)
                retry_instruction += (
                    f"\n\n【本轮首要任务：定向扩写】上一版只有 {previous_length} 字，不要另起炉灶重写成另一篇短稿。"
                    f"请以上一版为底稿，至少增加约 {missing_chars} 字，最终写到接近 {raw_len} 字。"
                    "逐条检查事实资料卡，把尚未充分展开的时间节点、人物动作、原因、后果和具体画面写成完整叙事；"
                    "补充自然过渡和情绪递进，但禁止虚构、重复同一件事或堆砌空话。必须返回扩写后的完整全文。"
                )
            elif previous_length > max_len:
                retry_instruction += (
                    f"\n\n【本轮首要任务：定向精简】上一版有 {previous_length} 字，请保留完整事实与叙事主线，"
                    f"删去重复表达和空泛评价，最终压缩到接近 {raw_len} 字。必须返回精简后的完整全文。"
                )
    fact_brief_json = json.dumps(fact_brief or {}, ensure_ascii=False, indent=2)
    creative_guidelines = load_rewrite_creative_guidelines()
    prompt = f"""
你是一名视频号爆款短视频文案改写专家，擅长改写卖书类、历史人物类、大国情绪类、爱国教育类短视频口播文案。

这是两步创作流程的第二步。原文已经由事实编辑拆成资料卡，你不能看到原文正文，也不得猜测或还原原文表达。请只根据事实资料卡重新创作完整文案，目标是在视频号发布，用于提高播放量和完播率。
事实资料卡不是篇幅上限。要把其中的客观事实重新组织成完整场景、动作、过渡和情绪递进，写到资料卡标注的目标字数；不得因为资料卡采用短句记录，就把成稿写成摘要。

【写作前必须使用第一步分析】
1. 先读取 viral_analysis，理解原文的停留爆点、完播机制、情绪曲线、转发价值以及评论、点赞、关注理由；不要只读取 timeline 和 facts。
2. 再读取 structure_summary 和 section_plan，按照新的叙事主线与各段 target_chars 规划全文。各段可以自然浮动，但总字数必须接近目标。
3. material_cards 是详细写作素材。把动作、条件、因果、画面和人物代价写进正文，维持原文的细节密度，不能把素材卡重新压缩成摘要。
4. 复用的是原文奏效的内容机制，不是原文句子。必须重新设计表达、段落组织和信息出现顺序。
5. 转发、评论、点赞、关注理由要融进观点、冲突和情绪落点；只在合适位置使用一次自然互动引导，禁止机械喊“点赞关注转发”。

【二创文案创作规则】
以下规则来自项目的《二创提示词》，必须执行。它规定创作方法；本提示词后续给出的固定开头、事实资料卡、字数、带书开关和 JSON 输出要求属于本次任务的具体约束，优先级更高。
{creative_guidelines}

【最重要要求】
用户选定的原文开头必须一字不改保留，不允许改字、不允许换词、不允许调整顺序、不允许删减。
你只能从这段受保护内容之后开始创作。即使你判断开头不够好，也不能擅自改动。
必须原样保留的开头是：{opening_hook}

【固定开头后的连续叙事】
固定开头不是一段可以独立放置的引子，而是整篇文案正在发生的第一段剧情。后续正文必须紧接固定开头最后一句继续讲，不能另起一个开头。
固定开头之后，不得沿着原文的段落结构逐段改写，必须根据资料卡重新选择一条更有吸引力的叙事主线。资料卡只规定事实边界，不规定写作顺序和表达结构。
固定开头后的第一句必须承担过渡作用，让观众知道后文和开头是同一个故事；但后续信息出现顺序、场景组合、铺垫位置、冲突展开和情绪递进都要重新设计。
禁止在固定开头后再次使用“谁能想到”“你敢相信吗”“很多人不知道”“他到底是谁”等二次钩子来重启文案；也不要像第一次出场一样重新介绍同一个人物，造成两个独立开头。原文确实转入人物背景时，可以保留这一结构，但必须写成承接上文的背景补充。
例如固定开头以“妻子追出来说了一番话，把他感动得老泪纵横”结束，而原文接下来转入他早年的经历，可以写“而这场迟到了四十二年的重逢，还要从他当年离开广东说起”，不能直接写“谁能想到，一个广东读书人……”另起一个开头。

【改写目标】
保留原文的短视频味道，不要改成书面文章。
改写后的文案要像一个懂视频号的人在口播，而不是像公众号社论、新闻评论、AI润色稿、学生作文。
整体风格要：口语化、有网感、有情绪、有画面、有节奏、有冲突。
不要追求文采高级，要追求用户愿意听下去、愿意点赞、愿意评论、愿意转发。

【只允许原样保留的内容】
1. 只有用户选定的固定开头必须逐字保留；未手动选择时，保留系统识别的前三秒钩子。
2. 人名、地名、年份、数字、事件等客观事实可以保留，但承载这些事实的句子必须重新表达。
3. 除固定开头和无法改写的专有名词外，原文中的完整句子、金句、狠话、过渡句、情绪表达和带书话术都不要照抄。
4. 必须保留客观事实、真实时间关系和因果关系；原文的信息揭示顺序、段落顺序、各段功能、叙述落点和转折方式不属于事实，必须重新设计。

【真正大改：把事实资料卡当素材库】
1. 除固定开头外，禁止按原文从第一段到最后一段逐段对应改写。
2. 禁止“读一句、换一种说法”；即使每句话都换了词，只要句子顺序、段落功能和信息出现位置仍与原文一一对应，也是不合格。
3. 先找出全文最值得讲的核心冲突和人物选择，以它为主轴；背景、履历、评价只在推动主轴时出现。
4. 可以合并原文中分散的同类事实，可以把后文的重要结果提前，可以把背景延后，可以删除重复观点；但不能篡改真实时间、因果和事实。
5. 每一段都要有新的叙述任务：推进冲突、揭示代价、制造反差、补关键证据或完成情绪转折。不要让新稿段落与原文段落一一配对。
6. 使用新的句式、新的观察角度、新的场景切入和新的转折。不要保留原文的金句、排比、设问、比喻、段尾结论和情绪口号。
7. 输出前在心里做一次“遮住原文测试”：如果读者能明显看出新稿是在沿着原文一句句改，必须推翻固定开头之后的全部内容重新写。

【禁止事项】
不要做简单同义词替换。
不要把口语改成书面语。
不要把“咱妈、塞铁、刷666、小鱼小虾、你可以试试、护筷子”这类短视频表达全部洗掉。
不要使用过多书面词，例如：悍然、方知、伟岸、至此、乃、赴汤蹈火、径直、再至、苍生、星光、抉择、壮烈史诗、强国气场、脱胎换骨、恩重如山。
不要频繁使用空泛大词，例如：伟大、震撼、辉煌、底蕴、史诗、精神源泉、民族脊梁、大国情怀。这些词可以少量使用，但不能堆。
不要把文案改成端着的播音腔。
不要一上来介绍背景，不要平铺直叙。
不要用背景、环境、天气、时代、氛围、人物状态来开头。开头必须先给爆点、冲突、结果、反差或悬念。
固定开头后的第一句不要生硬套用“在那个年代”“当时的环境”“故事要从某年说起”。需要按原文结构转入背景时，要先建立它与固定开头的联系，再自然过渡。
不要削弱原文的爽感、反差感和情绪冲击。

【短视频改写原则】
一、句子要短。适合真人口播。能用短句就不要用长句。能用人话就不要用书面话。
二、表达要狠。该硬的地方要硬。比如：“你可以试试敢不敢将它击落。”这种句子不要改成：“那便试试看是否敢于动用武力击落。”
三、要有画面。多保留或强化具体画面：飞机起飞、国旗铺满街道、地图包围、旧照片、病房电脑、公文包、胶鞋、行李箱、实验室灯光、戈壁风沙。少写抽象评价。
四、要有情绪递进。固定开头保持不变，后文围绕重新选定的核心冲突推进，重新安排事实、背景、转折和情绪爆发；不能复刻原文的信息揭示节奏。
五、带书内容跟随原文：{conversion_instruction}

【分段要求】
按画面逻辑组织自然段，画面和语义完整优先于字数。每段建议控制在 40 到 60 个中文字符，通常不要超过 80 个字符，但不能只因为达到某个字数就强行拆段。
用户选定的固定开头必须完整原样保留，不受段落字数建议限制，不得为了控制段落长度删改或拆碎固定开头。
每段应尽量对应一个完整画面，方便后续 AI 配图、素材搜索和剪映剪辑。只有出现时间、地点、人物动作、画面主体或情绪节点变化时才换段；同一人物、同一时间地点、同一连续动作应尽量放在一段。
去除空白后少于 25 个字符的段落，除非它本身是一个独立且有冲击力的完整画面，否则应与相邻段落合并。超过 80 个字符时，也只能在自然的语义或画面转换处拆段，不能从一句话中间生硬截断。
一个自然段内部不要换行，段落之间使用空行分隔。
换段标准是：时间变化、地点变化、人物动作变化、画面主体变化、情绪节点变化。
不要按朗读断句分段，而要按画面分段。
不要在 rewritten_script 中添加 [1]、[2] 等序号，序号由前端界面展示。

【改写尺度】
不是洗稿式同义替换。只保留固定开头和事实边界，原文叙事骨架必须推翻重建。
可以删掉重复啰嗦的句子。
可以强化画面感和冲突感。
必须重建固定开头之后的句子组织、信息顺序、段落功能、叙述落点、转折方式和情绪推进。真实时间与因果不能打乱，但原文的讲述顺序可以而且应当改变。
可以在不改变事实主体、真实时间关系和因果关系的前提下更换叙述视角；{book_rewrite_permission}
除固定开头外，不得连续照抄原文句子；不得只靠换词、增删标点或重新分段制造改写效果。

【长度和质量约束】
原文去除空白后的长度约 {raw_len} 个中文字符。
rewritten_script 去除空白后的长度必须控制在 {min_len} 到 {max_len} 个中文字符之间。
应以接近原文的 {raw_len} 字为写作目标，不要刻意写到允许区间的上限或下限；在事实完整、口播自然的前提下，最终字数应尽量贴近原文。
不要压缩成摘要、提纲或短版解说，也不要省略原文中的重要事实。
事实边界：不虚构；不添加没有依据的具体时间、地点、人物关系；人物、年代、事件、因果关系必须保留。
除必须原样保留的固定开头、专有名词、年份、数字和固定称谓之外，整体表达必须重新组织。
系统检查整篇文案，固定开头也参与重复率和总体重构度计算。总体重构度必须达到 {MIN_REWRITE_DIFFERENCE}% 以上，连续照抄率不得超过 {MAX_REWRITE_CONTINUOUS_REUSE}%，原文六字短语复用率不得超过 {MAX_REWRITE_SOURCE_PHRASE_REUSE}%，逐句模仿率不得超过 {MAX_REWRITE_SENTENCE_IMITATION}%。
新增一两句话、增加段落、换行、调整标点、调换少量句子都不能稀释这些指标；任何一项超限都会整篇退回重写。固定开头必须原样保留，即使它会提高重复率，也绝不能为了提高分数而改动。
人名、地点、年份和事实关键词的正常重合只单独展示，不计入差异度；不要为了降低关键词重合而篡改事实。
{MIN_REWRITE_DIFFERENCE}% 是硬性验收线：不能通过加句、换行、调整标点、段落错位或同义词替换达成，固定开头之后的正文必须让读者明显感到作者重新构思、重新组织、重新讲述了一遍。

【本次生成信息】
文案风格：{style}。
这是第 {attempt} 次生成。{retry_instruction}

【输出要求】
只返回可解析 JSON，字段必须包含 title, hook, rewritten_script, script_style。
rewritten_script 字段里只能放改写后的完整文案正文，禁止包含原文、原始文案、对照稿、解释、标题标签或“二创口播稿：”这类前缀。
不要先输出一遍原文再输出二创稿，也不要把原文和二创内容混在一起。
输出前自检固定开头与第一句新正文：如果第一句像与前文无关的新钩子，或像同一人物第一次出场一样重新介绍，必须在不改变事实与因果关系的前提下重写成自然过渡。
输出前检查分段是否过碎：不能为了凑 40 到 60 字拆散同一个连续画面；少于 25 字且不能独立成画面的段落要与相邻段合并，超过 80 字的段落只在确有画面转换时自然拆分。

【事实资料卡】
<fact_brief>{fact_brief_json}</fact_brief>
"""
    return prompt


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
        fact_brief = request_minimax_rewrite_analysis(raw_script, api_key)
    except RewriteGenerationError:
        raise
    except Exception as exc:
        raise RewriteGenerationError("source analysis", exc) from exc
    best_result: dict | None = None
    last_result: dict | None = None

    def candidate_rank(candidate: dict) -> tuple[int, int, int, int]:
        metrics = candidate.get("rewrite_comparison") or {}
        length_ratio = int(metrics.get("length_ratio") or 0)
        return (
            int(bool(metrics.get("passed"))),
            int(bool(metrics.get("non_length_quality_passed"))),
            -abs(length_ratio - 100),
            int(metrics.get("overall_difference") or 0),
        )

    for attempt in range(1, MAX_REWRITE_ATTEMPTS + 1):
        prompt = build_rewrite_prompt(
            raw_script, style, attempt, last_result, preserve_rule, fact_brief,
            append_book_promotion, promotion_book_title,
        )
        try:
            result = request_minimax_rewrite(
                prompt, raw_script, style, api_key, raw_len, preserve_rule,
                append_book_promotion, promotion_book_title,
            )
        except Exception as exc:
            if best_result:
                comparison = best_result.get("rewrite_comparison") or {}
                best_result["rewrite_attempts"] = attempt - 1
                best_result["rewrite_warning"] = (
                    build_rewrite_quality_warning(comparison)
                    + f" 后续第 {attempt} 次重试调用失败，已展示此前最好的完整稿件。"
                )
                best_result["rewrite_quality_status"] = "generation_warning"
                best_result["rewrite_error"] = ""
                return best_result
            if isinstance(exc, RewriteGenerationError):
                raise
            raise RewriteGenerationError(f"draft generation attempt {attempt}", exc) from exc
        result["rewrite_fact_brief"] = fact_brief
        result["rewrite_analysis_warning"] = fact_brief.get("analysis_warning", "")
        comparison = result.get("rewrite_comparison") or {}
        if not best_result or candidate_rank(result) > candidate_rank(best_result):
            best_result = result
        if comparison.get("passed", False):
            result["rewrite_attempts"] = attempt
            return result
        last_result = result

    assert best_result is not None
    comparison = best_result.get("rewrite_comparison") or {}
    best_result["rewrite_attempts"] = MAX_REWRITE_ATTEMPTS
    length_ratio = int(comparison.get("length_ratio") or 0)
    if comparison.get("non_length_quality_passed") and 80 <= length_ratio < 90:
        best_result["rewrite_warning"] = (
            f"二创稿已返回，但字数为原文的 {length_ratio}%，低于建议的90%；"
            "差异度和复用指标均已合格，可继续编辑或再次改写。"
        )
        best_result["rewrite_quality_status"] = "length_warning"
        best_result["rewrite_error"] = ""
        return best_result
    # A quality threshold miss should not discard a complete draft. Return the
    # best attempt with actionable metrics; reserve request failures for cases
    # where no draft could be generated at all.
    best_result["rewrite_warning"] = build_rewrite_quality_warning(comparison)
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
) -> dict:
    payload = {
        "model": minimax_model(),
        "messages": [
            {"role": "system", "content": "你只输出可解析 JSON。rewritten_script 按完整画面自然分段，不能为了字数把连续画面拆碎；每段建议 40 到 60 个中文字符，通常不超过 80 字，少于 25 字且不能独立成画面的段落应合并。用户选定的固定开头不受字数限制并必须原样保留。一个自然段内部不要换行，段落之间使用空行，不要在正文中添加段落序号。"},
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
        result, raw_script, style, preserve_rule, append_book_promotion, promotion_book_title
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


def choose_guozhijiliang_seed(person_name: str = "", event_angle: str = "") -> tuple[str, str]:
    person = person_name.strip()
    angle = event_angle.strip()
    if person and angle:
        return person, angle
    if not person:
        candidates = [
            seed for seed in GUOZHIJILIANG_STORY_SEEDS
            if seed[0] not in RECENT_GUOZHIJILIANG_PEOPLE
        ] or GUOZHIJILIANG_STORY_SEEDS
        person, default_angle = RANDOM.choice(candidates)
        RECENT_GUOZHIJILIANG_PEOPLE.append(person)
        del RECENT_GUOZHIJILIANG_PEOPLE[:-MAX_RECENT_GUOZHIJILIANG_PEOPLE]
        return person, angle or default_angle
    default_angle = next(
        (seed_angle for seed_person, seed_angle in GUOZHIJILIANG_STORY_SEEDS if seed_person == person),
        "从这个人物真实经历中选择一个最适合短视频叙事的核心事件",
    )
    return person, angle or default_angle


def choose_guozhijiliang_opening_guide() -> str:
    candidates = [
        guide for guide in GUOZHIJILIANG_OPENING_GUIDES
        if guide not in RECENT_GUOZHIJILIANG_OPENINGS
    ] or GUOZHIJILIANG_OPENING_GUIDES
    guide = RANDOM.choice(candidates)
    RECENT_GUOZHIJILIANG_OPENINGS.append(guide)
    del RECENT_GUOZHIJILIANG_OPENINGS[:-MAX_RECENT_GUOZHIJILIANG_OPENINGS]
    return guide


def guozhijiliang_script_stats(script: str) -> dict[str, int]:
    return {
        "chars": content_length(script),
        "paragraphs": len([line for line in str(script or "").splitlines() if line.strip()]),
    }


def guozhijiliang_opening_needs_rewrite(script: str) -> bool:
    lines = [line.strip() for line in str(script or "").splitlines() if line.strip()]
    if not lines:
        return True
    first_sentence = re.split(r"[。！？!?]", lines[0], maxsplit=1)[0].strip()
    opening = first_sentence[:70]
    if len(opening) < 8:
        return True
    if any(pattern in opening for pattern in WEAK_GUOZHIJILIANG_OPENING_PATTERNS):
        return True
    if re.match(r"^(在|当|那是|这是|有一位|很多人|如果说|我们都知道)", opening):
        return True
    return False


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
结尾必须用 2 到 3 个自然段、约 140 到 220 字完成《国之脊梁》的价值塑造，不能只提一次书名就结束。先承接本篇人物的具体选择和代价，再说清这本书能让读者看见哪些课本来不及展开的真实人生、理解怎样的选择与责任；接着落到家长和孩子共同阅读、成年人补上认知等具体阅读理由，最后给一句克制的行动引导。不要硬卖，不要喊“赶紧购买”，不要编造章节、人物数量、价格、优惠或赠品。

输出格式：
只返回 JSON，不要 Markdown，不要解释写作思路，不要列大纲，不要加小标题，不要加“镜头一、镜头二”。JSON 字段必须包含 title, person, event_angle, script。script 字段里只放按镜头分段后的正文。"""


def build_guozhijiliang_script_prompt_v2(person_name: str = "", event_angle: str = "") -> str:
    person_line, event_line = choose_guozhijiliang_seed(person_name, event_angle)
    opening_guide = choose_guozhijiliang_opening_guide()
    return f"""你是一名视频号爆款短视频文案策划，擅长写历史人物、爱国教育、大国叙事、卖书转化类文案，尤其擅长写《国之脊梁》《感动中国》风格的人物故事文案。

我要你围绕【人物名称】写一篇适合视频号发布的短视频口播文案，目标是提高播放量、完播率、转发率，并自然带出《国之脊梁》这本书。

人物名称：{person_line}
核心事件/角度：{event_line}
本篇独特点：必须从“{person_line}”这个人的真实反常识点出发，不要套其他人物也能用的通用开头。
本篇开头策略：{opening_guide}
目标书籍：《国之脊梁》
视频时长：4到5分钟
正文长度：1000到1300个中文字符，不能少于1000字。少于1000字必须继续扩写，不要提前收尾。
分镜段落：20到30段，每段建议35到65字。段落太少会导致视频太短、画面不够密，必须拆细动作、冲突、转折和情绪递进。

一、整体风格要求

不要写成人物百科。不要从“某某出生于某年”开始。不要平铺直叙介绍人物一生。不要写成新闻通稿、官方传记、公众号社论、学生作文。
要写成视频号爆款口播文案。风格要：狠、短、燃、抓人、有悬念、有爽感、有情绪、有画面、有故事。
文案要像一个情绪很足的人在给观众讲一个被埋没的英雄故事，而不是像 AI 在写人物简介。

二、前三秒要求

前三秒必须暴击。开头不能慢热，不能铺垫，不能介绍背景，不能描写环境、天气、时代氛围、人物外貌或普通状态。开头必须直接制造一个强冲突、强悬念、强反差，让观众立刻想知道“为什么”。
第一句话就是钩子，不能承担介绍任务。第一句话必须直接给事件爆点，必须是异常事实、异常动作、危险现场、巨大反差、结果倒放或未解悬念之一。
前三秒必须做到“三连击”：第一句给爆点，第二句加压或反转，第三句抛出观众必须追下去的问题。前三句里必须至少出现一个明确冲突词或动作词，例如扣下、炸掉、抹掉、失踪、拒绝、隐瞒、牺牲、封锁、审查、病危、坠毁、捐出、消失、不能回家、不能署名、被骂、被拦、被藏起来。
第一句话必须有“事情正在发生”的冲击感，不要只是“这个人很特殊”“这个故事很震撼”“他的一生不简单”。如果第一句删掉人物姓名后仍然能套到任何科学家身上，必须重写。
第一句话不要出现人物身份介绍，不要写成“某某是……”“他/她是……”“在中国科学史上……”“有这样一位……”“提到……很多人会想到……”“今天讲一个……”。
第一句话禁止写“在某年”“那个年代”“在某个地方”“寒风中”“夜色里”“一间实验室里”“一个普通清晨”这类背景、环境、氛围铺垫。不要先搭景，再讲事；必须先出事，再补背景。
第一句话不要先给结论和评价，比如“他很伟大”“他是民族脊梁”“他改变了中国”“他做出了巨大贡献”。这些放在开头会平。
强冲突开头示例，只学习力度和结构，不要照抄：他亲手建起的大桥，最后却要亲手炸掉。父亲去世那天，他明明活着，却不能回家奔丧。飞机坠毁前，他最后护住的不是自己，是那个公文包。她最大的功劳，是把自己的名字从功劳簿上抹掉。美国海关扣下他的行李时，真正害怕的不是箱子，是他回中国。
第一段前两句必须让人产生一个具体问题：他为什么这么做？这件东西为什么重要？这个结果怎么来的？这家人为什么沉默？这个名字为什么消失？
开头不要只写宏大概念，比如“中国芯片被卡脖子”“中国原子弹来之不易”“中国航天发展很艰难”“他为国家做出巨大贡献”。这种太普通，不够抓人。
开头要尽量从这个人物独有的具体画面、具体误区、具体反差切入。第一句话必须和“{event_line}”强相关，换成另一个人物就不成立。
禁止使用街访问答模板、百分比未知模板、泛泛“很多人不知道”模板。

可选开头方向，不要照抄：
1. 物件悬念：一只箱子、一份档案、一张图纸、一件病号服、一双旧鞋、一个饭盘，为什么能牵出国家命运？
2. 选择反常识：最该邀功的人为什么主动退后？最该回家的人为什么没有回家？最该活下去的人为什么先护住资料？
3. 结果倒放：先给出后来改变国家的结果，再倒回那个最不起眼、最没人理解的瞬间。
4. 亲情误解：亲人骂他、等他、误会他多年，最后才知道他不是不想说，而是不能说。
5. 普通画面反转：从食堂、病房、车间、田埂、桥边、银行柜台、戈壁风沙这样的普通画面进入，再反转出人物分量。
6. 名字缺席：从名单上没有他/她、档案里看不见他/她、庆功时站在后面切入。
前三秒的核心是：先给这个人物独有的反常识，再给冲突和悬念，不要先讲来龙去脉。背景最多从第三句开始补，而且只能为解释爆点服务。

三、故事结构要求

整篇文案按照下面结构写：
1. 暴击开头：用一句强冲突的话打碎观众认知。第一句直接写“发生了什么离谱/危险/反常的事”，不要写它发生在什么背景里。
2. 留下悬念：让观众产生疑问：为什么会这样？这个人到底是谁？
3. 人物登场：用一句有力量的话自然带出人物名字，不要固定套用“您听好这三个字”。可以用画面、结果、旁人误解、历史欠账来揭名，例如“这个被藏在功劳背后的人，叫朱光亚。”
4. 历史屈辱：写他当年被看不起、被封锁、被阻拦、被误解、被羞辱的场景。要具体，不要空泛。
5. 关键选择：写他放弃了什么。比如高薪、绿卡、世界顶级实验室、署名、家庭、荣誉、自由、健康、生命。
6. 炼狱过程：写他怎么熬过最难的阶段。要有具体画面：戈壁、算盘、手稿、病床、风沙、深夜的灯、破自行车、公文包、旧胶鞋、行李箱、实验室、母亲等待的门口。
7. 结果爆发：写原子弹爆炸、氢弹成功、核潜艇下水、导弹升空、资料被保住、巨款被捐出等关键结果。这里可以写得燃一点、爽一点。
8. 低调反差：写成功之后他没有邀功、没有署名、没有热搜、没有享受荣华，甚至主动隐藏自己。这部分要制造观众的亏欠感。
9. 观众共情：写“我们今天知道太多明星，却不知道这样的人”。让观众产生转发给孩子、家人、朋友的冲动。
10. 自然带书：最后自然引出《国之脊梁》。不要硬卖，不要写“赶紧买”。要让书成为这个情绪的承接和答案。

四、语言风格要求

语言必须适合视频号口播。要像真人说话，不要像书面文章。多用短句。多用反问。可以使用“您以为”“可真正狠的是”“这不是爽文”“您别划走”等口语表达，但不要把每篇都写成同一种开场和同一种揭名句。
禁止反复使用“您听好这三个字：某某”“请记住这个名字：某某”“这个人叫某某”这类模板句。人物姓名要顺着故事和画面自然出现。
可以适度夸张，可以有爽感，可以有攻击性，可以有情绪冲击。要优先追求高流量、强情绪、强钩子，而不是过度克制。
可以使用类似表达：“您被骗了几十年。”“他把自己从历史功劳簿上抹得干干净净。”“美国人最怕的不是一支军队，而是这个中国人回家。”“这口气，他咽了几十年。”“这不是爽文，这是那个年代真实发生过的事。”“他死后连热搜都没有，可他替14亿人挡住了最危险的威胁。”“他们才是中国孩子最该追的星。”
避免这些表达：悍然、方知、伟岸、至此、乃、赴汤蹈火、径直、再至、苍生、星光、壮烈史诗、强国气场、精神源泉、深受震撼、恩重如山。
不要把文案写成朗诵稿。不要把人物写成百科介绍。

五、故事化要求

必须围绕一个具体事件写，不要写人物一生简介。
写钱学森，不要泛泛写“中国航天之父”，要聚焦“美国海关扣下他的行李”。
写黄旭华，不要泛泛写“中国核潜艇之父”，要聚焦“父亲去世不能回家，母亲骂他三十年不孝”。
写郭永怀，不要泛泛写“两弹一星元勋”，要聚焦“飞机失事前用身体护住公文包”。
写林俊德，不要泛泛写“核试验专家”，要聚焦“生命最后一天穿病号服整理资料”。
写王承书，不要泛泛写“铀同位素分离专家”，要聚焦“主动要求抹掉自己的名字”。
写朱光亚，不要泛泛写“核武器专家”，要聚焦“写信召回52名留学生，后来又把自己从功劳簿上抹掉”。
写马旭，不要泛泛写“女空降兵”，要聚焦“穿15块钱胶鞋走进银行捐出1000万”。
写黄令仪，不要泛泛写“芯片卡脖子”，要聚焦“食堂里排队打饭的普通老太太，竟然是中国芯片最难时往前顶的人”。

六、细节要求

每篇文案必须有大量能拍出来、能配图、能搜素材的细节。
可以使用：旧胶鞋、破自行车、公文包、病号服、电脑、算盘、手稿、行李箱、抽屉、信封、实验室的灯、戈壁风沙、零下几十度、银行柜台、母亲的门口、烧毁的资料、密密麻麻的数据、深夜还亮着的窗户、食堂打饭的饭盘。
不要只写：他很伟大。他无私奉献。他是民族脊梁。他做出了巨大贡献。要通过动作和物品表现人物，而不是空喊口号。

七、情绪要求

情绪要一层比一层强。前面让人好奇。中间让人愤怒、憋屈、心疼。后面让人热血、敬佩、想转发。
结尾让人觉得：这些人才应该让孩子知道。
整篇文案的情绪路线是：好奇 → 震惊 → 憋屈 → 心疼 → 热血 → 敬佩 → 亏欠 → 转发/买书。

八、分镜分段要求

必须按短视频分镜逻辑分段。一个镜头一段。同一个镜头内部不要换行。
每一段必须能对应一个完整画面，方便后续 AI 配图、素材搜索、剪映剪辑。
不要出现只有几个字的空段。每段建议 35 到 65 字左右。
全文必须写满 20 到 30 个自然段，总字数必须达到 1000 到 1300 个中文字符；如果写到结尾发现段落不够，必须把关键事件过程、人物动作、现场细节、情绪转折拆成更多镜头段落，不要用空话凑字。
换段标准是：时间变化、地点变化、人物动作变化、画面主体变化、情绪节点变化。
不要按朗读断句分段，而要按画面分段。不要加“镜头一、镜头二”。直接用自然段输出。

九、带书转化要求

结尾必须用最后 2 到 3 个自然段、总计 140 到 220 个中文字符，自然带出《国之脊梁》并完成产品价值塑造。不能只写“如果家里有孩子，希望他认识这些人”，也不能只提一次书名就结束。
第一层先接住本篇人物带来的敬佩、心疼或亏欠感，从这个人的具体选择自然过渡到“还有更多这样的名字值得被看见”。
第二层说清产品价值：这本书不是一串人物简介，而是帮助读者看见课本来不及展开的真实人生，理解国家底气背后一个个普通人在关键时刻如何选择。只能做概括性表达，不得虚构具体章节、收录人数、作者背书或书中不存在的细节。
第三层给购买理由和阅读场景：家长可以和孩子一起读，让榜样不再只是一个抽象词；成年人也可以借它补上曾经错过的人物与历史。最后用一句克制但有行动力的话收束，让观众自然产生把书带回家、自己读或陪孩子读的冲动。
不要硬广，不要写“点击小黄车购买”“赶紧买”，不要编造价格、优惠、赠品、库存或购买渠道。避免连续喊口号和堆砌“伟大、震撼、民族脊梁”等空泛大词。

十、用户视角反审要求

写完整篇文案后，不要立刻输出。你必须先在心里模拟一个普通视频号用户的反应，从用户视角重新审视一遍。
请自检：前三秒会不会停下来？开头是不是太平？是不是一眼能猜到后面？有没有具体事件？有没有能记住的画面或物品？有没有足够反差？有没有爽点、痛点、亏欠感？用户会不会想转发给家人或孩子？是不是太像 AI？有没有太多大道理和空话？
如果答案不满意，必须重写。尤其注意：如果第一句话是在介绍人物身份、交代背景、下价值判断、喊口号，必须推翻重写。如果前三秒只是“某某很伟大”“某个领域被卡脖子”“某人做出巨大贡献”，必须推翻重写。如果开头像街采模板、问答模板、百科模板、人物通用模板，必须推翻重写。如果开头换成另一个科学家也能用，必须推翻重写。如果开头没有强悬念、强反差、强画面，必须推翻重写。如果全文只是在讲“他很伟大、他很奉献、国家很需要他”，必须推翻重写。如果普通用户听了前5秒就能猜到后面内容，必须推翻重写。

十一、最终输出要求

只输出经过自检后的完整文案。不要输出自检过程。不要解释写作思路。不要列大纲。不要加小标题。不要输出注意事项。不要说“以下是文案”。
文案必须按分镜自然分段。
script 字段正文必须是 1000 到 1300 个中文字符，20 到 30 个自然段。少于1000字或少于20段都视为不合格，必须重写后再输出。

程序解析要求：
你必须只返回严格 JSON，不要 Markdown。JSON 字段必须包含 title, person, event_angle, script。
script 字段里只放经过自检后的完整正文，按分镜自然段分段；不要在 script 里写自检过程、标题、小标题或说明。
person 字段填写：{person_line}
event_angle 字段填写：{event_line}
title 字段填写 2 到 9 个字的项目标题，不要标点。
"""


def generate_guozhijiliang_script(person_name: str = "", event_angle: str = "") -> dict:
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY is not configured")

    selected_person, selected_angle = choose_guozhijiliang_seed(person_name, event_angle)
    result: dict = {}
    script = ""
    stats = {"chars": 0, "paragraphs": 0}
    retry_note = ""
    for attempt in range(2):
        prompt = build_guozhijiliang_script_prompt_v2(selected_person, selected_angle) + retry_note
        payload = {
        "model": minimax_model(),
            "messages": [
                {"role": "system", "content": "你只输出可解析 JSON。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.85,
            "top_p": 0.9,
            "max_tokens": 8000,
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
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"MiniMax API {exc.code}: {error_body}") from exc

        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        result = json.loads(extract_json(str(content)))
        script = clean_rewritten_script("", str(result.get("script") or result.get("rewritten_script") or "")).strip()
        stats = guozhijiliang_script_stats(script)
        opening_needs_rewrite = guozhijiliang_opening_needs_rewrite(script)
        if (
            stats["chars"] >= MIN_GUOZHIJILIANG_SCRIPT_CHARS
            and stats["paragraphs"] >= MIN_GUOZHIJILIANG_SCRIPT_PARAGRAPHS
            and not opening_needs_rewrite
        ):
            break
        if opening_needs_rewrite:
            retry_note = (
                "\n\n【重写要求】刚才生成的开头不合格，前三秒留人能力不够。"
                "第一句话不能介绍人物、交代背景、下价值判断或写氛围，必须直接给强冲突事件。"
                "前三句必须形成三连击：爆点、加压或反转、抛出疑问。"
                "第一句必须让人立刻想问：为什么会这样？他/她接下来怎么办？"
            )
        else:
            retry_note = (
                "\n\n【重写要求】刚才生成的正文太短或段落太少，不合格。"
                f"必须扩写到 {MIN_GUOZHIJILIANG_SCRIPT_CHARS} 到 {MAX_GUOZHIJILIANG_SCRIPT_CHARS} 个中文字符，"
                f"{MIN_GUOZHIJILIANG_SCRIPT_PARAGRAPHS} 到 {MAX_GUOZHIJILIANG_SCRIPT_PARAGRAPHS} 个自然段。"
                "补充关键事件过程、人物代价、现场细节、情绪递进和自然带书，不要用空话凑字。"
            )

    if not script:
        raise RuntimeError("MiniMax response does not contain script")
    return {
        "title": normalize_auto_title(str(result.get("title") or ""), script),
        "person": str(result.get("person") or selected_person).strip(),
        "event_angle": str(result.get("event_angle") or selected_angle).strip(),
        "script": script,
        "script_chars": stats["chars"],
        "script_paragraphs": stats["paragraphs"],
        "provider": minimax_model(),
    }


def extract_json(text: str) -> str:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("MiniMax response does not contain JSON")
    return match.group(0)


def keywords_from_text(text: str) -> dict[str, list[str]]:
    people = [p for p in PERSON_HINTS if p in text]
    scenes = [s for s in SCENE_HINTS if s in text]
    eras = [e for e in ERA_HINTS if e in text]
    # Never derive tags by slicing arbitrary narration fragments. If AI visual
    # analysis is unavailable, leave unknown tags empty instead of inventing
    # misleading labels from the script text.
    keywords = list(dict.fromkeys(people + scenes + eras))
    return {"people": people, "scene": scenes, "era": eras, "keywords": keywords}


def is_meaningful_shot_text(text: str) -> bool:
    cleaned = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
    return bool(cleaned)


SHOT_VISUALS_BATCH_SIZE = 5
LOGGER = logging.getLogger(__name__)
SHOT_TAG_PUNCTUATION = re.compile(r"[\s，。！？、；：,.!?;:\"'()\[\]{}<>]+")
SHOT_TAG_BAD_PARTS = (
    "画面", "镜头", "旁白", "体现", "展现", "展示", "表现", "强调", "需要",
    "应该", "相关", "历史画面", "纪实画面", "老照片", "历史档案",
)
SHOT_TAG_BAD_PREFIXES = ("的", "了", "在", "把", "被", "将", "为", "以", "和", "与")
SHOT_TAG_BAD_SUFFIXES = ("的", "了", "着", "过", "中", "时", "后", "前")


def clean_shot_visual_terms(values: list, *, max_length: int) -> list[str]:
    cleaned: list[str] = []
    for value in values or []:
        tag = re.sub(r"\s+", "", str(value or "")).strip()
        if not tag:
            continue
        if len(tag) > max_length:
            continue
        if SHOT_TAG_PUNCTUATION.search(tag):
            continue
        if tag.startswith(SHOT_TAG_BAD_PREFIXES) or tag.endswith(SHOT_TAG_BAD_SUFFIXES):
            continue
        if any(part in tag for part in SHOT_TAG_BAD_PARTS):
            continue
        if tag in cleaned:
            continue
        cleaned.append(tag)
    return cleaned[:5]


def _build_shot_visuals_prompt(shot_items: list[dict], full_script: str) -> str:
    return f"""你是短视频分镜画面设计专家。请根据每个分镜的旁白文字，生成画面描述、搜索关键词和素材匹配标签。

规则：
1. 画面描述（visual_need）应描述这个镜头应该出现什么画面，指导图片搜索方向，不要复述旁白内容。每个镜头只能写一个明确、固定的场景；旁白涉及多个场景、地点、时刻、动作或事件时，直接选定最能表达核心的一种，不得写“A或者B”“时而A时而B”“从A切换到B”等备选、并列或切换画面。
2. 搜索关键词（search_keywords）应是可以直接用于中文图片搜索的词组，2-3个关键词，每个2-8个字。
3. 主体标签（object_tags）：画面中应该出现的人、物、建筑、标志物等核心主体，1-3个词，每个2-6字。必须是具体可识别的对象，如"钱学森""核潜艇""火车""纪念碑"。
4. 场景标签（scene_tags）：画面发生的地点、环境或氛围，1-2个词，每个2-6字。必须是具体场景，如"实验室""会议室""戈壁滩""码头"。如果无法确定具体场景，留空数组。
5. 关键词（keywords）：独立判断这个画面应该体现什么，1-3个词，每个2-8字。不要从旁白中提取或改写词汇，要站在图片搜索的角度，想想搜什么词能找到这张图。例如旁白说"他毅然放弃国外的优厚待遇回到祖国"，画面可能是"归国科学家走下飞机"，关键词应该是"归国科学家""留学回国"，而不是"优厚待遇""毅然放弃"。
6. 如果旁白明确描述某个具体人物、事件或场景，标签应聚焦该内容。
7. 禁止使用"老照片""历史档案""历史画面""纪实画面""相关画面"等泛化无意义词。
8. 画面描述要具体、可搜索，避免"相关画面""历史画面""纪实画面"等泛化描述。构图只保留核心主体、一个关键动作和必要环境，不要试图把旁白中的所有人物、物品、事件和象征元素都塞进同一画面。
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
    """Use MiniMax to generate visual_need and search_keywords for each shot."""
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
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
        "model": minimax_model(),
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
            body = None
            last_error: Exception | None = None
            for attempt in range(3):
                req = urllib.request.Request(
                    f"{minimax_endpoint().rstrip('/')}/chat/completions",
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
                raise RuntimeError(f"MiniMax 分镜画面描述连续请求失败：{last_error}")
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
                search_keywords = clean_shot_visual_terms(item.get("search_keywords") or [], max_length=12)
                object_tags = clean_shot_visual_terms(item.get("object_tags") or [], max_length=8)
                scene_tags = clean_shot_visual_terms(item.get("scene_tags") or [], max_length=8)
                keywords = clean_shot_visual_terms(item.get("keywords") or [], max_length=10)
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
        except Exception as exc:
            indexes = [shot.get("shot_index") for shot in batch]
            LOGGER.exception("MiniMax shot visual batch failed for shots %s: %s", indexes, exc)
            # Isolate a malformed item instead of losing every shot in the batch.
            if len(batch) > 1:
                for shot in batch:
                    all_visuals.update(ai_generate_shot_visuals([shot], full_script))
                continue
            # A single item already exhausted its retries. Keep a meaningful
            # fallback and derive any safe local tags instead of emptying all fields.
            for shot in batch:
                shot_id = str(shot["shot_index"])
                voice_text = str(shot.get("voice_text") or "").strip()
                local_tags = keywords_from_text(voice_text)
                objects = list(local_tags.get("people") or [])
                scenes = list(local_tags.get("scene") or [])
                all_visuals[shot_id] = {
                    "visual_need": f"根据旁白呈现具体历史纪实画面：{voice_text[:80]}",
                    "person_gender": "unknown",
                    "person_names": [],
                    "person_description": "",
                    "search_keywords": list(dict.fromkeys([*objects, *scenes]))[:3],
                    "object_tags": objects[:3],
                    "scene_tags": scenes[:2],
                    "keywords": list(local_tags.get("keywords") or [])[:3],
                }

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


def cover_title_score(line1: str, line2: str, script: str) -> int:
    combined = f"{line1}{line2}"
    score = 0
    score += sum(5 for word in COVER_TITLE_ATTRACTION_WORDS if word in combined)
    score += sum(3 for char in combined if char.isdigit())
    score += 6 if any(word in combined for word in ("却", "竟", "不能", "最后", "凭什么", "到底")) else 0
    score += 10 if any(word in combined for word in TITLE_OPEN_LOOP_WORDS) else 0
    score += 8 if any(word in line2 for word in ("却", "竟", "反而", "偏偏", "为何", "为什么", "到底", "凭什么", "谁")) else 0
    score += 4 if line1 in script or line2 in script else 0
    score += 3 if 8 <= len(combined) <= 16 else 0
    score -= sum(8 for pattern in WEAK_COVER_TITLE_PATTERNS if pattern in combined)
    score -= sum(12 for ending in TITLE_SUMMARY_ENDINGS if ending in line2)
    return score


def extract_json_array(text: str) -> str:
    match = re.search(r"\[.*\]", text, flags=re.S)
    if not match:
        raise ValueError("MiniMax response does not contain JSON array")
    return match.group(0)


def parse_title_candidates(content: str) -> list[dict]:
    parsed = json.loads(extract_json_array(content))
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def generate_viral_title(script: str) -> dict:
    """Generate a two-line cover title; retry instead of truncating overlong lines."""
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        return {"line1": "", "line2": "", "full_title": "", "error": "MINIMAX_API_KEY is not configured"}

    base_prompt = (
        "你是短视频封面标题策划。请通读整篇文案，生成两行式封面标题。"
        "\n\n【选择标题角度】"
        "\n先提炼全文核心命题：主人公是谁、完成了什么任务、持续多久、付出什么代价、最终出现什么结果。"
        "\n再提炼文中的局部冲突、反常选择、关键动作和具体后果，并把它们与全文核心命题比较。"
        "\n最终应选择普通观众最容易一眼理解、信息含量最高、最值得继续看的真实事实。局部瞬间并不天然优于全文核心事实。"
        "\n当人物的特殊身份、极端时长、任务风险或人生代价本身已经足够反常时，可以直接围绕这些核心事实起标题，不得因为它属于人物身份或全文概括就降级。"
        "\n如果全文没有足够强的局部悬念，可以写有具体身份、数字、任务或结果的事实概括标题；宁可准确、清楚、有信息量，也不要硬凑反差和悬念。"
        "\n不得只抓红烛、眼泪、旧照片、低头等装饰性细节，而忽略贯穿全文的重大身份、任务、时间跨度和人生代价。"
        "\n\n【真实性】"
        "\n标题的身份、数字、动作、人物关系、先后顺序和因果必须能在原文中找到直接依据。"
        "\n不得添加原文没有的独自、立即、马上、转身、掉头等修饰语，不得把正常反应包装成反常选择。"
        "\n禁止使用主体不明的代词对话，禁止把不敢抬头、低下头、红了眼、流泪、沉默等普通情绪反应当成核心爆点。"
        "\n两行脱离正文后也必须逻辑完整、主体清楚。疑问或反差只能来自事实本身，不能依靠却、竟、没、到底等词硬造。"
        "\n\n【表达要求】"
        "\n标题要口语化、具体、简洁，避免空洞评价、口号、人物颂词、中心思想和没有事实信息的情绪词。"
        "\n第一行和第二行可以组成冲突、反差、悬念，也可以共同构成一条有力度的核心事实概括，不强制套用固定结构。"
        "\n每行1到9个字，不得有任何标点；超过9字必须重新概括，严禁直接截断。"
        "\n\n【候选与依据】"
        "\n一次生成12组不同角度的候选。候选必须同时覆盖全文核心事实和局部关键事件，不得全部围绕同一种小细节。"
        "\n第1组必须是综合全文后判断的最强标题。"
        "\n每组必须包含 first_line、second_line、style、evidence_quote。"
        "\nstyle 只能是悬念型、反差型、冲突型、心疼型、爽感型、亏欠型、误区型、画面型、事实型之一。"
        "\nevidence_quote 必须从原文逐字复制6到80字，直接支撑标题两行的身份、数字、动作和人物关系，不得改写或编造。"
        "\n\n【输出前自检】"
        "\n比较全文核心事实与局部瞬间，确认没有用次要细节遮住更强的身份、时长、任务或代价。"
        "\n检查标题是否脱离正文也能看懂，是否有真实信息，是否存在空洞情绪、假反差、事实篡改或无效依据。"
        "\n不合格必须重写。只返回JSON数组，不要Markdown或解释。"
        '\n返回格式：[{"first_line":"第一行","second_line":"第二行","style":"事实型","evidence_quote":"原文直接依据"}]'
        "\n\n下面是文案内容："
        f"\n{script[:6000]}"
    )

    last_error = ""
    try:
        for attempt in range(1, 4):
            retry_note = f"\n\n上一版不合格，具体原因：{last_error}。请针对这些原因重新生成，不要重复上一版的问题。" if last_error else ""
            if attempt == 3:
                retry_note += (
                    "\n\n这是最后一次生成。不要再从零散小细节中硬凑悬念。"
                    "请回到全文核心命题，优先使用有原文依据的特殊身份、明确数字、时间跨度、核心任务、重大结果或人生代价。"
                    "允许输出事实型概括标题，不强制制造疑问或反差。"
                )
            payload = {
        "model": minimax_model(),
                "messages": [
                    {"role": "system", "content": "你只输出可解析JSON。"},
                    {"role": "user", "content": base_prompt + retry_note},
                ],
                "temperature": 0.8,
                "top_p": 0.9,
                "max_tokens": 1200,
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
            candidates = parse_title_candidates(str(content))
            last_error = "12组标题里没有合格候选"
            valid_candidates = []
            rejection_details: list[str] = []
            seen_titles: set[tuple[str, str]] = set()
            for candidate_index, item in enumerate(candidates):
                line1 = strip_title_punctuation(item.get("first_line") or item.get("line1") or "")
                line2 = strip_title_punctuation(item.get("second_line") or item.get("line2") or "")
                evidence_quote = str(item.get("evidence_quote") or "").strip()
                title_key = (line1, line2)
                reasons: list[str] = []
                if not 1 <= len(line1) <= 9 or not 1 <= len(line2) <= 9:
                    reasons.append("单行超过9字或为空")
                if title_key in seen_titles:
                    reasons.append("候选标题重复")
                reasons.extend(cover_title_rejection_reasons(line1, line2, script, evidence_quote))
                seen_titles.add(title_key)
                if reasons:
                    rejection_details.append(f"{line1}/{line2}：{'、'.join(dict.fromkeys(reasons))}")
                    continue
                valid_candidates.append({
                    "line1": line1,
                    "line2": line2,
                    "full_title": f"{line1} {line2}",
                    "style": str(item.get("style") or "").strip(),
                    "evidence_quote": evidence_quote,
                    "score": cover_title_score(line1, line2, script),
                    "model_rank": candidate_index,
                })
            if valid_candidates:
                best = max(valid_candidates, key=lambda item: (item["score"], -item["model_rank"]))
                best.pop("score", None)
                best.pop("model_rank", None)
                return best
            if rejection_details:
                last_error = "；".join(rejection_details[:4])
        return {"line1": "", "line2": "", "full_title": "", "error": last_error or "Title generation failed"}
    except Exception as exc:
        return {"line1": "", "line2": "", "full_title": "", "error": str(exc)[:200]}


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


def fallback_publish_assistant(script: str) -> dict:
    sentences = split_sentences(script)
    first = sentences[0] if sentences else script[:40]
    short_title = (
        strip_title_punctuation(first)[:MAX_PUBLISH_SHORT_TITLE_LENGTH]
        or "这个故事值得被看见"
    )
    description_source = " ".join(sentences[:3]) if sentences else script
    description = clean_publish_description(description_source)
    return {"short_title": short_title, "description": description}


def generate_publish_assistant(script: str) -> dict:
    """Generate a platform-ready description and a punctuation-free short title."""
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        return fallback_publish_assistant(script)

    prompt = (
        "你是短视频发布运营助手。请根据下面的中文口播文案，生成发布用内容。"
        "\n\n要求："
        "\n1. short_title 是一句话短标题，8 到 16 个汉字，不要任何标点符号，绝对不能超过16个字。"
        "\n2. short_title 要有悬念或反差，但必须忠于文案事实，不要标题党造假。"
        "\n3. description 是视频描述，80 到 140 个汉字，适合发视频号/抖音/小红书。"
        "\n4. description 开头要吸引人，点出故事冲突、反差、情绪爆点或评论点，让人想点开看完。"
        "\n5. description 只写视频内容本身，不要介绍书，不要提书名，不要写读书感受，不要出现买书、带书、小黄车、家长购买等表达。"
        "\n6. description 不要写成片头文案，不要写“本视频讲述”，不要堆砌空话。"
        "\n7. 只返回 JSON，不要 Markdown，不要解释。"
        "\n\n文案内容："
        f"\n{script[:1200]}"
        "\n\n返回格式："
        '\n{"short_title": "一句话短标题", "description": "吸引人的视频描述"}'
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
        short_title = strip_title_punctuation(result.get("short_title", ""))[:MAX_PUBLISH_SHORT_TITLE_LENGTH]
        description = clean_publish_description(result.get("description", ""))
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
            "search_keywords": [],
            "selected_asset_id": None,
            "asset_source": None,
            "match_score": 0,
            "status": "no_match",
        })
        cursor += duration

    # Use MiniMax to generate more accurate visual descriptions and search keywords
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
