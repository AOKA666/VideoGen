from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from difflib import SequenceMatcher

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
MIN_REWRITE_LENGTH_RATIO = 0.85
MAX_REWRITE_LENGTH_RATIO = 1.25
MIN_REWRITE_DIFFERENCE = 40
MAX_REWRITE_ATTEMPTS = 3
MAX_AUTO_TITLE_LENGTH = 9
MAX_PUBLISH_SHORT_TITLE_LENGTH = 16
OPENING_HOOK_MIN_CHARS = 20
OPENING_HOOK_MAX_CHARS = 35
TITLE_PUNCTUATION = re.compile(r"""[，。！？、；："'“”‘’《》【】（）—…\-.!?,;:()\[\]{}<>\s]""")
WEAK_COVER_TITLE_PATTERNS = (
    "伟大",
    "精神",
    "民族脊梁",
    "大国",
    "传奇",
    "一生",
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
    "不敢",
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
    "却", "竟", "反而", "偏偏", "不敢", "不能", "不许", "没", "没有", "为何",
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


def clean_auto_title(text: str) -> str:
    title = re.sub(r"^(标题|项目名称|短标题|片名)[:：]", "", str(text or "").strip())
    return TITLE_PUNCTUATION.sub("", title).strip()


def looks_like_truncated_sentence(title: str, raw_script: str) -> bool:
    if not title or not raw_script:
        return False
    sentences = split_sentences(raw_script)
    first = clean_auto_title(sentences[0] if sentences else raw_script)
    return len(first) > len(title) + 3 and first.startswith(title)


def fallback_infer_title(raw_script: str) -> str:
    text = re.sub(r"\s+", "", raw_script or "")
    if not text:
        return "未命名项目"
    people = [
        "钱学森", "邓稼先", "于敏", "黄旭华", "郭永怀", "袁隆平", "王淦昌",
        "孙家栋", "屠呦呦", "王承书", "彭士禄", "林俊德",
    ]
    themes = [
        ("两弹一星", "两弹一星"),
        ("回国", "归国"),
        ("归国", "归国"),
        ("隐姓埋名", "隐姓埋名"),
        ("核潜艇", "核潜艇"),
        ("青蒿素", "青蒿素"),
        ("杂交水稻", "稻田传奇"),
        ("卫星", "卫星时刻"),
        ("导弹", "导弹往事"),
        ("绝密", "绝密往事"),
        ("牺牲", "最后时刻"),
        ("母亲", "家书背后"),
        ("父亲", "家书背后"),
        ("国家", "国家选择"),
    ]
    person = next((item for item in people if item in text), "")
    theme = next((label for needle, label in themes if needle in text), "")
    candidates: list[str] = []
    if person and theme:
        candidates.append(f"{person}{theme}")
    if person:
        candidates.extend([f"{person}往事", f"{person}故事", person])
    if theme:
        candidates.append(theme)
    for candidate in candidates:
        cleaned = clean_auto_title(candidate)
        if 2 <= len(cleaned) <= MAX_AUTO_TITLE_LENGTH:
            return cleaned
    chunks = re.findall(r"[\u4e00-\u9fff]{2,6}", text)
    weak_chunks = {"今天我们", "很多人", "大家好", "你知道", "这个故事", "一个"}
    for chunk in chunks:
        if chunk not in weak_chunks and not looks_like_truncated_sentence(chunk, raw_script):
            return chunk
    return "未命名项目"


def normalize_auto_title(title: str, raw_script: str) -> str:
    cleaned = clean_auto_title(title)
    if 2 <= len(cleaned) <= MAX_AUTO_TITLE_LENGTH and not looks_like_truncated_sentence(cleaned, raw_script):
        return cleaned
    return fallback_infer_title(raw_script)


def infer_title(raw_script: str) -> str:
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        return fallback_infer_title(raw_script)
    prompt = (
        "请根据下面的中文短视频文案，生成一个项目标题。\n"
        "要求：1. 必须根据文案主题提炼，不要直接截取原文开头。"
        "2. 不要截断句子，优先使用完整短语。3. 不要标点符号。"
        "4. 必须是完整短标题，不要像一句话被截断。"
        "5. 只返回JSON，不要解释。\n\n"
        f"文案：\n{(raw_script or '')[:900]}\n\n"
        '{"title": "项目标题"}'
    )
    payload = {
        "model": minimax_model(),
        "messages": [
            {"role": "system", "content": "你只输出可解析JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.55,
        "top_p": 0.85,
        "max_tokens": 120,
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
        with urllib.request.urlopen(req, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        result = json.loads(extract_json(str(content)))
        return normalize_auto_title(str(result.get("title") or ""), raw_script)
    except Exception:
        return fallback_infer_title(raw_script)


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


def fallback_rewrite_script(raw_script: str, style: str = "纪实故事型", preserve_rule: str = "auto") -> dict:
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


def normalize_rewrite_result(result: dict, raw_script: str, style: str, preserve_rule: str = "auto") -> dict:
    title = str(result.get("title") or infer_title(raw_script)).strip()
    hook = extract_opening_hook(raw_script, preserve_rule) or str(result.get("hook") or build_fallback_hook(raw_script, title)).strip()
    rewritten_script = str(result.get("rewritten_script") or "").strip()
    if not rewritten_script:
        rewritten_script = fallback_rewrite_script(raw_script, style, preserve_rule)["rewritten_script"]
    rewritten_script = clean_rewritten_script(raw_script, rewritten_script)
    rewritten_script = ensure_original_opening(raw_script, rewritten_script, preserve_rule)
    rewritten_script = add_blank_lines_between_paragraphs(rewritten_script)
    comparison = compare_scripts(raw_script, rewritten_script)
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


def build_rewrite_prompt(raw_script: str, style: str, attempt: int, previous: dict | None = None, preserve_rule: str = "auto") -> str:
    opening_hook = extract_opening_hook(raw_script, preserve_rule)
    raw_len = content_length(raw_script)
    min_len = int(raw_len * MIN_REWRITE_LENGTH_RATIO)
    max_len = int(raw_len * MAX_REWRITE_LENGTH_RATIO)
    has_book_promotion = bool(re.search(
        r"(《[^》]+》|这本书|书里|书中|翻开|读完|买给孩子|下单|购买|小黄车|带书|卖书|推荐给家长)",
        raw_script,
    ))
    conversion_instruction = (
        "原文包含带书或图书推荐内容，可以保留原有转化意图并重新表达，但不要扩大篇幅、不要改成硬广。"
        if has_book_promotion else
        "原文不包含带书或图书推荐内容，改写稿也禁止主动添加书名、翻书、读书感受、买书、小黄车、家长购买或推荐给孩子等转化内容。结尾必须跟随原文主题自然收束。"
    )
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

我要你改写下面这篇文案，目标是在视频号发布，用于提高播放量和完播率。是否保留带书内容必须跟随原文，不得自行添加。

【最重要要求】
用户选定的原文开头必须一字不改保留，不允许改字、不允许换词、不允许调整顺序、不允许删减。
你只能从这段受保护内容之后开始优化。即使你判断开头不够好，也不能擅自改动。
必须原样保留的开头是：{opening_hook}

【改写目标】
保留原文的短视频味道，不要改成书面文章。
改写后的文案要像一个懂视频号的人在口播，而不是像公众号社论、新闻评论、AI润色稿、学生作文。
整体风格要：口语化、有网感、有情绪、有画面、有节奏、有冲突。
不要追求文采高级，要追求用户愿意听下去、愿意点赞、愿意评论、愿意转发。

【只允许原样保留的内容】
1. 只有原文前三秒钩子必须逐字保留。
2. 人名、地名、年份、数字、事件等客观事实可以保留，但承载这些事实的句子必须重新表达。
3. 除前三秒钩子和无法改写的专有名词外，原文中的完整句子、金句、狠话、过渡句、情绪表达和带书话术都不要照抄。
4. 不要求保留原文的句式、段落顺序、叙述视角、情绪推进方式或表达风格；这些内容必须重新组织。

【禁止事项】
不要做简单同义词替换。
不要把口语改成书面语。
不要把“咱妈、塞铁、刷666、小鱼小虾、你可以试试、护筷子”这类短视频表达全部洗掉。
不要使用过多书面词，例如：悍然、方知、伟岸、至此、乃、赴汤蹈火、径直、再至、苍生、星光、抉择、壮烈史诗、强国气场、脱胎换骨、恩重如山。
不要频繁使用空泛大词，例如：伟大、震撼、辉煌、底蕴、史诗、精神源泉、民族脊梁、大国情怀。这些词可以少量使用，但不能堆。
不要把文案改成端着的播音腔。
不要一上来介绍背景，不要平铺直叙。
不要用背景、环境、天气、时代、氛围、人物状态来开头。开头必须先给爆点、冲突、结果、反差或悬念。
如果原文前三秒之后需要承接，承接句也不要写“在那个年代”“当时的环境”“故事要从某年说起”，要直接进入具体事件和具体动作。
不要削弱原文的爽感、反差感和情绪冲击。

【短视频改写原则】
一、句子要短。适合真人口播。能用短句就不要用长句。能用人话就不要用书面话。
二、表达要狠。该硬的地方要硬。比如：“你可以试试敢不敢将它击落。”这种句子不要改成：“那便试试看是否敢于动用武力击落。”
三、要有画面。多保留或强化具体画面：飞机起飞、国旗铺满街道、地图包围、旧照片、病房电脑、公文包、胶鞋、行李箱、实验室灯光、戈壁风沙。少写抽象评价。
四、要有情绪递进。文案结构尽量按照：前三秒钩子不变 → 具体事件暴击 → 关键冲突 → 必要背景解释 → 历史伤痛/现实困境 → 今日反转 → 情绪爆发 → 跟随原文主题自然收束。背景只能在爆点之后补，不能放在开头。
五、带书内容跟随原文：{conversion_instruction}

【分段要求】
按画面逻辑组织自然段，但不限制每段字数，也不限制自然段内部换行。
每段应尽量对应一个完整画面，方便后续 AI 配图、素材搜索和剪映剪辑。
换段标准是：时间变化、地点变化、人物动作变化、画面主体变化、情绪节点变化。
不要按朗读断句分段，而要按画面分段。
不要在 rewritten_script 中添加 [1]、[2] 等序号，序号由前端界面展示。

【改写尺度】
不是洗稿式同义替换，而是在保留前三秒和事实边界的前提下重新写一篇文案。
可以删掉重复啰嗦的句子。
可以强化画面感和冲突感。
必须调整正文结构、叙述顺序、句式、转折方式和情绪推进。
可以更换叙述视角，可以把后文爆点前置；只有原文包含带书内容时，才可以重新设计其表达方式。
除前三秒外，不得连续照抄原文句子；不得只靠换词、增删标点或重新分段制造改写效果。

【长度和质量约束】
原文去除空白后的长度约 {raw_len} 个中文字符。
rewritten_script 去除空白后的长度必须控制在 {min_len} 到 {max_len} 个中文字符之间。
不要压缩成摘要、提纲或短版解说，也不要省略原文中的重要事实。
事实边界：不虚构；不添加没有依据的具体时间、地点、人物关系；人物、年代、事件、因果关系必须保留。
除必须原样保留的前三秒开头、专有名词、年份、数字和固定称谓之外，整体内容必须重新组织。
系统会用字符相似度和语义相似度自动对比，最终总体差异度必须达到 {MIN_REWRITE_DIFFERENCE}% 以上。
40% 是硬性验收线：不能通过重新换行、调整标点或少量同义词替换达成，正文必须让读者明显感到是重新讲述。

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


def rewrite_script_with_minimax(raw_script: str, style: str, api_key: str, preserve_rule: str = "auto") -> dict:
    raw_len = content_length(raw_script)
    best_result: dict | None = None
    last_result: dict | None = None
    for attempt in range(1, MAX_REWRITE_ATTEMPTS + 1):
        prompt = build_rewrite_prompt(raw_script, style, attempt, last_result, preserve_rule)
        result = request_minimax_rewrite(prompt, raw_script, style, api_key, raw_len, preserve_rule)
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


def request_minimax_rewrite(prompt: str, raw_script: str, style: str, api_key: str, raw_len: int, preserve_rule: str = "auto") -> dict:
    payload = {
        "model": minimax_model(),
        "messages": [
            {"role": "system", "content": "你只输出可解析 JSON。rewritten_script 按画面逻辑自然分段，不限制每段字数或段内换行，不要在正文中添加段落序号。"},
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
    return normalize_rewrite_result(result, raw_script, style, preserve_rule)


def rewrite_script(raw_script: str, style: str = "纪实故事型", preserve_rule: str = "auto") -> dict:
    fallback = fallback_rewrite_script(raw_script, style, preserve_rule)
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        return ensure_min_rewrite_difference(fallback)
    try:
        return rewrite_script_with_minimax(raw_script, style, api_key, preserve_rule)
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
结尾必须自然带《国之脊梁》，但不要硬卖。可以类似“最近读《国之脊梁》，再次看到他的故事，心里久久不能平静。”“如果家里有孩子，真希望他们认识这样的人。”不要连续喊口号。

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

结尾必须自然带出《国之脊梁》。不要硬广，不要写“点击小黄车购买”。
可以这样写：“翻开《国之脊梁》才知道，今天我们习以为常的底气，从来不是凭空来的。”“读完这本书才明白，我们不是没有英雄，只是太多人把名字藏进了历史深处。”“如果家里有孩子，真希望他们认识这些人。”“因为真正值得追的星，从来不在热搜里，而在《国之脊梁》里。”“他们不是娱乐新闻里的明星，却是中国孩子最该知道的名字。”

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


def cover_title_needs_rewrite(line1: str, line2: str, script: str = "") -> bool:
    combined = f"{line1}{line2}"
    if not combined:
        return True
    # “没先做某事”通常只是把一个正常选择硬包装成反常行为，而且隐藏了
    # 真正发生的动作。封面标题应直接写实际选择及其代价，而不是虚构预期。
    if any(pattern in combined for pattern in TITLE_FAKE_CONTRAST_PATTERNS):
        return True
    # 时间顺序、独自行动等修饰语会实质改变事实；原文没有时不能为制造冲突添加。
    if script:
        if any(modifier in combined and modifier not in script for modifier in TITLE_FACT_SENSITIVE_MODIFIERS):
            return True
        if TITLE_SEQUENCE_ACTION_PATTERN.search(combined) and "先" not in script:
            return True
    if any(pattern in combined for pattern in WEAK_COVER_TITLE_PATTERNS):
        return True
    if any(left in combined and right in combined for left, right in COVER_TITLE_SPOILER_COMBOS):
        return True
    if any(ending in line2 for ending in TITLE_SUMMARY_ENDINGS):
        return True
    if len(combined) >= 6 and not any(word in combined for word in COVER_TITLE_ATTRACTION_WORDS):
        return True
    if len(combined) >= 8 and not any(word in combined for word in TITLE_OPEN_LOOP_WORDS):
        return True
    return False


def cover_title_score(line1: str, line2: str, script: str) -> int:
    combined = f"{line1}{line2}"
    score = 0
    score += sum(5 for word in COVER_TITLE_ATTRACTION_WORDS if word in combined)
    score += sum(3 for char in combined if char.isdigit())
    score += 6 if any(word in combined for word in ("却", "竟", "不敢", "不能", "最后", "凭什么", "到底")) else 0
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
        return {"line1": "", "line2": "", "full_title": ""}

    base_prompt = (
        "你是视频号和抖音爆款封面标题专家，擅长为历史人物、爱国教育、大国叙事、卖书转化类短视频生成高停留率封面标题。"
        "\n\n请根据我提供的文案内容，生成两行式短视频封面标题。"
        "\n\n目标：让用户在推荐流里扫一眼就想停下来，产生“为什么会这样”“这也太离谱了”“这个人是谁”“我得看下去”的反应。"
        "\n\n【最重要原则】"
        "\n标题不是文章标题，不是新闻标题，不是中心思想，不是文案摘要。"
        "\n标题只需要抓住文案里最有冲突、最反常识、最心疼、最不公平、最有画面感的一个局部爆点。"
        "\n宁可抓一个狠瞬间，也不要写得全面、平衡、正确但没人想点。"
        "\n必须通读整篇文案后再选爆点，不能只根据开头、人物身份或最终成就起标题。"
        "\n爆点必须来自文案中的真实具体事实，优先选择全篇冲突强度最高、最让普通人意外的那一件事。"
        "\n标题的逻辑必须独立成立：前一行是处境，后一行必须是真正出人意料的选择或后果，不能仅靠却、竟、没、反而等词假装反常。"
        "\n判断反常时必须使用普通人的现实常识：只有相反选择明显更正常、更合理时，当前选择才算反常；如果多数人处在同样处境也会这么做，就不是爆点。"
        "\n尤其禁止把正常的不作为包装成反常，例如丈夫被关押时，妻子没有先带孩子回国本身很正常，不能写成悬念；若真正爆点是她掉头回国、留下营救或作出其他主动选择，应直接写那个真实动作。"
        "\n不得为了制造反差擅自添加先、独自、立即、马上、转身、掉头等会改变事实或时间顺序的词，除非文案明确写出。"
        "\n\n【生成前的内部步骤】"
        "\n在生成标题前，请你先在内部完成以下判断，但不要输出过程："
        "\n1. 从文案里提炼5个最有停留价值的爆点瞬间。"
        "\n2. 判断哪个爆点最适合做封面标题。"
        "\n3. 优先选择有具体画面、具体动作、具体物品、具体数字的爆点。"
        "\n4. 不要优先选择抽象主题、人物贡献、中心思想。"
        "\n5. 站在普通视频号用户视角反审：如果我刷到这个标题，会不会停下来？如果不会，必须重写。"
        "\n6. 给5个爆点按冲突强度、反常识程度、具体画面感、情绪代价分别打分，最终标题必须围绕总分最高的爆点。"
        "\n7. 不得因为某个爆点出现在文案开头就优先选它；后文爆点更强时必须选择后文。"
        "\n8. 对每个候选做反事实检查：去掉却、竟、没等转折词后，后一行是否仍是一件客观异常且值得追问的事；若只是正常选择，整组淘汰。"
        "\n9. 检查两行的主体、时间和因果是否衔接；不能偷换人物，不能把先后顺序或普通反应硬说成反差。"
        "\n\n【标题格式】"
        "\n1. 必须生成两行文字。"
        "\n2. 每行1到9个字，任何一行都不能超过9个字。"
        "\n3. 每组标题由“第一行”和“第二行”组成。"
        "\n4. 标题中禁止出现任何标点符号，包括逗号、句号、感叹号、问号、冒号、破折号、引号等。"
        "\n5. 如果一句话超过9个字，必须重新概括成更短的完整表达，严禁直接截断。"
        "\n6. 只返回JSON，不要Markdown，不要解释。"
        "\n\n【两行分工】"
        "\n第一行优先放：冲突现场、反常动作、具体物品、身份反差、强结果。"
        "\n第二行优先放：悬念补刀、情绪放大、代价、反差、追问、亏欠感。"
        "\n两行之间必须形成认知落差：第一行建立预期，第二行打破预期或留下一个没有解释完的问题。"
        "\n认知落差必须来自事实本身，而不是由却、竟、没、反而等连接词强行制造。没有真实反常点时，宁可写具体困境、主动动作或明确代价。"
        "\n禁止写成完整的因果总结、人物评价或功绩概括，例如“隐姓埋名二十年/铸就雷达千里眼”。"
        "\n第二行禁止用铸就、成就、奉献、贡献、功勋、报国、守护中国、照亮中国等词收束主题。"
        "\n\n好的结构示例："
        "\n第一行：父亲去世那天\n第二行：他不敢回家"
        "\n第一行：美国扣下箱子\n第二行：到底怕什么"
        "\n第一行：她捐出千万\n第二行：却穿15块鞋"
        "\n第一行：名单上没她\n第二行：她自己划掉"
        "\n第一行：法国领奖台\n第二行：他却不笑"
        "\n第一行：病床前电脑\n第二行：他还在敲字"
        "\n\n【标题风格】"
        "\n标题必须口语化、狠一点、像人话。"
        "\n可以大胆使用爆款短视频写法：悬念、反差、冲突、误区纠正、身份反差、强结果、强代价、心疼感、不公平感、亏欠感。"
        "\n不要端着。不要像纪念馆展板。不要像作文题目。不要像新闻标题。不要像领导题词。不要像百科词条。"
        "\n\n【优先使用的爆点类型】"
        "\n1. 认知误区：用户以为是A，其实是B。"
        "\n2. 身份反差：看起来普通的人，做了极不普通的事。"
        "\n3. 荣誉缺席：明明立大功，却没有名字。"
        "\n4. 亲情亏欠：父亲去世不能回家，母亲骂他不孝。"
        "\n5. 生死瞬间：临终前、坠毁前、病床上、最后一天。"
        "\n6. 屈辱反击：被外国人看不起，后来用结果打回去。"
        "\n7. 具体物品：公文包、旧胶鞋、行李箱、手稿、算盘、病号服、轮椅、饭盘、抽屉、奖章。"
        "\n8. 数字反差：15块鞋、1000万、52人、30年、90岁、最后一天。"
        "\n\n【高停留词优先】"
        "\n可以优先使用这些词：扣下、炸掉、抹掉、坠毁、不能回家、被骂、捐出、消失、千万、15块、普通老太太、亲手、名单、功劳簿、凭什么、为什么、到底、没人敢、谁也没想到、最后一天、没名字、没热搜、被忘了、全额赔钱、他却不笑、她自己划掉。"
        "\n\n【禁止使用】"
        "\n不要用这些空洞词：震惊、惊人、不可思议、伟大、精神、民族脊梁、大国、传奇、一生、值得铭记、感人至深、无私奉献、家国情怀、时代楷模、光辉事迹。"
        "\n不要写成这种：国之脊梁、民族英雄、伟大科学家、致敬先辈、孩子该看、中国骄傲、隐姓埋名一生、共和国不会忘记。"
        "\n这些太正、太空、太像展板，不能作为封面标题。"
        "\n\n【避免自我解谜】"
        "\n不要写一眼就把故事讲完的标题。"
        "\n比如：第一行：女儿病危那天\n第二行：他死盯图纸"
        "\n这种用户一眼就猜到“为国家舍小家”，张力已经被解释完。"
        "\n要留一点空，让用户想知道为什么。"
        "\n更好的方式：第一行：女儿病危那天\n第二行：他不敢抬头"
        "\n或者：第一行：病危通知来了\n第二行：他却没回家"
        "\n\n【输出数量】"
        "\n请一次生成12组标题。"
        "\n12组标题要尽量覆盖不同类型，不要全是疑问句，不要全是同一种模板。"
        "\n第1组必须是你判断的全篇最强爆点，后续各组才允许尝试其他角度。"
        "\n每组必须包含：first_line、second_line、style。"
        "\nstyle只能从以下类型中选择：悬念型、反差型、冲突型、心疼型、爽感型、亏欠型、误区型、画面型。"
        "\n\n【最终自检】"
        "\n输出前请内部自检："
        "\n1. 每行是否不超过9个字。"
        "\n2. 是否没有标点符号。"
        "\n3. 是否像封面大字，而不是文章标题。"
        "\n4. 是否抓住了具体爆点，而不是全文概括。"
        "\n5. 普通用户扫一眼是否有停留理由。"
        "\n6. 是否避免了空洞大词。"
        "\n7. 是否有足够反差或悬念。"
        "\n8. 去掉转折词后，所谓反常是否仍然成立；如果只是正常人的正常选择，必须淘汰。"
        "\n9. 标题中的先后顺序、人物动作和因果关系是否都能在原文中找到依据。"
        "\n如果不合格，必须重写后再输出。"
        "\n\n【输出格式】"
        "\n只返回JSON数组，不要Markdown，不要解释。"
        "\n格式如下："
        '\n[{"first_line": "第一行", "second_line": "第二行", "style": "悬念型"}]'
        "\n\n下面是文案内容："
        f"\n{script[:6000]}"
    )

    last_error = ""
    try:
        for _ in range(3):
            retry_note = f"\n\n上一版不合格：{last_error}。请重新生成，不要截断原句。" if last_error else ""
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
            for candidate_index, item in enumerate(candidates):
                line1 = strip_title_punctuation(item.get("first_line") or item.get("line1") or "")
                line2 = strip_title_punctuation(item.get("second_line") or item.get("line2") or "")
                if 1 <= len(line1) <= 9 and 1 <= len(line2) <= 9 and not cover_title_needs_rewrite(line1, line2, script):
                    valid_candidates.append({
                        "line1": line1,
                        "line2": line2,
                        "full_title": f"{line1} {line2}",
                        "style": str(item.get("style") or "").strip(),
                        # The prompt requires the model to rank its strongest title
                        # first. Preserve that semantic judgement while still using
                        # local scoring to break ties and reject weak structures.
                        "score": cover_title_score(line1, line2, script) + max(0, 12 - candidate_index) * 4,
                    })
            if valid_candidates:
                best = max(valid_candidates, key=lambda item: item["score"])
                best.pop("score", None)
                return best
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
