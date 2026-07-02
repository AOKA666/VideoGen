# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import base64
import http.client
import json
import mimetypes
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import wave
from difflib import SequenceMatcher
from io import BytesIO
from json import JSONDecoder
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont, ImageOps

from services.store import public_url


STORYBOARD_SEEDREAM_MODEL = "doubao-seedream-4-0-250828"
STORYBOARD_IMAGE_SIZE = "1024x1024"


FEMALE_PERSON_HINTS = {
    "黄令仪", "王承书", "屠呦呦", "林巧稚", "吴健雄", "何泽慧", "叶叔华",
}
MALE_PERSON_HINTS = {
    "钱学森", "邓稼先", "于敏", "黄旭华", "郭永怀", "袁隆平", "王淦昌",
    "赵忠尧", "汤飞凡", "顾维钧", "王伟",
}
PERSON_APPEARANCE_HINTS = {
    "黄令仪": "八十岁左右的中国女性科学家，短灰发，清瘦脸型，戴细框眼镜，穿深色朴素外套，神情专注坚毅",
    "王承书": "中老年中国女性科学家，短发整洁，清瘦脸型，穿朴素年代感外套，神情沉静坚定",
    "屠呦呦": "老年中国女性科学家，短灰发，面容清瘦，穿朴素深色外套，气质沉稳",
    "钱学森": "中老年中国男性科学家，短发整齐，清瘦脸型，穿深色中山装或朴素西装，神情严谨",
    "邓稼先": "中年中国男性科学家，短发，方圆脸型，穿朴素年代感外套，神情沉稳专注",
    "黄旭华": "老年中国男性工程师，短灰发，清瘦脸型，戴眼镜，穿朴素深色外套，神情坚毅",
    "赵忠尧": "中年中国男性科学家，短发，清瘦脸型，穿朴素长衫或年代感外套，神情坚毅",
}
ALL_PERSON_HINTS = FEMALE_PERSON_HINTS | MALE_PERSON_HINTS | set(PERSON_APPEARANCE_HINTS)
COMMON_CHINESE_SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费"
    "廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于傅皮卞齐康伍余元顾孟平黄和穆"
    "萧尹姚邵汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮"
    "蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞"
    "万支柯昝管卢莫经房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉龚程嵇"
    "邢滑裴陆荣翁荀羊甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山"
    "谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘厉戎祖武符刘景詹束龙叶幸司"
    "黎乔苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍郤璩桑桂濮牛寿通边扈燕"
    "冀郏浦尚农温别庄晏柴瞿阎连习容向古易廖庾终暨居衡步都耿满弘匡国"
    "文寇广禄阙东欧沃利蔚越夔隆师巩聂晁勾敖融冷訾辛阚那简饶空曾毋沙"
    "养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)


def infer_shot_person_gender(shot: dict) -> str:
    declared = str(shot.get("person_gender") or "").strip().lower()
    aliases = {
        "female": "female", "女": "female", "女性": "female",
        "male": "male", "男": "male", "男性": "male",
        "mixed": "mixed", "混合": "mixed", "男女": "mixed",
        "none": "none", "无人": "none", "无人物": "none",
        "unknown": "unknown", "未知": "unknown",
    }
    if aliases.get(declared) in {"female", "male", "mixed", "none"}:
        return aliases[declared]

    visual_parts = [
        shot.get("visual_need"),
        *(shot.get("object_tags") or []),
        *(shot.get("required_object") or []),
        *(shot.get("keywords") or []),
    ]
    contexts = [
        " ".join(str(part or "") for part in visual_parts),
        str(shot.get("voice_text") or ""),
    ]
    for context in contexts:
        female = any(name in context for name in FEMALE_PERSON_HINTS) or bool(re.search(
            r"女科学家|女工程师|女军人|女医生|女士|女性|老妇人|老奶奶|母亲|妈妈|妻子|女儿|她(?!们)",
            context,
        ))
        male = any(name in context for name in MALE_PERSON_HINTS) or bool(re.search(
            r"男科学家|男工程师|男军人|男医生|男士|男性|老先生|老爷爷|父亲|爸爸|丈夫|儿子|他(?!们)",
            context,
        ))
        if female and male:
            return "mixed"
        if female:
            return "female"
        if male:
            return "male"
    return "unknown"


def person_gender_instruction(shot: dict) -> str:
    gender = infer_shot_person_gender(shot)
    instructions = {
        "female": (
            "人物性别硬约束：主要人物必须是女性，保持女性面部、体态和身份特征；"
            "严禁把该人物画成男性。"
        ),
        "male": (
            "人物性别硬约束：主要人物必须是男性，保持男性面部、体态和身份特征；"
            "严禁把该人物画成女性。"
        ),
        "mixed": "人物性别硬约束：画面包含男性和女性，按画面描述保留各自正确性别，不得互换。",
    }
    return instructions.get(gender, "")


