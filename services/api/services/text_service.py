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
MIN_REWRITE_DIFFERENCE = 75
MAX_REWRITE_CONTINUOUS_REUSE = 10
MAX_REWRITE_SOURCE_PHRASE_REUSE = 18
MAX_REWRITE_SENTENCE_IMITATION = 25
MAX_REWRITE_STRUCTURE_SIMILARITY = 72
MAX_REWRITE_DETAIL_DISTRIBUTION_SIMILARITY = 75
MAX_REWRITE_ATTEMPTS = 2
MAX_REWRITE_ANALYSIS_ATTEMPTS = 2
MAX_REWRITE_ANALYSIS_REQUEST_ATTEMPTS = 2
REWRITE_COMPRESSION_WARNING_RATIO = 0
SUPPORTED_PROMOTION_BOOK_TITLES = ("女性人物传记", "历史深处的民国", "国之脊梁")
SENSITIVE_AI_SCRIPT_PEOPLE = ("孙中山", "孙文", "中山先生", "周恩来", "周总理")
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
BOOK_SCRIPT_STORY_SEEDS = {
    "女性人物传记": [
        ("杨绛", "在时代动荡、家庭离散与亲人相继离去中，她如何守住写作、尊严和内心秩序"),
        ("陆小曼", "从万众瞩目的才女到承受婚姻争议与生活困顿，她如何面对选择带来的代价"),
        ("张爱玲", "在成名、爱情受挫与远走他乡之间，她如何用清醒和写作安放自己"),
        ("林徽因", "身体长期抱病，她仍奔波考察古建筑，在家庭、事业和时代压力中坚持自己的选择"),
        ("三毛", "经历漂泊、爱情与失去之后，她如何一次次离开熟悉生活，又重新寻找人生出口"),
    ],
    "历史深处的民国": [
        ("李鸿章", "在晚清内外交困中主持洋务与外交，一个被骂了一百多年的人究竟面对怎样的残局"),
        ("袁世凯", "从晚清重臣到民国大总统，再到称帝失败，权力选择如何改变他和时代的走向"),
        ("宋教仁", "在议会政治刚露出希望时遇刺，他未完成的制度理想如何改变民国走向"),
        ("蔡锷", "面对袁世凯称帝，他如何离开北京、发动护国战争并付出生命代价"),
        ("黄兴", "革命屡败、战友牺牲，他为何仍站在最危险的位置继续推动起义"),
        ("张作霖", "从东北崛起到皇姑屯事件，他如何在列强、中央与地方势力之间作出选择"),
        ("张学良", "从东北易帜到西安事变，一个决定如何改变国家命运，也改变他此后的人生"),
    ],
}
RECENT_GUOZHIJILIANG_PEOPLE: list[str] = []
RECENT_BOOK_SCRIPT_PEOPLE: dict[str, list[str]] = {
    title: [] for title in BOOK_SCRIPT_STORY_SEEDS
}
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
    expected = set(indexed_cards)

    covered = {
        number for number in (_fact_card_number(item) for item in audit.get("covered_cards", []))
        if number in expected
    }
    partial_items = audit.get("partial_cards") if isinstance(audit.get("partial_cards"), list) else []
    missing_items = audit.get("missing_cards") if isinstance(audit.get("missing_cards"), list) else []
    partial = {number for number in (_fact_card_number(item) for item in partial_items) if number in expected}
    missing = {number for number in (_fact_card_number(item) for item in missing_items) if number in expected}
    # Every fact direction must survive. Mergeable cards may be compressed into
    # neighbouring passages, but they may not disappear.
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
    return {
        "fact_coverage_passed": bool(expected) and not failed,
        "timeline_order_passed": timeline_order_passed,
        "timeline_order_issues": order_items,
        "emotional_quality_passed": emotional_quality_passed,
        "emotional_issues": emotional_items,
        "covered_fact_cards": sorted(covered),
        "expected_fact_cards": len(expected),
        "missing_fact_cards": missing_fact_cards,
        "fact_coverage_summary": str(audit.get("summary") or "").strip(),
    }


