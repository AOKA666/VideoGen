from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from services.text_service import minimax_endpoint, minimax_model


STEP_LABELS = {
    1: "策略分析",
    2: "正文创作",
    3: "史实与合规终审",
}
HISTORY_MODEL_PROVIDERS = {"minimax", "deepseek", "openai"}


def normalize_history_model_provider(value: object) -> str:
    provider = str(value or "minimax").strip().lower()
    if provider not in HISTORY_MODEL_PROVIDERS:
        raise ValueError("history model provider must be minimax, deepseek, or openai")
    return provider


def _history_model_config(provider: str) -> tuple[str, str, str, str]:
    normalized = normalize_history_model_provider(provider)
    if normalized == "openai":
        return (
            os.getenv("OPENAI_API_KEY", "").strip(),
            os.getenv("OPENAI_ENDPOINT", "https://api.openai.com/v1").strip()
            or "https://api.openai.com/v1",
            os.getenv("OPENAI_MODEL", "gpt-5.6").strip() or "gpt-5.6",
            "OpenAI",
        )
    if normalized == "deepseek":
        return (
            os.getenv("DEEPSEEK_API_KEY", "").strip(),
            os.getenv("DEEPSEEK_ENDPOINT", "https://api.deepseek.com").strip()
            or "https://api.deepseek.com",
            os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
            or "deepseek-v4-flash",
            "DeepSeek",
        )
    return (
        os.getenv("MINIMAX_API_KEY", "").strip(),
        minimax_endpoint(),
        minimax_model(),
        "MiniMax",
    )


@lru_cache(maxsize=1)
def load_history_workflow_prompt() -> str:
    candidates = (
        Path(__file__).resolve().parents[1] / "二创提示词.txt",
        Path(__file__).resolve().parents[3] / "二创提示词.txt",
    )
    for path in candidates:
        try:
            content = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        if content:
            return content
    raise RuntimeError("未找到历史创作提示词：二创提示词.txt")


def extract_history_fixed_opening(strategy: str) -> str:
    marker = "【前三秒固定开头】"
    if marker not in strategy:
        return ""
    section = strategy.split(marker, 1)[1]
    section = re.sub(r"^\s*[:：]\s*", "", section)
    next_section = re.search(r"\n\s*(?:#{1,6}\s*)?【[^】]+】", section)
    if next_section:
        section = section[:next_section.start()]
    cleaned_lines = []
    for raw_line in section.splitlines():
        line = re.sub(r"^\s*>\s?", "", raw_line).strip()
        if line:
            cleaned_lines.append(line)
    opening = "\n".join(cleaned_lines).strip()
    if opening.startswith("**") and opening.endswith("**") and len(opening) > 4:
        opening = opening[2:-2].strip()
    quote_pairs = (("“", "”"), ('"', '"'), ("‘", "’"), ("'", "'"))
    for left, right in quote_pairs:
        if opening.startswith(left) and opening.endswith(right) and len(opening) > 2:
            opening = opening[len(left):-len(right)].strip()
            break
    return opening


def _step_two_review_notes(script: str, fixed_opening: str, formatted_book_title: str) -> list[str]:
    notes = []
    if not script.lstrip().startswith(fixed_opening):
        notes.append("固定开头保留度偏低：正文没有逐字以【前三秒固定开头】起笔")
    if formatted_book_title not in script:
        notes.append(f"带书转化得分偏低：正文没有自然植入{formatted_book_title}")
    elif formatted_book_title not in script[-220:]:
        notes.append(f"结尾结构得分偏低：正文没有以介绍{formatted_book_title}的带书段落结尾")
    return notes