def _shot_person_names(shot: dict) -> list[str]:
    explicit = [str(name).strip() for name in (shot.get("person_names") or []) if str(name).strip()]
    tagged_names = []
    for item in [
        *(shot.get("object_tags") or []),
        *(shot.get("required_object") or []),
    ]:
        candidate = str(item or "").strip()
        if (
            2 <= len(candidate) <= 4
            and candidate[0] in COMMON_CHINESE_SURNAMES
            and re.fullmatch(r"[\u4e00-\u9fff]+", candidate)
        ):
            tagged_names.append(candidate)
    context = " ".join([
        str(shot.get("visual_need") or ""),
        str(shot.get("voice_text") or ""),
        *[str(item) for item in (shot.get("object_tags") or [])],
        *[str(item) for item in (shot.get("required_object") or [])],
    ])
    inferred = [name for name in ALL_PERSON_HINTS if name in context]
    return list(dict.fromkeys(explicit + tagged_names + inferred))


def _anonymous_person_description(shot: dict, person_names: list[str]) -> str:
    description = str(shot.get("person_description") or "").strip()
    for name in person_names:
        description = description.replace(name, "")
    description = re.sub(r"\s+", " ", description).strip(" ，,。")
    if description:
        return description

    mapped = [PERSON_APPEARANCE_HINTS[name] for name in person_names if name in PERSON_APPEARANCE_HINTS]
    if mapped:
        return "；".join(mapped)

    gender = infer_shot_person_gender(shot)
    age_context = f"{shot.get('visual_need') or ''} {shot.get('voice_text') or ''}"
    age = "老年" if re.search(r"八十|九十|老年|年迈|白发|灰发", age_context) else "中年"
    descriptions = {
        "female": f"{age}中国女性，五官自然，发型朴素，穿符合时代背景的简洁服装，神情真实",
        "male": f"{age}中国男性，五官自然，短发整齐，穿符合时代背景的简洁服装，神情真实",
        "mixed": "不同性别的中国人物，年龄和服装符合画面时代背景，五官自然，神情真实",
    }
    return descriptions.get(gender, "")


def _remove_person_names(text: str, person_names: list[str]) -> str:
    cleaned = str(text or "")
    for name in sorted(person_names, key=len, reverse=True):
        cleaned = cleaned.replace(name, "")
    cleaned = re.sub(r"([，、]\s*){2,}", "，", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" ，,、。")


def generate_svg_placeholder(path: Path, shot: dict) -> str:
    prompt = build_image_prompt(shot)
    title = html.escape(shot.get("visual_need") or "历史纪实画面")
    text = html.escape((shot.get("voice_text") or "")[:38])
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1080" viewBox="0 0 1080 1080">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1f2937"/>
      <stop offset="0.42" stop-color="#5b5347"/>
      <stop offset="1" stop-color="#b8b2a4"/>
    </linearGradient>
    <filter id="grain"><feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves="3" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/></filter>
  </defs>
  <rect width="1080" height="1080" fill="url(#g)"/>
  <rect width="1080" height="1080" opacity="0.18" filter="url(#grain)"/>
  <rect x="92" y="120" width="896" height="660" fill="#111827" opacity="0.28"/>
  <circle cx="540" cy="420" r="160" fill="#e5e7eb" opacity="0.16"/>
  <path d="M280 650 C400 540 680 540 800 650 L800 760 L280 760 Z" fill="#e5e7eb" opacity="0.14"/>
  <text x="92" y="880" fill="#f8fafc" font-size="54" font-family="Arial, sans-serif">{title}</text>
  <text x="92" y="950" fill="#e5e7eb" font-size="30" font-family="Arial, sans-serif">{text}</text>
  <text x="92" y="1760" fill="#d1d5db" font-size="28" font-family="Arial, sans-serif">AI placeholder · archival documentary style</text>
