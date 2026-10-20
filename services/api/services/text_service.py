# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import urllib.request
from json import JSONDecoder


MAX_AUTO_TITLE_LENGTH = 8
TITLE_PUNCTUATION = re.compile(r"""[，。！？、；："'“”‘’《》【】（）—…\-.!?,;:()\[\]{}<>\s]""")


def minimax_endpoint() -> str:
    return os.getenv("MINIMAX_ENDPOINT", "https://api.minimaxi.com/v1")


def minimax_model() -> str:
    return os.getenv("MINIMAX_MODEL", "MiniMax-M3")


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


def infer_title(raw_script: str) -> str:
    return extract_leading_title(raw_script)


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

    decoder = JSONDecoder()
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
            retry_note = (
                f"\n\n上一版不合格，具体原因：{last_error}。请针对这些原因重新生成，不要重复上一版的问题。"
                if last_error else ""
            )
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
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
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