def _step_two_optimization_prompt(fixed_opening: str, formatted_book_title: str) -> str:
    return (
        "现在批评上面的 Step 2 首稿，并只对确有必要的局部做一版优化候选稿。"
        "先在内部按 10 分制逐项检查：\n"
        "1. 开头钩子：前三秒是否抓人，固定开头后的承接是否继续放大悬念；\n"
        "2. 故事性：人物、冲突、转折、细节和叙事推进是否能持续吸引观众；\n"
        "3. 结构：信息顺序、段落衔接、设问与解析的节奏是否清楚；\n"
        "4. 共鸣感：是否击中中老年受众熟悉的人情、处境和人生经验；\n"
        "5. 完播率：是否持续制造期待，避免中段松散、重复和提前泄掉悬念；\n"
        "6. 情绪力度：情绪是否有递进、转折和余味，同时不靠虚构煽情；\n"
        f"7. 带书转化：{formatted_book_title}的植入是否自然，读者价值与购买引导是否具体可信；\n"
        "8. 带货篇幅是否合适：带书是否只占一个紧凑自然段且总计不超过200个汉字；"
        "是否挤压故事、重复卖点、反复劝购或拖慢结尾；过长必须主动压缩；\n"
        "9. 评论互动：是否有能引发真实讨论的观点、选择或人生问题，避免生硬求评论。\n\n"
        "检查后只修正明确存在的薄弱项。不要为了显得更完整而重写已经自然、准确的段落，"
        "不要增加解释层级，不要统一改写全篇句式。优化时必须遵守：\n"
        f"- 全文必须仍然逐字以以下固定开头起笔，其前不得添加任何文字：\n{fixed_opening}\n"
        f"- 必须保留{formatted_book_title}的自然带书，但要压缩成一个紧凑自然段；"
        "带货内容总计不超过200个汉字，只讲一个与本篇直接相关的核心价值点，"
        "再给一句克制的行动引导；删除重复卖点、阅读感受、泛泛拔高和连续劝购；\n"
        f"- 最后一个自然段必须专门介绍{formatted_book_title}并作为全文结尾；"
        "该段之后不得再写故事总结、价值升华、互动提问或任何其他内容；\n"
        "- 不得虚构史实、对话或心理活动，不得为了评分而堆砌设问、情绪词和互动话术；\n"
        "- 不设目标字数和篇幅上下限，保留所有必要事实；\n"
        "- 最终只输出局部优化后的完整口播正文，不输出分数、评分表、分析、标题或修改说明。"
    )


def _step_two_judgement_prompt() -> str:
    return (
        "比较下面的首稿 draft_1 与局部优化稿 draft_2。不要默认优化稿更好。"
        "优先保留更自然、更准确、更少重复、更适合口播且带书结尾更克制的一稿。"
        "required_edits 只列胜出稿仍然必须修改的局部问题，最多 4 条；没有则返回空数组。"
        "只返回合法 JSON，不要 Markdown 代码块、评分表或额外说明，格式必须是：\n"
        '{"winner":"draft_1","reason":"首稿更自然，第二稿重复解释过多",'
        '"required_edits":["压缩第三段","结尾减少一层升华"]}'
    )


def _parse_step_two_judgement(content: str) -> dict[str, Any]:
    cleaned = str(content or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1)
    else:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1]
    try:
        parsed = json.loads(cleaned)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {
            "winner": "draft_1",
            "reason": "评审结果无法解析，为避免优化稿无条件覆盖，安全保留首稿。",
            "required_edits": [],
            "review_parse_failed": True,
        }
    winner = str(parsed.get("winner") or "draft_1").strip()
    if winner not in {"draft_1", "draft_2"}:
        winner = "draft_1"
    edits = parsed.get("required_edits")
    if not isinstance(edits, list):
        edits = []
    return {
        "winner": winner,
        "reason": str(parsed.get("reason") or "评审未提供原因。").strip(),
        "required_edits": [str(item).strip() for item in edits[:4] if str(item).strip()],
        "review_parse_failed": False,
    }


