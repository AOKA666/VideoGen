from __future__ import annotations

import re


STORYBOARD_STYLE_PROMPT = (
    "9:16竖屏，新国风宋式工笔画，古绢泛黄宣纸底色，"
    "传统国画白描线条，淡墨晕染肌理，低饱和赭石暖金配色，"
    "线条细腻流畅，画面清晰，构图居中均衡，"
    "纯手绘国画质感，无厚涂油画笔触，无CG塑料感，"
    "画面全程无任何文字、字幕、水印、logo，干净留白古画氛围感"
)


_CONFLICTING_VISUAL_STYLE = re.compile(
    r"(?:"
    r"写实(?:历史)?(?:油画)?(?:风格|画风)|"
    r"写实油画|油画(?:风格|画风)|"
    r"真实摄影(?:质感|风格)?|超写实(?:风格|画风)?|"
    r"冷灰(?:色调)?|"
    r"电影感(?:构图|光影|画面|风格)?|"
    r"强烈(?:的)?(?:明暗|光影)(?:对比)?|"
    r"(?:明暗|光影)对比强烈|高对比度?"
    r")",
    flags=re.IGNORECASE,
)


def sanitize_storyboard_visual_prompt(value: object) -> str:
    """Remove model-added style directions that conflict with the shared art style."""
    prompt = re.sub(r"\s+", " ", str(value or "")).strip()
    prompt = _CONFLICTING_VISUAL_STYLE.sub("", prompt)
    prompt = re.sub(r"(?:\s*[，、；;]){2,}", "，", prompt)
    prompt = re.sub(r"[，、；;]+\s*(?=[。！？!?])", "", prompt)
    prompt = re.sub(r"([，、；;])\s*$", "", prompt)
    return prompt.strip()
