from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

from services.text_service import minimax_endpoint, minimax_model


STEP_LABELS = {
    1: "策略分析",
    2: "正文创作",
    3: "史实与合规终审",
}


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
            return content.split("## 🚀 Initialization (初始化)", 1)[0].rstrip()
    raise RuntimeError("未找到历史创作提示词：二创提示词.txt")


def _request_history_ai(messages: list[dict[str, str]], *, max_tokens: int = 10000) -> str:
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY is not configured")
    payload = {
        "model": minimax_model(),
        "messages": messages,
        "temperature": 0.72,
        "top_p": 0.9,
        "max_tokens": max_tokens,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    request = urllib.request.Request(
        f"{minimax_endpoint().rstrip('/')}/chat/completions",
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
        raise RuntimeError(f"MiniMax API {exc.code}: {detail}") from exc
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
            "【爆款逻辑洞察】【三要素提取表】【切入视角】【3个开场方案】。"
            f"另外输出【{formatted_book_title}带书衔接策略】，说明历史主题与该书的关联、"
            "适合植入的位置、读者价值和自然过渡方式；不得替换成其他书。"
            "末尾简短提醒用户可以继续修改或确认进入第二步，不要写正文。\n\n"
            f"<指定推广书籍>{formatted_book_title}</指定推广书籍>\n\n"
            f"<参考文案>\n{raw_script}\n</参考文案>"
        )
    elif step == 2:
        strategy = str(outputs.get("1") or "").strip()
        if not strategy:
            raise ValueError("执行 step2 前必须先完成 step1")
        feedback = format_stage_feedback(stage_messages.get("1") or [])
        task = (
            "用户已确认阶段一结果。现在执行 /step2。以参考文案的史实和已确认策略为依据，"
            "必须优先落实用户在阶段一问答中表达的选择、取舍和修改意见。"
            "创作1000-1500字历史口播正文。彻底重组叙事，不虚构对话；使用“陈述+设问+解析”"
            f"的循环节奏，并严格按阶段一策略自然植入{formatted_book_title}；"
            "书籍价值必须与本篇历史主题和目标受众相关，不得泛化为任意书籍话术。"
            "只输出完整正文，不重复策略分析。"
            "末尾不要替用户自动进入第三步。\n\n"
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
            "购买引导不过度生硬；发现问题必须直接修正，且不得改推其他书。"
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
) -> str:
    return _request_history_ai(
        build_history_step_messages(
            step,
            raw_script,
            outputs,
            stage_messages,
            promotion_book_title,
        ),
        max_tokens=12000 if step in {2, 3} else 7000,
    )


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
    )