def _step_two_local_edit_prompt(required_edits: list[str], fixed_opening: str, formatted_book_title: str) -> str:
    edits = "\n".join(f"- {item}" for item in required_edits)
    return (
        "只对上面的胜出稿执行以下必要局部修改，不得整体重写，不得改变无关段落的观点、顺序或措辞：\n"
        f"{edits}\n\n"
        f"全文仍须逐字以以下固定开头起笔：\n{fixed_opening}\n"
        f"最后一个自然段仍须专门介绍{formatted_book_title}并以此结束，带货内容不超过200个汉字。\n"
        "只输出完成局部修改后的完整正文，不输出修改说明。"
    )


def _compact_length(text: str) -> int:
    return len(re.sub(r"\s+", "", str(text or "")))


def _repeated_sentence_ratio(text: str) -> float:
    sentences = [
        re.sub(r"[\s，。！？；：、“”‘’《》]", "", item)
        for item in re.split(r"(?<=[。！？；])|\n+", str(text or ""))
    ]
    sentences = [item for item in sentences if len(item) >= 12]
    if len(sentences) < 2:
        return 0.0
    repeated: set[int] = set()
    for left in range(len(sentences)):
        for right in range(left + 1, len(sentences)):
            if SequenceMatcher(None, sentences[left], sentences[right]).ratio() >= 0.88:
                repeated.update((left, right))
    return round(len(repeated) / len(sentences), 4)


def _book_introduction_length(text: str, formatted_book_title: str) -> int:
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n|\n", str(text or "")) if item.strip()]
    matching = [item for item in paragraphs if formatted_book_title in item]
    return _compact_length(matching[-1]) if matching else 0


def _draft_metrics(text: str, formatted_book_title: str) -> dict[str, Any]:
    return {
        "character_count": _compact_length(text),
        "repeated_sentence_ratio": _repeated_sentence_ratio(text),
        "book_introduction_length": _book_introduction_length(text, formatted_book_title),
    }