</svg>"""
    path.write_text(svg, encoding="utf-8")
    return prompt


def build_image_prompt(shot: dict) -> str:
    person_names = _shot_person_names(shot)
    visual_need = _remove_person_names(
        str(shot.get("visual_need") or "历史纪实画面"),
        person_names,
    )
    person_description = _anonymous_person_description(shot, person_names)
    gender_instruction = person_gender_instruction(shot)
    return (
        "真实历史纪实影像风格，档案照片质感，克制色彩，电影级构图，"
        f"画面需求：{visual_need or '历史纪实人物场景'}。"
        f"{f'人物外貌：{person_description}。' if person_description else ''}"
        f"{gender_instruction}"
        "不要出现真实人物姓名，不要求复刻任何特定真人的精确面孔。"
        "不要生成文字、水印、Logo，不要夸张奇幻，不要过度美化。"
    )


def ark_endpoint() -> str:
    return os.getenv("ARK_ENDPOINT", "https://ark.cn-beijing.volces.com/api/v3")


def ark_image_model() -> str:
    return os.getenv("ARK_IMAGE_MODEL", STORYBOARD_SEEDREAM_MODEL)


def ark_image_edit_model() -> str:
    return os.getenv("ARK_IMAGE_EDIT_MODEL", ark_image_model())


def image_size_for_ratio(video_ratio: str | None) -> str:
    if video_ratio == "16:9":
        return "2560x1440"
    if video_ratio == "1:1":
        return "1024x1024"
    return "1440x2560"


def storyboard_image_size() -> str:
    return STORYBOARD_IMAGE_SIZE


def image_edit_size_for_source(path: Path) -> str:
    with Image.open(path) as image:
        width, height = image.size
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Image has invalid dimensions: {width}x{height}")

    aspect_ratio = width / height
    if aspect_ratio >= 1.2:
        return image_size_for_ratio("16:9")
    if aspect_ratio <= (1 / 1.2):
        return image_size_for_ratio("9:16")
    return image_size_for_ratio("1:1")


def remove_watermark_with_seedream(path: Path, shot: dict | None = None) -> dict:
    api_key = os.getenv("ARK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ARK_API_KEY is not configured")
    if not path.exists():
        raise RuntimeError(f"Image does not exist: {path}")

    prompt = (
        "仅移除原图中已有的字幕、Logo和水印，并自然修复被遮挡区域。"
        "禁止新增任何文字、Logo、角标、水印、平台标识或“AI生成”标识。"
        "保持原图主体、人物、构图、色彩、光影和画面比例不变。"
    )

    original_bytes = path.read_bytes()
    with Image.open(BytesIO(original_bytes)) as source:
        original = ImageOps.exif_transpose(source)
        original_size = original.size

    image_b64 = base64.b64encode(original_bytes).decode("ascii")
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    image_data_uri = f"data:{mime};base64,{image_b64}"

    payload = {
        "model": ark_image_edit_model(),
        "prompt": prompt,
        "image": image_data_uri,
        "strength": 0.3,
        "n": 1,
        "size": storyboard_image_size(),
        "watermark": False,
        "response_format": "url",
    }
    req = urllib.request.Request(
        f"{ark_endpoint().rstrip('/')}/images/generations",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            response_body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Volcengine Ark image edit API {exc.code}: {error_body}") from exc

    images = response_body.get("data") or response_body.get("images") or []
    image = images[0] if images and isinstance(images[0], dict) else {}
    image_url = image.get("url") or image.get("image_url")
    image_b64 = image.get("b64_json") or image.get("base64")
    if image_url:
        with urllib.request.urlopen(image_url, timeout=180) as response:
            edited = response.read()
    elif image_b64:
        edited = base64.b64decode(image_b64)
    else:
        raise RuntimeError(f"Volcengine Ark image edit response does not contain an image: {response_body}")

    try:
        with Image.open(BytesIO(edited)) as source:
            edited_image = ImageOps.exif_transpose(source)
            edited_image.load()
            if edited_image.size != original_size:
                edited_image = edited_image.resize(original_size, Image.Resampling.LANCZOS)
            suffix = path.suffix.lower()
            save_format = {
                ".png": "PNG",
                ".webp": "WEBP",
            }.get(suffix, "JPEG")
            edited_image = edited_image.convert("RGBA" if save_format == "PNG" else "RGB")
            output = BytesIO()
            save_options = {"quality": 95} if save_format in {"JPEG", "WEBP"} else {}
            edited_image.save(output, format=save_format, **save_options)
            output_bytes = output.getvalue()
    except Exception as exc:
        raise RuntimeError(f"Volcengine Ark returned an invalid edited image: {exc}") from exc

    path.write_bytes(output_bytes)
    return {
        "watermark_checked_by_ai": True,
        "watermark_removed": True,
        "subtitles_removed": True,
        "logos_removed": True,
        "output_watermark_disabled": True,
        "original_size_preserved": True,
        "provider": "volcengine_ark",
        "model": ark_image_edit_model(),
        "image_size": payload["size"],
        "remote_url": image_url or "",
    }


def generate_doubao_image(
    path: Path,
    shot: dict,
    video_ratio: str | None = "9:16",
    prompt_override: str | None = None,
) -> dict:
    api_key = os.getenv("ARK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ARK_API_KEY is not configured")

    person_gender = infer_shot_person_gender(shot)
    prompt = str(prompt_override or "").strip() or build_image_prompt(shot)
    payload = {
        "model": ark_image_model(),
        "prompt": prompt,
        "negative_prompt": "文字，水印，logo，畸形手指，低清晰度，过曝，过度卡通，现代广告感",
        "size": storyboard_image_size(),
        "response_format": "url",
        "watermark": False,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{ark_endpoint().rstrip('/')}/images/generations",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Volcengine Ark API {exc.code}: {error_body}") from exc

    images = body.get("data") or body.get("images") or []
    image_url = images[0].get("url") if images and isinstance(images[0], dict) else ""
    if not image_url:
        raise RuntimeError(f"Volcengine Ark response does not contain an image URL: {body}")

    with urllib.request.urlopen(image_url, timeout=180) as response:
        path.write_bytes(response.read())

    return {
        "prompt": prompt,
        "provider": "volcengine_ark",
        "model": ark_image_model(),
        "remote_url": image_url,
        "image_size": payload["size"],
        "seed": body.get("seed"),
        "person_gender": person_gender,
    }


def generate_seedream_cover(
    path: Path,
    project: dict,
    title: str,
    subtitle: str = "",
    skip_text: bool = False,
) -> dict:
    api_key = os.getenv("ARK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ARK_API_KEY is not configured")

    title = str(title or project.get("name") or "").strip()[:18]
    script = str(project.get("rewritten_script") or project.get("raw_script") or "").strip()
    subtitle = str(subtitle or "").strip()
    if not subtitle:
        candidates = [
            item.strip()
            for item in re.split(r"[。！？!?，,\n]+", script)
            if item.strip() and item.strip() != title
        ]
        subtitle = candidates[0] if candidates else ""
    subtitle = subtitle[:28]
    if skip_text:
        prompt = (
            "为一条中文纪实短视频设计专业竖版封面，画布比例9:16。"
            "根据视频主题设计有冲击力的主体画面，纪实电影海报风格，真实摄影质感，"
            "构图简洁，主体明确，高对比度，适合手机信息流展示。"
            f"视频主题：{project.get('name') or title}。"
            f"文案背景：{script[:260]}。"
            "画面中央偏上区域留出较大空白，不要在画面中放置任何文字、标题或字幕。"
            "不添加Logo、平台角标或水印。"
        )
    else:
        prompt = (
            "为一条中文纪实短视频设计专业竖版封面，画布比例9:16。"
            "根据视频主题设计有冲击力的主体画面，纪实电影海报风格，真实摄影质感，"
            "构图简洁，主体明确，高对比度，适合手机信息流展示。"
            f"视频主题：{project.get('name') or title}。"
            f"文案背景：{script[:260]}。"
            f"封面必须清晰准确地排版主标题「{title}」，"
            "主标题使用高饱和亮黄色中文粗体，并添加清晰醒目的黑色粗描边。"
        )
        if subtitle:
            prompt += (
                f"主标题下方排版较小的副标题「{subtitle}」，"
                "副标题同样使用黄色粗体和黑色描边。"
            )
        prompt += (
            "所有封面文字只能使用黄色字体、黑色描边，不使用白色或其他颜色；"
            "字形端正，不能出现错别字、乱码、重复文字；"
            "文字与背景有清晰层次和足够留白，不添加Logo、平台角标或水印。"
        )
    payload = {
        "model": ark_image_model(),
        "prompt": prompt,
        "negative_prompt": "错别字，乱码，重复文字，英文标题，logo，水印，平台角标，低清晰度，过度卡通",
        "size": image_size_for_ratio("9:16"),
        "response_format": "url",
        "watermark": False,
    }
    request = urllib.request.Request(
        f"{ark_endpoint().rstrip('/')}/images/generations",
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
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Volcengine Ark cover API {exc.code}: {error_body}") from exc

    images = body.get("data") or body.get("images") or []
    image = images[0] if images and isinstance(images[0], dict) else {}
    image_url = image.get("url") or image.get("image_url")
    image_b64 = image.get("b64_json") or image.get("base64")
    if image_url:
        with urllib.request.urlopen(image_url, timeout=180) as response:
            content = response.read()
    elif image_b64:
        content = base64.b64decode(image_b64)
    else:
        raise RuntimeError(f"Volcengine Ark cover response does not contain an image: {body}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "prompt": prompt,
        "provider": "volcengine_ark",
        "model": ark_image_model(),
        "remote_url": image_url or "",
        "image_size": payload["size"],
        "title": title,
        "subtitle": subtitle,
        "seed": body.get("seed"),
    }


def _find_chinese_font(size: int) -> ImageFont.FreeTypeFont:
    """Find a suitable Chinese font, prioritizing 文源圆体, falling back to default."""
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    candidates = [
        project_root / "WenYuanRoundedSCVF.ttf",  # 文源圆体 (bundled)
        Path("C:/Windows/Fonts/WenYuanRoundedSCVF.ttf"),  # 文源圆体 (system)
        "C:/Windows/Fonts/msyhbd.ttc",   # Microsoft YaHei Bold
        "C:/Windows/Fonts/msyh.ttc",      # Microsoft YaHei
        "C:/Windows/Fonts/simhei.ttf",    # SimHei
        "C:/Windows/Fonts/simsun.ttc",    # SimSun
    ]
    for font_path in candidates:
        if isinstance(font_path, str):
            font_path = Path(font_path)
        if font_path.exists():
            try:
                return ImageFont.truetype(str(font_path), size)
            except Exception:
                continue
    return ImageFont.load_default()


def overlay_title_on_cover(cover_path: Path, line1: str, line2: str) -> None:
    """Overlay two-line title text centered on the cover image."""
    with Image.open(cover_path) as img:
        img = img.convert("RGBA")
        width, height = img.size

        # Create a transparent overlay for text
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        font_size = max(60, width // 14)
        font = _find_chinese_font(font_size)

        # Calculate text positions - centered horizontally, positioned in upper-center area
        line1_bbox = draw.textbbox((0, 0), line1, font=font)
        line2_bbox = draw.textbbox((0, 0), line2, font=font)
        line1_w = line1_bbox[2] - line1_bbox[0]
        line2_w = line2_bbox[2] - line2_bbox[0]
        line_h = line1_bbox[3] - line1_bbox[1]

        line_spacing = font_size // 3
        total_text_h = line_h * 2 + line_spacing
        start_y = int(height * 0.35) - total_text_h // 2

        line1_x = (width - line1_w) // 2
        line2_x = (width - line2_w) // 2
        line1_y = start_y
        line2_y = start_y + line_h + line_spacing

        # Draw black stroke (outline) then yellow fill for each line
        stroke_width = max(4, font_size // 15)
        for text, x, y in [(line1, line1_x, line1_y), (line2, line2_x, line2_y)]:
            # Draw stroke in 8 directions
            for dx in range(-stroke_width, stroke_width + 1):
                for dy in range(-stroke_width, stroke_width + 1):
                    if dx * dx + dy * dy <= stroke_width * stroke_width:
                        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 255))
            # Draw main text in bright yellow
            draw.text((x, y), text, font=font, fill=(255, 220, 0, 255))

        result = Image.alpha_composite(img, overlay)
        # Convert back to RGB for PNG save (preserve format)
        result = result.convert("RGB")
        result.save(cover_path, format="PNG", optimize=True)


def compose_uploaded_cover(source_path: Path, cover_path: Path, line1: str, line2: str) -> None:
    """Compose a cover inside a centered 1080x1440 content area."""
    canvas_width, canvas_height = 1080, 1920
    content_top = (canvas_height - 1440) // 2
    content_bottom = content_top + 1440
    portrait_size = 960
    portrait_x = (canvas_width - portrait_size) // 2
    portrait_y = content_top + 72
    corner_radius = 56

    with Image.open(source_path) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        portrait = ImageOps.fit(
            source,
            (portrait_size, portrait_size),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    canvas = Image.new("RGB", (canvas_width, canvas_height), (0, 0, 0))
    portrait_mask = Image.new("L", (portrait_size, portrait_size), 0)
    ImageDraw.Draw(portrait_mask).rounded_rectangle(
        (0, 0, portrait_size - 1, portrait_size - 1),
        radius=corner_radius,
        fill=255,
    )
    canvas.paste(portrait, (portrait_x, portrait_y), portrait_mask)
    draw = ImageDraw.Draw(canvas)

    stroke_width = 8
    line_gap = 30
    title_area_top = portrait_y + portrait_size + 70
    title_area_height = content_bottom - title_area_top
    max_text_width = canvas_width - 80

    font_size = 104
    while True:
        font = _find_chinese_font(font_size)
        try:
            font.set_variation_by_name("Heavy")
        except (AttributeError, OSError):
            pass
        line_boxes = [
            draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
            for line in (line1, line2)
        ]
        line_heights = [box[3] - box[1] for box in line_boxes]
        total_height = sum(line_heights) + line_gap
        widest_line = max(box[2] - box[0] for box in line_boxes)
        if (widest_line <= max_text_width and total_height <= title_area_height) or font_size <= 64:
            break
        font_size -= 4

    line_heights = [box[3] - box[1] for box in line_boxes]
    total_height = sum(line_heights) + line_gap
    y = title_area_top + max(0, (title_area_height - total_height) // 2)

    for line, box, line_height in zip((line1, line2), line_boxes, line_heights):
        line_width = box[2] - box[0]
        x = (canvas_width - line_width) // 2 - box[0]
        draw.text(
            (x, y),
            line,
            font=font,
            fill=(255, 220, 0),
            stroke_width=stroke_width,
            stroke_fill=(0, 0, 0),
        )
        y += line_height + line_gap

    cover_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(cover_path, format="PNG", optimize=True)


def write_silent_wav(path: Path, duration_sec: float) -> None:
    frame_rate = 22050
    frames = int(frame_rate * max(duration_sec, 1))
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(frame_rate)
        wav.writeframes(b"\x00\x00" * frames)


def volc_tts_endpoint() -> str:
    return os.getenv("VOLC_TTS_ENDPOINT", "https://openspeech.bytedance.com/api/v3/tts/unidirectional")


def volc_tts_resource_id() -> str:
    return os.getenv("VOLC_TTS_RESOURCE_ID", "seed-tts-2.0")


def volc_tts_voice() -> str:
    return os.getenv("VOLC_TTS_VOICE", "zh_male_m191_uranus_bigtts")


def parse_chunked_json_objects(text: str) -> list[dict]:
    decoder = JSONDecoder()
    idx = 0
    items = []
    if "\ndata:" in text or text.startswith("data:"):
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data and data != "[DONE]":
                items.append(json.loads(data))
        return items
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        obj, end = decoder.raw_decode(text, idx)
        if isinstance(obj, dict):
            items.append(obj)
        idx = end
    return items


def extract_tts_response(body: bytes) -> dict:
    text = body.decode("utf-8", errors="replace").strip()
    audio = bytearray()
    sentences = []
    for item in parse_chunked_json_objects(text):
        if item.get("code") not in (None, 0, 200, 20000000):
            raise RuntimeError(f"Volcengine TTS error: {item}")
        sentence = item.get("sentence")
        if isinstance(sentence, dict):
            sentences.append(sentence)
        data_obj = item.get("data")
        payload_obj = item.get("payload") or {}
        if isinstance(payload_obj, str):
            try:
                payload_obj = json.loads(payload_obj)
            except json.JSONDecodeError:
                payload_obj = {}
        if isinstance(payload_obj, dict):
            payload_sentences = payload_obj.get("sentences") or []
            if isinstance(payload_sentences, list):
                sentences.extend(item for item in payload_sentences if isinstance(item, dict))
        data = data_obj if isinstance(data_obj, str) else None
        if isinstance(data_obj, dict):
            data = data_obj.get("audio") or data_obj.get("audio_data") or data_obj.get("data")
        data = data or item.get("audio") or item.get("audio_data")
        if isinstance(payload_obj, dict):
            data = data or payload_obj.get("data") or payload_obj.get("audio") or payload_obj.get("audio_data")
        if data:
            audio.extend(base64.b64decode(data))
    if not audio:
        raise RuntimeError(f"Volcengine TTS response does not contain audio data: {text[:500]}")
    return {"audio": bytes(audio), "sentences": sentences}


def extract_audio_from_tts_response(body: bytes) -> bytes:
    return extract_tts_response(body)["audio"]


def read_tts_response(response) -> bytes:
    chunks = []
    while True:
        try:
            chunk = response.read(8192)
        except http.client.IncompleteRead as exc:
            if exc.partial:
                chunks.append(exc.partial)
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def synthesize_volcengine_tts(text: str, voice_type: str | None = None, speech_rate: int | None = None) -> dict:
    api_key = os.getenv("VOLC_TTS_API_KEY", "").strip()
    app_id = os.getenv("VOLC_TTS_APP_ID", "").strip()
    access_key = os.getenv("VOLC_TTS_ACCESS_KEY", "").strip()
    if not api_key and not (app_id and access_key):
        raise RuntimeError("Configure VOLC_TTS_API_KEY or VOLC_TTS_APP_ID + VOLC_TTS_ACCESS_KEY")

    payload = {
        "user": {"uid": "video-draft-generator"},
        "req_params": {
            "text": text,
            "speaker": voice_type or volc_tts_voice(),
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24000,
                "speech_rate": speech_rate if speech_rate is not None else 0,
                "loudness_rate": 0,
                "enable_subtitle": True,
            },
        },
    }
    data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Connection": "keep-alive",
        "X-Api-Resource-Id": volc_tts_resource_id(),
        "X-Api-Request-Id": str(uuid4()),
    }
    if api_key:
        headers["X-Api-Key"] = api_key
    else:
        headers["X-Api-App-Key"] = os.getenv("VOLC_TTS_APP_KEY", "aGjiRDfUWi")
        headers["X-Api-App-Id"] = app_id
        headers["X-Api-Access-Key"] = access_key

    req = urllib.request.Request(
        volc_tts_endpoint(),
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return extract_tts_response(read_tts_response(response))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Volcengine TTS API {exc.code}: {error_body}") from exc


def audio_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=0x08000000,
    )
    return float(result.stdout.strip())


def alignment_text(text: str) -> str:
    return "".join(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", text or ""))


def subtitle_display_text(text: str) -> str:
    return "".join(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", str(text or "")))


def _character_timeline_from_words(words: list[dict], source_text: str, duration: float) -> list[dict]:
    recognized_chars: list[dict] = []
    for word in words:
        chars = list(alignment_text(word.get("text", "")))
        if not chars:
            continue
        start = float(word.get("start") or 0)
        end = float(word.get("end") or start)
        step = max(end - start, 0.001) / len(chars)
        for index, char in enumerate(chars):
            recognized_chars.append({
                "char": char,
                "start": start + step * index,
                "end": start + step * (index + 1),
            })

    source_chars = list(alignment_text(source_text))
    if not source_chars:
        return []
    recognized_text = "".join(item["char"] for item in recognized_chars)
    source_compact = "".join(source_chars)
    matcher = SequenceMatcher(None, source_compact, recognized_text, autojunk=False)
    mapped: list[dict | None] = [None] * len(source_chars)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            mapped[block.a + offset] = recognized_chars[block.b + offset]

    known = [index for index, item in enumerate(mapped) if item is not None]
    for index in range(len(mapped)):
        if mapped[index] is not None:
            continue
        previous = max((known_index for known_index in known if known_index < index), default=None)
        following = min((known_index for known_index in known if known_index > index), default=None)
        left_time = mapped[previous]["end"] if previous is not None else 0.0
        right_time = mapped[following]["start"] if following is not None else duration
        left_index = previous if previous is not None else -1
        right_index = following if following is not None else len(mapped)
        span = max(right_index - left_index, 1)
        start = left_time + (right_time - left_time) * (index - left_index - 1) / span
        end = left_time + (right_time - left_time) * (index - left_index) / span
        mapped[index] = {"char": source_chars[index], "start": start, "end": end}
    return [item for item in mapped if item is not None]


def _official_timestamp_words(sentences: list[dict]) -> list[dict]:
    words = []
    for sentence in sentences:
        for word in sentence.get("words") or []:
            text = word.get("word") or word.get("text") or ""
            start = word.get("startTime", word.get("start_time"))
            end = word.get("endTime", word.get("end_time"))
            if text and start is not None and end is not None:
                words.append({"text": text, "start": float(start), "end": float(end)})
    return words


def _weighted_subtitle_timeline(shots: list[dict], duration: float) -> list[dict]:
    entries = []
    for shot in shots:
        for index, text in enumerate(split_subtitle_text(shot.get("voice_text", "")) or [shot.get("voice_text", "")], 1):
            punctuation = text[-1:] if text else ""
            pause_weight = 2.2 if punctuation in "。！？!?" else 1.35 if punctuation in "，；;：" else 0
            entries.append({
                "shot_id": shot.get("id"),
                "shot_index": shot.get("shot_index"),
                "subtitle_index": index,
                "text": text,
                "weight": max(len(alignment_text(text)), 1) + pause_weight,
            })
    total_weight = sum(item["weight"] for item in entries) or 1
    cursor = 0.0
    for item in entries:
        start = cursor
        cursor += duration * item.pop("weight") / total_weight
        item.update({
            "start_time": start,
            "end_time": cursor,
            "duration_sec": cursor - start,
        })
    if entries:
        entries[-1]["end_time"] = duration
        entries[-1]["duration_sec"] = duration - entries[-1]["start_time"]
    return entries


def align_voice_to_script(path: Path, shots: list[dict], duration: float, sentences: list[dict]) -> dict:
    words = _official_timestamp_words(sentences)

    full_text = "".join(str(shot.get("voice_text") or "") for shot in shots)
    characters = _character_timeline_from_words(words, full_text, duration) if words else []
    if not characters:
        subtitle_timings = _weighted_subtitle_timeline(shots, duration)
        alignment_method = "duration_weighted"
    else:
        subtitle_timings = []
        alignment_method = "volcengine_word_timestamps"

    shot_timings = []
    character_cursor = 0
    for shot in shots:
        shot_text = str(shot.get("voice_text") or "")
        if characters:
            shot_char_count = len(alignment_text(shot_text))
            shot_chars = characters[character_cursor:character_cursor + shot_char_count]
            character_cursor += shot_char_count
            local_cursor = 0
            for subtitle_index, subtitle_text in enumerate(split_subtitle_text(shot_text) or [shot_text], 1):
                subtitle_count = len(alignment_text(subtitle_text))
                subtitle_chars = shot_chars[local_cursor:local_cursor + subtitle_count]
                local_cursor += subtitle_count
                if not subtitle_chars:
                    continue
                subtitle_timings.append({
                    "shot_id": shot.get("id"),
                    "shot_index": shot.get("shot_index"),
                    "subtitle_index": subtitle_index,
                    "text": subtitle_display_text(subtitle_text),
                    "start_time": subtitle_chars[0]["start"],
                    "end_time": subtitle_chars[-1]["end"],
                    "duration_sec": subtitle_chars[-1]["end"] - subtitle_chars[0]["start"],
                })
        own_timings = [item for item in subtitle_timings if item.get("shot_id") == shot.get("id")]
        if own_timings:
            shot_timings.append({
                "shot_id": shot.get("id"),
                "shot_index": shot.get("shot_index"),
                "start_time": own_timings[0]["start_time"],
                "end_time": own_timings[-1]["end_time"],
                "duration_sec": own_timings[-1]["end_time"] - own_timings[0]["start_time"],
                "subtitle_timings": own_timings,
            })
    return {
        "shot_timings": shot_timings,
        "subtitle_timings": subtitle_timings,
        "alignment_method": alignment_method,
    }


def synthesize_project_voice(path: Path, shots: list[dict], voice_type: str | None = None, speech_rate: int | None = None) -> dict:
    legacy_chunk_dir = path.parent / "voice_chunks"
    if legacy_chunk_dir.exists():
        shutil.rmtree(legacy_chunk_dir)
    full_text = "".join(
        str(shot.get("voice_text") or "").strip()
        for shot in shots
        if str(shot.get("voice_text") or "").strip()
    )
    if not full_text:
        raise RuntimeError("No voice text to synthesize")
    response = synthesize_volcengine_tts(full_text, voice_type, speech_rate)
    path.write_bytes(response["audio"])
    final_duration = audio_duration(path)
    alignment = align_voice_to_script(path, shots, final_duration, response.get("sentences") or [])
    return {
        "audio_format": "mp3",
        "voice_type": voice_type or volc_tts_voice(),
        "provider": "volcengine_tts",
        "timestamp_provider": (
            "volcengine_tts_subtitle"
            if alignment.get("alignment_method") == "volcengine_word_timestamps"
            else "duration_weighted"
        ),
        "long_text_error": None,
        "duration_sec": final_duration,
        **alignment,
    }


def format_srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h = ms // 3_600_000
    ms %= 3_600_000
    m = ms // 60_000
    ms %= 60_000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def chinese_char_count(text: str) -> int:
    return len(re.findall(r"[\u3400-\u9fff]", text or ""))


def _split_clause_by_words(clause: str, max_hanzi: int) -> list[str]:
    try:
        import jieba
        tokens = [token for token in jieba.cut(clause, cut_all=False) if token]
    except ImportError:
        tokens = list(clause)

    chunks: list[str] = []
    current: list[str] = []
    current_count = 0
    for token in tokens:
        token_count = chinese_char_count(token)
        if current and current_count + token_count > max_hanzi:
            if token.startswith("的") and len(current) > 1:
                moved = current.pop()
                chunks.append("".join(current).strip())
                current = [moved, token]
                current_count = chinese_char_count(moved) + token_count
                continue
            chunks.append("".join(current).strip())
            current = []
            current_count = 0
        if token_count > max_hanzi:
            for char in token:
                char_count = chinese_char_count(char)
                if current and current_count + char_count > max_hanzi:
                    chunks.append("".join(current).strip())
                    current = []
                    current_count = 0
                current.append(char)
                current_count += char_count
            continue
        current.append(token)
        current_count += token_count
    if current:
        chunks.append("".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def split_subtitle_text(text: str, max_hanzi: int = 9) -> list[str]:
    text = re.sub(r"\s+", "", str(text or "")).strip()
    if not text:
        return []

    clauses = re.findall(r"[^，。！？!?；;：:\n]+[，。！？!?；;：:]?", text)
    chunks: list[str] = []
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        punctuation = ""
        if clause[-1:] in "，。！？!?；;：:":
            punctuation = clause[-1]
            clause = clause[:-1]
        clause_chunks = (
            [clause]
            if chinese_char_count(clause) <= max_hanzi
            else _split_clause_by_words(clause, max_hanzi)
        )
        if punctuation and clause_chunks:
            clause_chunks[-1] += punctuation
        chunks.extend(clause_chunks)

    normalized: list[str] = []
    for chunk in chunks:
        if chunk.startswith("的") and normalized:
            previous = normalized[-1]
            if chinese_char_count(previous) < max_hanzi:
                normalized[-1] = previous + "的"
                chunk = chunk[1:]
        if chunk:
            normalized.append(chunk)
    return normalized


def build_export_subtitles(shots: list[dict], max_hanzi: int = 9) -> list[dict]:
    subtitles: list[dict] = []
    for shot in shots:
        exact_timings = shot.get("subtitle_timings") or []
        if exact_timings:
            for timing in exact_timings:
                text = str(timing.get("text") or "")
                text = subtitle_display_text(text)
                if not text:
                    continue
                subtitles.append({
                    "shot_id": shot.get("id"),
                    "shot_index": shot.get("shot_index"),
                    "start_time": round(float(timing["start_time"]), 3),
                    "end_time": round(float(timing["end_time"]), 3),
                    "text": text,
                    "hanzi_count": chinese_char_count(text),
                })
            continue
        shot_start = float(shot.get("start_time") or 0)
        shot_end = float(shot.get("end_time") or 0)
        if shot_end <= shot_start:
            shot_end = shot_start + float(shot.get("duration_sec") or 3)
        chunks = split_subtitle_text(shot.get("voice_text", ""), max_hanzi=max_hanzi)
        if not chunks:
            continue
        weights = [max(chinese_char_count(chunk), 1) for chunk in chunks]
        total_weight = sum(weights)
        elapsed_weight = 0
        for index, (chunk, weight) in enumerate(zip(chunks, weights)):
            start = shot_start + (shot_end - shot_start) * elapsed_weight / total_weight
            elapsed_weight += weight
            end = (
                shot_end
                if index == len(chunks) - 1
                else shot_start + (shot_end - shot_start) * elapsed_weight / total_weight
            )
            subtitles.append({
                "shot_id": shot.get("id"),
                "shot_index": shot.get("shot_index"),
                "start_time": round(start, 3),
                "end_time": round(end, 3),
                "text": subtitle_display_text(chunk),
                "hanzi_count": chinese_char_count(chunk),
            })
    return subtitles


def generate_export_srt(shots: list[dict], max_hanzi: int = 9) -> str:
    blocks = []
    for idx, subtitle in enumerate(build_export_subtitles(shots, max_hanzi=max_hanzi), 1):
        start = format_srt_time(subtitle["start_time"])
        end = format_srt_time(subtitle["end_time"])
        blocks.append(f"{idx}\n{start} --> {end}\n{subtitle['text']}")
    return "\n\n".join(blocks) + "\n"


def generate_srt(shots: list[dict]) -> str:
    blocks = []
    for idx, shot in enumerate(shots, 1):
        start = format_srt_time(float(shot.get("start_time", 0)))
        end = format_srt_time(float(shot.get("end_time", 0)))
        text = shot.get("voice_text", "")
        blocks.append(f"{idx}\n{start} --> {end}\n{text}")
    return "\n\n".join(blocks) + "\n"


def write_timeline(path: Path, shots: list[dict]) -> None:
    timeline = [
        {
            "shot_index": s.get("shot_index"),
            "start_time": s.get("start_time"),
            "end_time": s.get("end_time"),
            "voice_text": s.get("voice_text"),
            "asset_source": s.get("asset_source"),
            "selected_asset_id": s.get("selected_asset_id"),
        }
        for s in shots
    ]
    path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