def apply_rewrite_fact_coverage_quality(result: dict, coverage: dict) -> dict:
    comparison = result.get("rewrite_comparison") or {}
    comparison.update(coverage)
    if "length_passed" not in comparison:
        comparison["length_passed"] = True
    comparison["compression_warning"] = False
    comparison["passed"] = (
        bool(comparison.get("non_length_quality_passed"))
        and bool(comparison.get("fact_coverage_passed"))
        and comparison.get("timeline_order_passed") is not False
        and comparison.get("emotional_quality_passed") is not False
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
    if comparison.get("fact_coverage_passed") is False:
        missing_cards = comparison.get("missing_fact_cards") or []
        card_summaries = []
        for item in missing_cards[:8]:
            if isinstance(item, dict):
                card_summaries.append(f"素材卡 {item.get('card')}：{item.get('missing') or item.get('fact')}")
        issues.append("重要事实覆盖不完整：" + ("；".join(card_summaries) or "存在未写入的素材卡"))
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
1. 每个独立事件一张 material_card，按真实时间排序。删除后会破坏主线、因果、人物关系、关键选择、结果或数字的，标为 must；重复背景、履历和同类成就标为 mergeable。
2. must 卡写清人物、事件、原因、动作、结果、代价和关键数字；mergeable 卡保持简短。所有卡都必须进入成稿，mergeable 只表示允许与相邻事实合并表达，不表示允许删除。不要为了凑数量拆卡。
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
  "material_cards": [{{"id": 1, "priority": "must", "emotion_focus": true, "time": "时间阶段", "person": "人物姓名", "fact": "中性事实", "details": "原因、动作、结果、代价、数字", "emotional_stakes": "原文明示的处境、选择、代价或关系变化"}}, {{"id": 2, "priority": "mergeable", "emotion_focus": false, "time": "时间阶段", "person": "人物姓名", "fact": "可合并的背景或履历", "details": "必要信息"}}],
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
        legacy_beat = str(normalized_item.pop("emotional_beat", "") or "").strip()
        if legacy_beat:
            legacy_emotional_nodes.append({"card": normalized_item.get("id"), "beat": legacy_beat})
        for optional_field in ("emotional_stakes", "relationship_change"):
            if not str(normalized_item.get(optional_field) or "").strip():
                normalized_item.pop(optional_field, None)
        expansion_level = str(normalized_item.get("expansion_level") or "").strip().lower()
        if expansion_level not in {"focus", "support", "brief"}:
            expansion_level = "support" if str(normalized_item.get("priority") or "").strip().lower() == "must" else "brief"
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
    mergeable_count = sum(
        1 for item in structured_cards
        if str(item.get("priority") or "").strip().lower() == "mergeable"
    )
    priority_balance_passed = len(structured_cards) < 6 or mergeable_count > 0
    focus_count = sum(1 for item in structured_cards if item.get("expansion_level") == "focus")
    maximum_focus_count = max(1, (len(structured_cards) * 3 + 4) // 5)
    expansion_balance_passed = len(structured_cards) < 4 or focus_count <= maximum_focus_count
    # A usable brief needs facts and identifiable subjects. Section planning,
    # focus balance and mergeable-card ratios are writing aids, not reasons to
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

    protagonists = [person for person in PERSON_HINTS if person in original_source]
    card_count = len(cards)
    fallback_material_cards = []
    must_signal = re.compile(
        r"(决定|拒绝|选择|回国|离开|被捕|牺牲|死亡|去世|离婚|成功|完成|发明|研制|解决|结果|真相|获奖)"
    )
    for index, card in enumerate(cards, 1):
        is_boundary = index == 1 or index == card_count
        priority = "must" if is_boundary or must_signal.search(card) else "mergeable"
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
                "重复背景和同类履历仍标记 mergeable，不要增加传播分析、情绪设计或叙事计划。"
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
        if str(item.get("priority") or "must").lower() != "mergeable" and item.get("id") is not None
    ]
    focus_ids = emotional_ids or must_ids or [
        item.get("id") for item in cards
        if item.get("expansion_level") == "focus" and item.get("id") is not None
    ]
    all_ids = [item.get("id") for item in cards if item.get("id") is not None]
    return [
        {"strategy": "核心冲突", "focus_cards": focus_ids[:2] or all_ids[:2], "guidance": "把阻力如何出现、人物如何应对写成因果链；背景压进动作发生的当下，不作生平铺陈。"},
        {"strategy": "关键选择", "focus_cards": focus_ids[-2:] or all_ids[-3:], "guidance": "以几次不可回避的选择为段落支点，重点写选择前的处境和选择后的实际变化，弱化原文惯用高潮。"},
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
        "资料卡时间线已经核验。固定开头之后按 material_cards 的 id 从小到大、按真实时间从早到晚推进。"
        if timeline_verified else
        "这是保底资料卡，卡片 id 仅代表原文出现顺序，不代表真实时间。先依据 time、人物年龄、事件因果和明确年份恢复真实时间线，再从早到晚写作；不得照抄卡片编号顺序。"
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
            "本轮仍须覆盖全部资料卡，但不能恢复原文的句子和段落。"
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
        if comparison.get("timeline_order_passed") is False:
            issues = comparison.get("timeline_order_issues") or []
            issue_text = "；".join(
                str(item.get("reason") or item.get("issue") or item)
                for item in issues[:8]
                if isinstance(item, dict)
            )
            retry_instruction += (
                f"\n【本轮必须修正时间线】{issue_text or '上一版没有按素材卡编号顺叙'}。"
                + (
                    "固定开头之外，从最早的素材卡开始，严格按编号从小到大推进；"
                    if timeline_verified else
                    "卡片编号未经时间核验，不得照编号写；先根据年份、年龄和因果恢复真实时间，再从早到晚推进；"
                )
                + "不得提前透露后期结果，不得讲到后期再跳回早年。"
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
- 时间线：{chronology_instruction}只有固定开头本身位于后期结果时，才允许用一句承接语回到最早节点，之后始终顺叙。
- 所有资料卡都必须保留事实方向。must 卡完整展开；mergeable 卡可以和相邻内容合并概括，但不能删除。
- emotion_focus=true 的卡是全篇最有情绪价值的事实节点。围绕 emotional_stakes 展开人物处境、选择、动作、实际代价、关系变化和他人反应；只能使用卡片依据，不得虚构煽情。
- 不设目标字数和篇幅上下限。篇幅服从主线和事实表达：关键节点写透，普通履历简洁合并，既不为压缩而删事实，也不为凑字数填空话。

【核心创作规则】
{creative_guidelines}

【本次任务】
- 文案风格：{style}
- 候选稿 {attempt}/{MAX_REWRITE_ATTEMPTS}，本稿叙事策略：{narrative_strategy['strategy']}
- 策略说明：{narrative_strategy['guidance'] or '按该观察角度重新分配详略，但保持真实时间顺序。'}
- 本稿表达指纹：{expression_profile['name']}。{expression_profile['guidance']}
- 优先展开情绪与主线素材卡：{json.dumps(narrative_strategy['focus_cards'], ensure_ascii=False)}。其他资料卡仍须保留事实方向，mergeable 内容只允许合并表达。
- 不得把资料卡机械写成自然段，也不得让段落数量、段落长短和信息落点与原文形成一一对应。
- verified_quotes 之外的对话、演讲和遗言只能转成间接叙述，不得保留或仿写引号内原话。
- 固定开头必须一字不改并单独成段：{opening_hook}
- 后续正文必须自然承接固定开头，不能另起钩子；固定开头不参与重复率和总体重构度计算。
- 带书规则：{conversion_instruction}
{retry_instruction}

【输出】
只返回可解析 JSON，字段为 title、hook、rewritten_script、script_style。
rewritten_script 只能包含完整成稿正文，不得混入原文、分析、说明、标题标签或段落序号。
输出前只检查：固定开头是否原样保留；全部资料卡的事实方向是否保留；情绪重点是否来自卡片依据；是否按真实时间顺叙；是否存在资料卡以外的事实。不要检查字数。

【事实资料卡】
<fact_brief>{fact_brief_json}</fact_brief>
"""
    return prompt


def request_minimax_rewrite_fact_coverage(
    fact_brief: dict,
    rewritten_script: str,
    api_key: str,
    protected_opening: str = "",
) -> dict:
    cards = fact_brief.get("material_cards")
    if not isinstance(cards, list) or not cards:
        return {
            "fact_coverage_passed": True,
            "timeline_order_passed": True,
            "timeline_order_issues": [],
            "emotional_quality_passed": True,
            "emotional_issues": [],
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
    }
    prompt = f"""你只检查资料卡内容是否被完整保留以及时间顺序，不评价成稿新增内容、文采、结构或篇幅，也不要因为换了说法就判定缺失。

审核标准：
1. 检查完整成稿，包括 protected_opening。逐张审核全部资料卡，不能漏号。must 卡完整展开；mergeable 卡允许与相邻内容合并概括，但核心事实方向必须出现，不能因写得简短就判缺失。
2. 卡片的核心事件、人物、原因、结果和关键数字已表达即为 covered；只有会改变事实方向的缺失才标 partial，整项未出现才标 missing。
3. 成稿可以加入资料卡之外的补充内容、概括、评价、转场和带书文案；不要检查、记录或处罚任何新增内容。唯一的事实覆盖要求是资料卡中的内容不能遗漏。
4. 对 emotion_focus=true 的卡，检查成稿是否突出该卡已有的处境、选择、动作、实际代价、关系变化或他人反应，形成情绪重点。
5. 不以字数、措辞或细节多少判定，不要求文案复述资料卡原句。
6. timeline_verified=true 时检查各事件是否按编号顺叙；false 时根据年份、年龄和因果检查。固定开头位于后期时，正文一句回到早年不算乱序。
7. 只输出 JSON，不使用 Markdown。

输出格式：
{{
  "covered_cards": [1, 2],
  "partial_cards": [{{"card": 3, "missing": "缺少的原因、结果、动作或关键细节"}}],
  "missing_cards": [{{"card": 4, "missing": "整项事件未出现"}}],
  "emotional_quality_passed": true,
  "emotional_issues": [],
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
            {"role": "system", "content": "你只检查核心事实覆盖和时间顺序，只输出可解析 JSON。"},
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
            )
        except Exception as exc:
            result["rewrite_audit_warning"] = (
                f"事实审稿暂不可用，已保留本地篇幅与重复率检查：{type(exc).__name__}: {str(exc)[:180]}"
            )
            coverage = {
                "fact_coverage_passed": True,
                "timeline_order_passed": True,
                "timeline_order_issues": [],
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


def choose_book_script_seed(
    book_title: str,
    person_name: str = "",
    event_angle: str = "",
) -> tuple[str, str]:
    bare, _ = normalize_sales_book_title(book_title)
    if bare not in SUPPORTED_PROMOTION_BOOK_TITLES:
        raise ValueError(f"Unsupported promotion book: {bare}")
    person = person_name.strip()
    angle = event_angle.strip()
    if any(name in person or name in angle for name in SENSITIVE_AI_SCRIPT_PEOPLE):
        person = ""
        angle = ""
    if bare == "国之脊梁":
        return choose_guozhijiliang_seed(person, angle)

    seeds = BOOK_SCRIPT_STORY_SEEDS[bare]
    if not person:
        recent = RECENT_BOOK_SCRIPT_PEOPLE[bare]
        candidates = [seed for seed in seeds if seed[0] not in recent] or seeds
        person, default_angle = RANDOM.choice(candidates)
        recent.append(person)
        del recent[:-min(MAX_RECENT_GUOZHIJILIANG_PEOPLE, len(seeds))]
        return person, angle or default_angle
    default_angle = next(
        (seed_angle for seed_person, seed_angle in seeds if seed_person == person),
        "从这个人物的真实经历中，选择一个最能体现时代处境、个人选择和实际代价的核心事件",
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
结尾必须用 2 到 3 个自然段、约 140 到 220 字完成《国之脊梁》的价值塑造，不能只提一次书名就结束。让本篇人物的具体选择和代价自然承接到这本书能补全的真实人生与选择责任，再自然落到家长和孩子共同阅读、成年人补上认知等阅读理由，并用一句克制的行动引导收束。不要在成稿中写成“先说、再说、最后说”的步骤结构；不要硬卖，不要喊“赶紧购买”，不要编造章节、人物数量、价格、优惠或赠品。

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


def build_book_script_prompt(
    book_title: str,
    person_name: str = "",
    event_angle: str = "",
) -> str:
    bare, formatted = normalize_sales_book_title(book_title)
    person_line, event_line = choose_book_script_seed(bare, person_name, event_angle)
    if bare == "国之脊梁":
        return build_guozhijiliang_script_prompt_v2(person_line, event_line)

    if bare == "女性人物传记":
        subject_rule = (
            "选择真实女性人物，重点不是罗列成就，而是讲她在爱情、婚姻、家庭、事业、自由、"
            "名声或失去面前遇到的难处，以及她作出的选择和承担的真实代价。"
            "写出女性处境的复杂性，不把人物写成恋爱八卦、苦难堆砌或完美女性模板。"
        )
        emotion_route = "好奇 → 心疼 → 理解 → 清醒 → 共鸣"
        reader_value = "让读者从她的人生得失中看见自己的关系、选择和成长"
    else:
        subject_rule = (
            "选择晚清至民国关键人物，围绕一个改变其命运或时代走向的真实事件展开。"
            "把人物放回当时的制度、战争、外交、革命和社会处境中，避免简单贴忠奸、成败、好坏标签；"
            "既要讲选择，也要讲限制、后果和历史争议。"
        )
        emotion_route = "悬念 → 冲突 → 复杂 → 恍然 → 历史纵深"
        reader_value = "把课本中零散的人名与事件连成完整的时代脉络"

    promotion_rules = load_book_promotion_guidelines(bare)
    return f"""你是一名擅长视频号人物故事和图书转化的短视频文案策划。

请围绕下面的人物和事件，写一篇适合带出{formatted}的原创口播文案。人物选择、故事角度、情绪价值和结尾推荐都必须与这本书匹配，禁止套用《国之脊梁》的科学家报国模板。

目标书籍：{formatted}
人物名称：{person_line}
核心事件/角度：{event_line}
选题边界：{subject_rule}
读者价值：{reader_value}
情绪路线：{emotion_route}
视频时长：4到5分钟
正文长度：1000到1300个中文字符，不能少于1000字
分镜段落：20到30个自然段，每段只对应一个可呈现的画面

写作要求：
1. 只写真实人物和可核实的真实经历。不得编造数字、台词、心理活动、人物关系或书中章节；不确定的细节不要写死。
2. 不写人物百科，不从出生年份讲起，不概括整个人生。围绕一个具体事件，用动作、物件、关系变化和实际代价推进故事。
3. 前三句形成“爆点—加压或反转—悬念”。第一句直接进入人物独有的反常处境或关键动作，不能介绍身份、铺时代背景、喊口号，也不能套到任何人物身上。
4. 人物姓名要顺着故事自然出现。背景只为解释核心事件服务，时间线必须清楚。
5. 语言适合真人口播，短句、有画面、有情绪，但不要用夸大史实、强行煽情、网络谣言或空泛评价制造冲突。
6. 情绪来自人物真实处境、选择和后果。让读者自己感受到人物分量，不连续喊“伟大、传奇、震撼、值得铭记”。
7. 按画面变化自然分段，不加小标题、“镜头一”等标记，不要出现只有几个字的空段。
8. 结尾用2到3个自然段承接人物故事，自然带出{formatted}。必须讲清这本书能帮助谁、看懂什么、为什么值得读；不要突然转成硬广。

所选书籍的专属带书规则：
{promotion_rules}

输出前自检：
- 这个人物和故事是否真的适合{formatted}，而不是换一本书也成立？
- 是否讲清人物面对的具体难处、选择、代价和结果？
- 开头是否属于这个人物，换个人就不能直接套用？
- 正文是否达到1000字和20段，结尾是否自然完成书籍价值承接？

只返回严格 JSON，不要 Markdown。字段必须包含 title, person, event_angle, script。
script 只放完整正文；title 为2到9个字且不要标点。
person 字段填写：{person_line}
event_angle 字段填写：{event_line}
""".strip()


def build_original_script_method_prompt(book_title: str) -> str:
    _, formatted = normalize_sales_book_title(book_title)
    return f"""

【原创爆款方法——必须执行】
不要按人物生平写，要把一个真实人物写成一场观众愿意看完、表态和转发的情绪事件。全文只证明一个核心命题，所有材料都为这个命题服务。

一人三极：
1. 极大分量：人物解决了什么重要问题、推动了什么变化，或他的选择为什么值得今天的人理解。
2. 极难处境：人物失去了什么、承受了什么不公、误解、疾病、贫困、危险、关系破裂或不可逆代价。
3. 极小细节：至少安排三个有事实依据、能入镜的具体物件或生活细节，分别承载处境、感情和人物分量。没有依据就不用硬凑。

七段推进：
1. 结果炸弹：前80字优先放一个有事实依据的具体数字、明显反差和主悬念。没有可靠数字时宁可不用，严禁编造。
2. 身份翻转：迅速揭示人物真正分量，不堆称号。
3. 苦难起点：用具体画面写第一道困难。
4. 第一次高光：让人物先赢一次，让希望成立。
5. 命运重击：在希望之后出现更严重、最好不可逆的转折。
6. 终极选择：重点写人物在最难时主动选择了什么，以及真实代价。
7. 关联今天：把人物的影响翻译成普通人能理解的现实意义，再自然承接到{formatted}。

悬念与节奏：
- 开头设置一个主悬念和至少两个副悬念，不能在前三句把答案一次说完。
- 每200到350字重新启动一次悬念、危机、选择或真相揭晓，禁止“A之后B、后来C”的履历流水账。
- 情绪要有起伏，可按“震惊—好奇—心疼—敬佩—不平—释然或自豪—表态”推进；不能从头到尾一直卖惨或一直赞美。
- 专业贡献必须翻译成普通人听得懂的结果，并说明它与今天读者的认知或生活有什么关系，但不得夸大因果。

互动与带书：
- 故事完成七成以后才能出现互动引导。点赞要有价值表态的理由，转发要有传播人物或分享认知的理由，评论要留下有经历感的选择题；不要机械索赞，不要每篇都让人刷“致敬”。
- 结尾先收住人物，再说明{formatted}能补全什么认知、适合谁读，书必须是故事情绪的答案，不得突然硬切广告。

事实底线：
- 数字、引语、具体物件、人物关系和历史结果必须有可靠依据；不要把网络金句安到人物头上，不写“99%的人不知道”“世界唯一”等无依据夸张。
- 全文不得直接写孙中山、孙文、中山先生、周恩来或周总理，也不得以他们为主角或借他们作流量钩子。涉及相关时代背景时，用不指向敏感人物的客观时代描述带过。

输出前检查12项：前80字有无可靠数字；有无反常识结果；有无至少两个未解问题；核心矛盾能否一句话说清；贡献是否通俗可懂；有无具体物件；每300字左右有无新转折；人物有无主动选择；有无最强情绪场景；是否关联今天；结尾是否克制；互动和带书是否各有自然理由。至少满足九项再输出。
""".rstrip()


def generate_guozhijiliang_script(
    person_name: str = "",
    event_angle: str = "",
    promotion_book_title: str = "国之脊梁",
) -> dict:
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY is not configured")

    bare_book_title, formatted_book_title = normalize_sales_book_title(promotion_book_title)
    if bare_book_title not in SUPPORTED_PROMOTION_BOOK_TITLES:
        raise ValueError(f"Unsupported promotion book: {bare_book_title}")
    selected_person, selected_angle = choose_book_script_seed(
        bare_book_title, person_name, event_angle
    )
    result: dict = {}
    script = ""
    stats = {"chars": 0, "paragraphs": 0}
    retry_note = ""
    for attempt in range(3):
        prompt = build_book_script_prompt(
            bare_book_title, selected_person, selected_angle
        ) + build_original_script_method_prompt(bare_book_title) + retry_note
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
        contains_sensitive_person = any(
            name in script for name in SENSITIVE_AI_SCRIPT_PEOPLE
        )
        if (
            stats["chars"] >= MIN_GUOZHIJILIANG_SCRIPT_CHARS
            and stats["paragraphs"] >= MIN_GUOZHIJILIANG_SCRIPT_PARAGRAPHS
            and not opening_needs_rewrite
            and not contains_sensitive_person
        ):
            break
        if contains_sensitive_person:
            retry_note = (
                "\n\n【重写要求】刚才的成稿直接出现了禁写人物姓名或称呼，不合格。"
                "必须完整改用当前选定的其他人物推进故事，全文不得出现孙中山、孙文、"
                "中山先生、周恩来或周总理；相关时代背景只作客观概括。"
            )
        elif opening_needs_rewrite:
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
    if any(name in script for name in SENSITIVE_AI_SCRIPT_PEOPLE):
        raise RuntimeError("MiniMax response still contains a blocked sensitive person")
    return {
        "title": normalize_auto_title(str(result.get("title") or ""), script),
        "person": str(result.get("person") or selected_person).strip(),
        "event_angle": str(result.get("event_angle") or selected_angle).strip(),
        "promotion_book_title": formatted_book_title,
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