def _request_history_ai(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 10000,
    provider: str = "minimax",
    reasoning_effort: str = "medium",
) -> str:
    normalized_provider = normalize_history_model_provider(provider)
    api_key, endpoint, model, provider_label = _history_model_config(normalized_provider)
    if not api_key:
        variable = {
            "deepseek": "DEEPSEEK_API_KEY",
            "openai": "OPENAI_API_KEY",
        }.get(normalized_provider, "MINIMAX_API_KEY")
        raise RuntimeError(f"{variable} is not configured")
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if normalized_provider == "openai":
        payload.update({
            "max_completion_tokens": max_tokens,
            "reasoning_effort": reasoning_effort,
        })
    else:
        payload.update({
            "temperature": 0.72,
            "top_p": 0.9,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        })
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{provider_label} API {exc.code}: {detail}") from exc
    content = body["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    result = str(content or "").strip()
    if not result:
        raise RuntimeError("AI 未返回有效内容")
    return result


def build_history_step_messages(
    step: int,
    raw_script: str,
    outputs: dict[str, str] | None = None,
    stage_messages: dict[str, list[dict[str, str]]] | None = None,
    promotion_book_title: str = "国之脊梁",
) -> list[dict[str, str]]:
    if step not in STEP_LABELS:
        raise ValueError("step must be between 1 and 3")
    outputs = outputs or {}
    stage_messages = stage_messages or {}
    book_title = str(promotion_book_title or "").strip().strip("《》").strip() or "国之脊梁"
    formatted_book_title = f"《{book_title}》"
    protocol = load_history_workflow_prompt()
    system = (
        f"{protocol}\n\n"
        "你正在应用程序内执行门控工作流。只执行本次用户消息指定的阶段。"
        "不要要求用户再次输入斜杠命令，不要提前执行下一阶段。"
        f"本项目指定推广书籍为{formatted_book_title}，三个阶段都必须围绕这本书制定策略。"
    )
    if step == 1:
        task = (
            "现在执行 /step1。分析下面的参考文案，严格输出阶段一要求的"
            "【前三秒固定开头】【爆款逻辑洞察】【三要素提取表】【切入视角】【3个开场方案】。"
            "【前三秒固定开头】必须逐字摘录参考文案开头三秒对应内容，不得增删、改写或调整顺序；"
            "3个开场方案只能设计固定开头之后的承接方式，不得替换固定开头。"
            f"另外输出【{formatted_book_title}带书衔接策略】，说明历史主题与该书的关联、"
            "适合植入的位置、唯一核心价值点和一句自然过渡方式；策略必须简短，"
            "不要预写冗长带书话术，不得替换成其他书。"
            "末尾简短提醒用户可以继续修改或确认进入第二步，不要写正文。\n\n"
            f"<指定推广书籍>{formatted_book_title}</指定推广书籍>\n\n"
            f"<参考文案>\n{raw_script}\n</参考文案>"
        )
    elif step == 2:
        strategy = str(outputs.get("1") or "").strip()
        if not strategy:
            raise ValueError("执行 step2 前必须先完成 step1")
        fixed_opening = extract_history_fixed_opening(strategy)
        if not fixed_opening:
            raise ValueError("阶段一结果缺少可识别的【前三秒固定开头】，请重新执行 step1")
        feedback = format_stage_feedback(stage_messages.get("1") or [])
        task = (
            "用户已确认阶段一结果。现在执行 /step2。以参考文案的史实和已确认策略为依据，"
            "必须优先落实用户在阶段一问答中表达的选择、取舍和修改意见。"
            "全文第一个非空字符必须直接进入下面给出的固定开头；固定开头之前不得添加标题、标签、"
            "引言或任何其他文字，固定开头本身必须逐字、逐句、原顺序保留。"
            "创作历史口播正文，不设目标字数和篇幅上下限；以完整讲清人物命运、历史因果且适合口播为准，"
            "不要为了缩短或凑长而删减关键事实、重复表达或添加空话。彻底重组叙事，不虚构对话；"
            "使用“陈述+设问+解析”"
            f"的循环节奏，并严格按阶段一策略自然植入{formatted_book_title}；"
            "书籍价值必须与本篇历史主题和目标受众相关，不得泛化为任意书籍话术。"
            "带书只允许一个紧凑自然段，带货内容总计不超过200个汉字；"
            "只保留自然过渡、一个核心价值点和一句克制的行动引导，禁止重复介绍卖点、"
            "堆叠阅读感受、连续劝购或用稀缺库存制造压力。"
            f"全文最后一个自然段必须专门介绍{formatted_book_title}并以此结束；"
            "书后不得再追加故事总结、升华、互动提问或其他内容。"
            "只输出完整正文，不重复策略分析。"
            "末尾不要替用户自动进入第三步。\n\n"
            f"<必须逐字置于全文开头的固定文字>\n{fixed_opening}\n"
            "</必须逐字置于全文开头的固定文字>\n\n"
            f"<指定推广书籍>{formatted_book_title}</指定推广书籍>\n\n"
            f"<参考文案>\n{raw_script}\n</参考文案>\n\n"
            f"<已确认的阶段一策略>\n{strategy}\n</已确认的阶段一策略>\n\n"
            f"<用户对阶段一问题的回答>\n{feedback or '用户未补充意见，按阶段一结果执行。'}\n"
            "</用户对阶段一问题的回答>"
        )
    else:
        draft = str(outputs.get("2") or "").strip()
        if not draft:
            raise ValueError("执行 step3 前必须先完成 step2")
        feedback = format_stage_feedback(stage_messages.get("2") or [])
        task = (
            "用户已确认阶段二正文。现在执行 /step3：核查史实风险、原创重构程度和口语流畅度，"
            "并落实用户在阶段二问答中提出的节奏、设问深度和其他修改意见。"
            f"同时检查{formatted_book_title}是否书名准确、植入自然、卖点与历史主题相关、"
            "购买引导不过度生硬；还要检查带货篇幅是否合适：只能保留一个紧凑自然段，"
            "带货内容总计不超过200个汉字，过长、重复或挤压故事时必须直接压缩，"
            f"并确保最后一个自然段专门介绍{formatted_book_title}且作为全文结尾；"
            "书后有任何总结、升华或互动内容都要移到带书段落之前，且不得改推其他书。"
            "直接修正发现的问题。最终只输出可直接录制的定稿口播文案，不输出审核报告、"
            "修改说明或下一步提示。\n\n"
            f"<指定推广书籍>{formatted_book_title}</指定推广书籍>\n\n"
            f"<参考文案>\n{raw_script}\n</参考文案>\n\n"
            f"<待终审正文>\n{draft}\n</待终审正文>\n\n"
            f"<用户对阶段二问题的回答>\n{feedback or '用户未补充意见，按阶段二正文终审。'}\n"
            "</用户对阶段二问题的回答>"
        )
    return [{"role": "system", "content": system}, {"role": "user", "content": task}]


def format_stage_feedback(messages: list[dict[str, str]]) -> str:
    lines = []
    for item in messages[-12:]:
        role = str(item.get("role") or "")
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            speaker = "用户" if role == "user" else "AI助手"
            lines.append(f"{speaker}：{content}")
    return "\n".join(lines)


def generate_history_step(
    step: int,
    raw_script: str,
    outputs: dict[str, str] | None = None,
    stage_messages: dict[str, list[dict[str, str]]] | None = None,
    promotion_book_title: str = "国之脊梁",
    model_provider: str = "minimax",
    return_details: bool = False,
) -> str | dict[str, Any]:
    normalized_provider = normalize_history_model_provider(model_provider)
    messages = build_history_step_messages(
        step,
        raw_script,
        outputs,
        stage_messages,
        promotion_book_title,
    )
    max_tokens = 12000 if step in {2, 3} else 7000
    result = _request_history_ai(
        messages,
        max_tokens=max_tokens,
        provider=normalized_provider,
        reasoning_effort="high" if step in {1, 2, 3} else "medium",
    )
    if step != 2:
        return result

    strategy = str((outputs or {}).get("1") or "").strip()
    fixed_opening = extract_history_fixed_opening(strategy)
    book_title = str(promotion_book_title or "").strip().strip("《》").strip() or "国之脊梁"
    formatted_book_title = f"《{book_title}》"
    draft = result
    review_notes = _step_two_review_notes(draft, fixed_opening, formatted_book_title)
    optimization_prompt = _step_two_optimization_prompt(fixed_opening, formatted_book_title)
    if review_notes:
        optimization_prompt += (
            "\n\n【首稿评分时需重点关注】\n"
            + "\n".join(f"- {item}" for item in review_notes)
            + "\n请将这些观察纳入九维检查，并只修改对应的局部问题。"
        )

    optimized = _request_history_ai(
        [
            *messages,
            {"role": "assistant", "content": draft},
            {"role": "user", "content": optimization_prompt},
        ],
        max_tokens=max_tokens,
        provider=normalized_provider,
        reasoning_effort="medium",
    )
    judgement_raw = _request_history_ai(
        [
            {"role": "system", "content": "你是谨慎的中文口播稿编辑，只负责两稿择优。"},
            {"role": "user", "content": (
                f"{_step_two_judgement_prompt()}\n\n"
                f"<draft_1>\n{draft}\n</draft_1>\n\n"
                f"<draft_2>\n{optimized}\n</draft_2>"
            )},
        ],
        max_tokens=1200,
        provider=normalized_provider,
        reasoning_effort="medium",
    )
    judgement = _parse_step_two_judgement(judgement_raw)
    selected = draft if judgement["winner"] == "draft_1" else optimized
    final_output = selected
    local_edit_applied = False
    local_edit_rejected = False
    local_edit_similarity = 1.0
    if judgement["required_edits"]:
        edited = _request_history_ai(
            [
                {"role": "system", "content": "你是克制的中文文字编辑，只做指定的局部修改。"},
                {"role": "assistant", "content": selected},
                {"role": "user", "content": _step_two_local_edit_prompt(
                    judgement["required_edits"], fixed_opening, formatted_book_title,
                )},
            ],
            max_tokens=max_tokens,
            provider=normalized_provider,
            reasoning_effort="medium",
        )
        local_edit_similarity = round(SequenceMatcher(None, selected, edited).ratio(), 4)
        if local_edit_similarity >= 0.72:
            final_output = edited
            local_edit_applied = True
        else:
            local_edit_rejected = True
    details = {
        "draft_1": draft,
        "draft_2": optimized,
        "final_output": final_output,
        "draft_1_metrics": _draft_metrics(draft, formatted_book_title),
        "draft_2_metrics": _draft_metrics(optimized, formatted_book_title),
        "final_metrics": _draft_metrics(final_output, formatted_book_title),
        "winner": judgement["winner"],
        "reason": judgement["reason"],
        "required_edits": judgement["required_edits"],
        "local_edit_applied": local_edit_applied,
        "local_edit_rejected": local_edit_rejected,
        "local_edit_similarity": local_edit_similarity,
        "review_parse_failed": judgement["review_parse_failed"],
    }
    return details if return_details else final_output


def build_history_chat_messages(
    step: int,
    raw_script: str,
    current_output: str,
    user_message: str,
    history: list[dict[str, str]] | None = None,
    promotion_book_title: str = "国之脊梁",
) -> list[dict[str, str]]:
    if step not in STEP_LABELS:
        raise ValueError("step must be between 1 and 3")
    protocol = load_history_workflow_prompt()
    book_title = str(promotion_book_title or "").strip().strip("《》").strip() or "国之脊梁"
    formatted_book_title = f"《{book_title}》"
    stage_rule = {
        1: "围绕策略、切入视角和开场方案交流，严禁写正文。",
        2: "围绕正文节奏、设问深度和表达取舍交流，严禁执行终审阶段。",
        3: "围绕终审结果和定稿取舍交流，不要另写一版口播稿。",
    }[step]
    messages: list[dict[str, str]] = [{
        "role": "system",
        "content": (
            f"{protocol}\n\n你是阶段{step}（{STEP_LABELS[step]}）的问答助手。"
            f"本项目指定推广书籍是{formatted_book_title}，所有建议必须针对这本书。"
            f"{stage_rule} 用户正在回答当前阶段结果末尾提出的问题。"
            "你只需理解并确认用户的选择或修改意见；必要时提出一个简短追问，"
            "也可以说明该意见会怎样影响下一步。不要重写、替换或输出当前阶段完整结果，"
            "不要提前执行下一阶段。回复应简洁、自然。"
        ),
    }, {
        "role": "user",
        "content": (
            f"<参考文案>\n{raw_script}\n</参考文案>\n\n"
            f"<指定推广书籍>{formatted_book_title}</指定推广书籍>\n\n"
            f"<当前阶段完整结果>\n{current_output}\n</当前阶段完整结果>"
        ),
    }]
    for item in (history or [])[-8:]:
        role = str(item.get("role") or "")
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({
        "role": "user",
        "content": user_message,
    })
    return messages


def revise_history_step(
    step: int,
    raw_script: str,
    current_output: str,
    user_message: str,
    history: list[dict[str, str]] | None = None,
    promotion_book_title: str = "国之脊梁",
    model_provider: str = "minimax",
) -> str:
    return _request_history_ai(
        build_history_chat_messages(
            step,
            raw_script,
            current_output,
            user_message,
            history,
            promotion_book_title,
        ),
        max_tokens=2000,
        provider=normalize_history_model_provider(model_provider),
    )
