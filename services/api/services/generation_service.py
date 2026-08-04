# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import base64
import http.client
import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import tempfile
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
STORYBOARD_IMAGE_SIZE = "1440x2560"

SONG_COURT_STYLE_PROMPT = (
    "9:16竖屏，新国风宋式水墨工笔，古绢泛黄宣纸底色，"
    "传统国画白描线条，淡墨晕染肌理，低饱和赭石暖金配色，"
    "无强烈明暗对比，人物为中国古风，"
    "线条细腻流畅，画面庄重肃穆，构图居中均衡，"
    "纯手绘国画质感，无厚涂油画笔触，无CG塑料感，"
    "画面全程无任何文字、字幕、水印、logo，干净留白古画氛围感；"
)

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
    visual_need = str(shot.get("visual_need") or "").strip()
    if not visual_need or visual_need == "待AI生成画面描述":
        raise ValueError("DeepSeek did not return an image prompt for this shot")
    return f"{SONG_COURT_STYLE_PROMPT}具体画面：{visual_need}"


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
        return "1920x1920"
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


def _fit_without_distortion(image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    target_width, target_height = target_size
    source_width, source_height = image.size
    if source_width <= 0 or source_height <= 0:
        raise RuntimeError(f"Edited image has invalid dimensions: {source_width}x{source_height}")
    source_ratio = source_width / source_height
    target_ratio = target_width / target_height
    if abs(source_ratio - target_ratio) <= 0.02:
        return image.resize(target_size, Image.Resampling.LANCZOS)
    return ImageOps.fit(
        image,
        target_size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )


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
    edit_size = image_edit_size_for_source(path)

    image_b64 = base64.b64encode(original_bytes).decode("ascii")
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    image_data_uri = f"data:{mime};base64,{image_b64}"

    payload = {
        "model": ark_image_edit_model(),
        "prompt": prompt,
        "image": image_data_uri,
        "strength": 0.3,
        "n": 1,
        "size": edit_size,
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
                edited_image = _fit_without_distortion(edited_image, original_size)
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
        "original_size": f"{original_size[0]}x{original_size[1]}",
        "aspect_preserved": True,
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

    prompt = str(prompt_override or "").strip() or build_image_prompt(shot)
    payload = {
        "model": ark_image_model(),
        "prompt": prompt,
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
    """Find the Gongfan Nufang cover-title font, falling back safely."""
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    candidates = [
        os.getenv("VIDEOGEN_TITLE_FONT_FILE", "").strip(),
        project_root / "assets" / "龚帆怒放体.ttf",
        os.getenv("VIDEOGEN_FONT_FILE", "").strip(),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",   # Microsoft YaHei Bold
        "C:/Windows/Fonts/msyh.ttc",      # Microsoft YaHei
        "C:/Windows/Fonts/simhei.ttf",    # SimHei
        "C:/Windows/Fonts/simsun.ttc",    # SimSun
    ]
    for font_path in candidates:
        if not font_path:
            continue
        if isinstance(font_path, str):
            font_path = Path(font_path)
        if font_path.exists():
            try:
                return ImageFont.truetype(str(font_path), size)
            except Exception:
                continue
    return ImageFont.load_default()


COVER_TITLE_CENTER_Y_RATIO = 409 / 1920


def _cover_title_start_y(canvas_height: int, total_text_height: int) -> int:
    """Mirror the former lower title center (y=1511) across a 1920px canvas center."""
    title_center_y = round(canvas_height * COVER_TITLE_CENTER_Y_RATIO)
    return max(0, title_center_y - total_text_height // 2)


def cover_title_layout(
    line1: str,
    line2: str,
    canvas_width: int = 1080,
    canvas_height: int = 1920,
) -> dict:
    """Return the shared title geometry used by covers and exported video."""
    scale = canvas_width / 1080
    font_size = max(12, round(124 * scale))
    stroke_width = max(2, round(8 * scale))
    line_gap = max(8, round(30 * scale))
    max_text_width = canvas_width - max(24, round(80 * scale))
    measure = ImageDraw.Draw(Image.new("L", (1, 1)))

    while True:
        font = _find_chinese_font(font_size)
        try:
            font.set_variation_by_name("Heavy")
        except (AttributeError, OSError):
            pass
        boxes = [
            measure.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
            for line in (line1, line2)
        ]
        heights = [box[3] - box[1] for box in boxes]
        widest_line = max((box[2] - box[0] for box in boxes), default=0)
        if widest_line <= max_text_width or font_size <= 12:
            break
        font_size = max(12, font_size - max(1, round(4 * scale)))

    total_height = sum(heights) + (line_gap if line1 and line2 else 0)
    return {
        "font": font,
        "font_size": font_size,
        "stroke_width": stroke_width,
        "line_gap": line_gap,
        "boxes": boxes,
        "heights": heights,
        "start_y": _cover_title_start_y(canvas_height, total_height),
        "underline_width": max(3, round(font_size / 18)),
        "underline_gap": max(3, round(font_size / 16)),
    }


def normalize_cover_title_positions(value: dict | None = None) -> dict:
    """Return safe, normalized positions and sizes for both editable title lines."""
    source = value if isinstance(value, dict) else {}
    defaults = {
        "line1": {"x": 0.5, "y": 0.18, "font_size": 124},
        "line2": {"x": 0.5, "y": 0.25, "font_size": 124},
    }
    normalized: dict[str, dict[str, float | int]] = {}
    for key, fallback in defaults.items():
        line = source.get(key) if isinstance(source.get(key), dict) else {}
        try:
            x = float(line.get("x", fallback["x"]))
            y = float(line.get("y", fallback["y"]))
            font_size = int(round(float(line.get("font_size", fallback["font_size"]))))
        except (TypeError, ValueError):
            x, y, font_size = fallback["x"], fallback["y"], fallback["font_size"]
        normalized[key] = {
            "x": max(0.03, min(0.97, x)),
            "y": max(0.03, min(0.97, y)),
            "font_size": max(32, min(260, font_size)),
        }
    return normalized


def _draw_editable_cover_title(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    line1: str,
    line2: str,
    title_positions: dict,
) -> None:
    scale = width / 1080
    measure = ImageDraw.Draw(Image.new("L", (1, 1)))
    for index, (key, line) in enumerate((("line1", line1), ("line2", line2))):
        if not line:
            continue
        position = title_positions[key]
        font_size = max(12, round(int(position["font_size"]) * scale))
        font = _find_chinese_font(font_size)
        stroke_width = max(2, round(font_size / 15.5))
        box = measure.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
        center_x = float(position["x"]) * width
        center_y = float(position["y"]) * height
        x = round(center_x - (box[0] + box[2]) / 2)
        y = round(center_y - (box[1] + box[3]) / 2)
        fill = (255, 255, 255) if index == 0 else (255, 220, 0)
        draw.text(
            (x, y),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=(0, 0, 0),
        )
        if index == 1:
            underline_y = y + box[3] + max(3, round(font_size / 16))
            underline_width = max(3, round(font_size / 18))
            draw.line(
                (x + box[0], underline_y, x + box[2], underline_y),
                fill=(0, 0, 0),
                width=underline_width + stroke_width * 2,
            )
            draw.line(
                (x + box[0], underline_y, x + box[2], underline_y),
                fill=fill,
                width=underline_width,
            )


def _draw_cover_title(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    line1: str,
    line2: str,
    title_positions: dict | None = None,
) -> None:
    if title_positions is not None:
        _draw_editable_cover_title(
            draw,
            width,
            height,
            line1,
            line2,
            normalize_cover_title_positions(title_positions),
        )
        return
    layout = cover_title_layout(line1, line2, width, height)
    font = layout["font"]
    stroke_width = layout["stroke_width"]
    y = layout["start_y"]
    lines = (line1, line2)
    for index, (line, box, line_height) in enumerate(zip(lines, layout["boxes"], layout["heights"])):
        if not line:
            continue
        line_width = box[2] - box[0]
        x = (width - line_width) // 2 - box[0]
        fill = (255, 255, 255) if index == 0 else (255, 220, 0)
        draw.text(
            (x, y),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=(0, 0, 0),
        )
        if index == 1:
            underline_y = y + box[3] + layout["underline_gap"]
            draw.line(
                (x + box[0], underline_y, x + box[2], underline_y),
                fill=(0, 0, 0),
                width=layout["underline_width"] + stroke_width * 2,
            )
            draw.line(
                (x + box[0], underline_y, x + box[2], underline_y),
                fill=fill,
                width=layout["underline_width"],
            )
        y += line_height + (layout["line_gap"] if index == 0 and line2 else 0)


def render_title_overlay(
    path: Path,
    line1: str,
    line2: str,
    width: int = 1080,
    height: int = 1920,
) -> None:
    """Render the shared cover/video title style to a transparent PNG."""
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    _draw_cover_title(ImageDraw.Draw(overlay), width, height, line1, line2)
    path.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(path, format="PNG", optimize=True)


def overlay_title_on_cover(cover_path: Path, line1: str, line2: str) -> None:
    """Overlay two-line title text centered on the cover image."""
    with Image.open(cover_path) as img:
        img = img.convert("RGBA")
        width, height = img.size

        # Create a transparent overlay for text
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        _draw_cover_title(draw, width, height, line1, line2)

        result = Image.alpha_composite(img, overlay)
        # Convert back to RGB for PNG save (preserve format)
        result = result.convert("RGB")
        result.save(cover_path, format="PNG", optimize=True)


def compose_uploaded_cover(
    source_path: Path,
    cover_path: Path,
    line1: str,
    line2: str,
    title_positions: dict | None = None,
) -> None:
    """Compose a full 9:16 cover without cropping the uploaded image."""
    canvas_width, canvas_height = 1080, 1920

    with Image.open(source_path) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        fitted_cover = ImageOps.contain(
            source,
            (canvas_width, canvas_height),
            method=Image.Resampling.LANCZOS,
        )

    canvas = Image.new("RGB", (canvas_width, canvas_height), (0, 0, 0))
    canvas.paste(
        fitted_cover,
        (
            (canvas_width - fitted_cover.width) // 2,
            (canvas_height - fitted_cover.height) // 2,
        ),
    )
    draw = ImageDraw.Draw(canvas)

    _draw_cover_title(draw, canvas_width, canvas_height, line1, line2, title_positions)

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


def volc_tts_resource_id(voice_type: str | None = None) -> str:
    voice = (voice_type or volc_tts_voice()).strip()
    if voice.startswith("S_"):
        return os.getenv("VOLC_TTS_CLONED_RESOURCE_ID", "seed-icl-2.0")
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

    selected_voice = voice_type or volc_tts_voice()
    payload = {
        "user": {"uid": "video-draft-generator"},
        "req_params": {
            "text": text,
            "speaker": selected_voice,
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
        "X-Api-Resource-Id": volc_tts_resource_id(selected_voice),
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


def expected_lyrics_from_shots(shots: list[dict]) -> list[str]:
    lines: list[str] = []
    for shot in shots:
        text = re.sub(r"\s+", "", str(shot.get("voice_text") or ""))
        chunks = [
            item.strip()
            for item in re.findall(r"[^，。！？!?；;\n]+[，。！？!?；;]?", text)
            if item.strip()
        ]
        if not chunks and text:
            chunks = [text]
        lines.extend(chunk for chunk in chunks if chunk)
    return lines


def whisperx_command() -> list[str]:
    command = os.getenv("WHISPERX_COMMAND", "whisperx").strip() or "whisperx"
    if os.name == "nt" and Path(command).exists():
        return [command]
    return shlex.split(command, posix=(os.name != "nt"))


def _whisperx_json_path(output_dir: Path, audio_path: Path) -> Path:
    exact = output_dir / f"{audio_path.stem}.json"
    if exact.exists():
        return exact
    matches = sorted(output_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not matches:
        raise RuntimeError("WhisperX did not produce a JSON output file")
    return matches[0]


def _extract_whisperx_words(data: dict) -> list[dict]:
    if isinstance(data.get("output"), dict):
        nested_words = _extract_whisperx_words(data["output"])
        if nested_words:
            return nested_words

    words: list[dict] = []
    for item in data.get("word_segments") or []:
        text = item.get("word") or item.get("text") or ""
        start = item.get("start")
        end = item.get("end")
        if text and start is not None and end is not None:
            words.append({"text": str(text), "start": float(start), "end": float(end)})
    if words:
        return words

    segments = data.get("segments") or []
    if isinstance(segments, dict):
        segments = segments.values()
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        for item in segment.get("words") or []:
            text = item.get("word") or item.get("text") or ""
            start = item.get("start")
            end = item.get("end")
            if text and start is not None and end is not None:
                words.append({"text": str(text), "start": float(start), "end": float(end)})
    return words


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _load_replicate_output(output) -> dict:
    if isinstance(output, dict):
        return output
    if isinstance(output, list):
        if output and all(isinstance(item, dict) for item in output):
            return {"segments": output}
        for item in output:
            try:
                return _load_replicate_output(item)
            except RuntimeError:
                continue
    if hasattr(output, "read"):
        raw = output.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        return _load_replicate_output(raw)

    text = str(output or "").strip()
    if not text:
        raise RuntimeError("Replicate WhisperX returned an empty output")
    if text.startswith(("http://", "https://")):
        with urllib.request.urlopen(text, timeout=60) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return _load_replicate_output(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Replicate WhisperX returned an unsupported output format") from exc
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return _load_replicate_output(data)
    raise RuntimeError("Replicate WhisperX returned an unsupported output format")


def _replicate_versioned_model_identifier(model: str, token: str) -> str:
    model = model.strip()
    if not model or ":" in model:
        return model
    parts = model.split("/")
    if len(parts) != 2 or not all(parts):
        return model
    request = urllib.request.Request(
        f"https://api.replicate.com/v1/models/{parts[0]}/{parts[1]}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "VideoGen/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Replicate model lookup failed for {model}: {exc}") from exc
    version_id = ((data.get("latest_version") or {}).get("id") or "").strip()
    if not version_id:
        raise RuntimeError(f"Replicate model lookup did not return a latest version for {model}")
    return f"{model}:{version_id}"


def _lines_from_character_timeline(expected_lines: list[str], characters: list[dict]) -> list[dict]:
    lines: list[dict] = []
    cursor = 0
    for line in expected_lines:
        char_count = len(alignment_text(line))
        line_chars = characters[cursor:cursor + char_count]
        cursor += char_count
        if not line_chars:
            continue
        lines.append({
            "start_time": round(float(line_chars[0]["start"]), 3),
            "end_time": round(float(line_chars[-1]["end"]), 3),
            "duration_sec": round(float(line_chars[-1]["end"]) - float(line_chars[0]["start"]), 3),
            "text": line,
        })
    return lines


def align_lyrics_with_whisperx(audio_path: Path, expected_lines: list[str], duration: float | None = None) -> dict:
    if os.getenv("WHISPERX_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        raise RuntimeError("WhisperX alignment is disabled by WHISPERX_ENABLED=false")
    if not expected_lines:
        raise RuntimeError("No project lyrics are available for alignment")
    if duration is None:
        duration = audio_duration(audio_path)
    command = whisperx_command()
    with tempfile.TemporaryDirectory(prefix="whisperx_align_") as tmp:
        output_dir = Path(tmp)
        args = [
            *command,
            str(audio_path),
            "--model", os.getenv("WHISPERX_MODEL", "small"),
            "--language", os.getenv("WHISPERX_LANGUAGE", "zh"),
            "--device", os.getenv("WHISPERX_DEVICE", "cpu"),
            "--compute_type", os.getenv("WHISPERX_COMPUTE_TYPE", "int8"),
            "--output_format", "json",
            "--output_dir", str(output_dir),
        ]
        extra_args = os.getenv("WHISPERX_EXTRA_ARGS", "").strip()
        if extra_args:
            args.extend(shlex.split(extra_args))
        env = os.environ.copy()
        env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        env.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
        try:
            subprocess.run(
                args,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=0x08000000,
                timeout=int(os.getenv("WHISPERX_TIMEOUT_SEC", "900")),
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("WhisperX is not installed or WHISPERX_COMMAND is not on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"WhisperX timed out after {exc.timeout} seconds") from exc
        except subprocess.CalledProcessError as exc:
            combined_output = "\n".join(part for part in [exc.stdout, exc.stderr] if part)
            raise RuntimeError(f"WhisperX failed: {combined_output[-3000:]}") from exc

        data = json.loads(_whisperx_json_path(output_dir, audio_path).read_text(encoding="utf-8"))
    words = _extract_whisperx_words(data)
    if not words:
        raise RuntimeError("WhisperX did not return word-level timestamps")
    source_text = "".join(expected_lines)
    characters = _character_timeline_from_words(words, source_text, float(duration))
    lines = _lines_from_character_timeline(expected_lines, characters)
    if not lines:
        raise RuntimeError("WhisperX timestamps could not be matched to the project lyrics")
    return {
        "provider": "whisperx",
        "model": os.getenv("WHISPERX_MODEL", "small"),
        "language": os.getenv("WHISPERX_LANGUAGE", "zh"),
        "lines": lines,
        "lrc": lrc_from_lines(lines),
        "word_count": len(words),
    }


def align_lyrics_with_replicate_whisperx(audio_path: Path, expected_lines: list[str], duration: float | None = None) -> dict:
    if not expected_lines:
        raise RuntimeError("No project lyrics are available for alignment")
    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN is not configured")
    if duration is None:
        duration = audio_duration(audio_path)

    try:
        import replicate
    except ImportError as exc:
        raise RuntimeError("Python package 'replicate' is not installed. Run pip install -r services/api/requirements.txt") from exc

    os.environ["REPLICATE_API_TOKEN"] = token
    model = os.getenv("REPLICATE_WHISPERX_MODEL", "victor-upmeet/whisperx").strip() or "victor-upmeet/whisperx"
    model_identifier = _replicate_versioned_model_identifier(model, token)
    language = os.getenv("REPLICATE_WHISPERX_LANGUAGE", os.getenv("WHISPERX_LANGUAGE", "zh")).strip()
    prompt = os.getenv("REPLICATE_WHISPERX_INITIAL_PROMPT", "").strip()

    input_payload = {
        "align_output": True,
        "batch_size": _int_env("REPLICATE_WHISPERX_BATCH_SIZE", 64),
        "diarization": _bool_env("REPLICATE_WHISPERX_DIARIZATION", False),
        "temperature": _float_env("REPLICATE_WHISPERX_TEMPERATURE", 0.0),
        "task": os.getenv("REPLICATE_WHISPERX_TASK", "transcribe").strip() or "transcribe",
    }
    if language:
        input_payload["language"] = language
    if prompt:
        input_payload["initial_prompt"] = prompt

    extra_input = os.getenv("REPLICATE_WHISPERX_INPUT_JSON", "").strip()
    if extra_input:
        try:
            parsed_extra = json.loads(extra_input)
        except json.JSONDecodeError as exc:
            raise RuntimeError("REPLICATE_WHISPERX_INPUT_JSON is not valid JSON") from exc
        if not isinstance(parsed_extra, dict):
            raise RuntimeError("REPLICATE_WHISPERX_INPUT_JSON must be a JSON object")
        input_payload.update(parsed_extra)

    with audio_path.open("rb") as audio_file:
        input_payload["audio_file"] = audio_file
        output = replicate.run(model_identifier, input=input_payload)

    data = _load_replicate_output(output)
    words = _extract_whisperx_words(data)
    if not words:
        raise RuntimeError("Replicate WhisperX did not return word-level timestamps")
    source_text = "".join(expected_lines)
    characters = _character_timeline_from_words(words, source_text, float(duration))
    lines = _lines_from_character_timeline(expected_lines, characters)
    if not lines:
        raise RuntimeError("Replicate WhisperX timestamps could not be matched to the project lyrics")
    return {
        "provider": "replicate_whisperx",
        "model": model_identifier,
        "language": data.get("detected_language") or language,
        "lines": lines,
        "lrc": lrc_from_lines(lines),
        "word_count": len(words),
    }


def align_lyrics(audio_path: Path, expected_lines: list[str], duration: float | None = None) -> dict:
    provider = os.getenv("LYRIC_ALIGNMENT_PROVIDER", "whisperx").strip().lower()
    if provider in {"replicate", "replicate_whisperx", "cloud_whisperx"}:
        return align_lyrics_with_replicate_whisperx(audio_path, expected_lines, duration)
    if provider in {"whisperx", "local", "local_whisperx"}:
        return align_lyrics_with_whisperx(audio_path, expected_lines, duration)
    raise RuntimeError(f"Unsupported LYRIC_ALIGNMENT_PROVIDER: {provider}")


def _parse_lrc_seconds(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    match = re.fullmatch(r"(?:(\d{1,2}):)?(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?", text)
    if match:
        hour = int(match.group(1) or 0)
        minute = int(match.group(2))
        second = int(match.group(3))
        fraction = match.group(4) or "0"
        return hour * 3600 + minute * 60 + second + int(fraction.ljust(3, "0")[:3]) / 1000
    match = re.fullmatch(r"(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?", text)
    if match:
        minute = int(match.group(1))
        second = int(match.group(2))
        fraction = match.group(3) or "0"
        return minute * 60 + second + int(fraction.ljust(3, "0")[:3]) / 1000
    return None


def normalize_lrc_lines(lines: list[dict], lrc_text: str = "") -> list[dict]:
    normalized: list[dict] = []
    for item in lines if isinstance(lines, list) else []:
        text = str(item.get("text") or item.get("lyric") or item.get("line") or "").strip()
        start = item.get("start_time", item.get("timestamp", item.get("time", item.get("start"))))
        if not text:
            continue
        start_time = _parse_lrc_seconds(start)
        if start_time is None:
            continue
        normalized.append({"start_time": round(max(start_time, 0.0), 3), "text": text})
    if not normalized:
        for match in re.finditer(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]([^\r\n]+)", lrc_text or ""):
            minute = int(match.group(1))
            second = int(match.group(2))
            fraction = match.group(3) or "0"
            start_time = minute * 60 + second + int(fraction.ljust(3, "0")[:3]) / 1000
            text = match.group(4).strip()
            if text:
                normalized.append({"start_time": round(start_time, 3), "text": text})
    normalized.sort(key=lambda item: item["start_time"])
    deduped: list[dict] = []
    for item in normalized:
        if deduped and abs(item["start_time"] - deduped[-1]["start_time"]) < 0.05 and item["text"] == deduped[-1]["text"]:
            continue
        deduped.append(item)
    return deduped


def format_lrc_time(seconds: float) -> str:
    total_cs = int(round(max(seconds, 0.0) * 100))
    minute = total_cs // 6000
    second = (total_cs % 6000) // 100
    centisecond = total_cs % 100
    return f"{minute:02d}:{second:02d}.{centisecond:02d}"


def lrc_from_lines(lines: list[dict]) -> str:
    return "\n".join(
        f"[{format_lrc_time(float(item['start_time']))}]{str(item.get('text') or '').strip()}"
        for item in lines
        if str(item.get("text") or "").strip()
    ) + "\n"


def convert_music_to_main_voice(source_path: Path, output_path: Path, start_sec: float = 0.0) -> float:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-y"]
    if start_sec > 0:
        command.extend(["-ss", f"{start_sec:.3f}"])
    command.extend([
        "-i", str(source_path),
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "44100",
        "-b:a", "192k",
        str(output_path),
    ])
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=0x08000000,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required to use music as the main audio") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Failed to prepare music audio: {exc.stderr[-500:]}") from exc
    return audio_duration(output_path)


def align_lyrics_to_shots(lines: list[dict], shots: list[dict], duration: float, source_start_sec: float = 0.0) -> list[dict]:
    adjusted: list[dict] = []
    for index, item in enumerate(lines):
        start = float(item["start_time"]) - source_start_sec
        if start < -0.05 or start > duration:
            continue
        next_start = (
            float(lines[index + 1]["start_time"]) - source_start_sec
            if index + 1 < len(lines)
            else min(duration, max(start + 3.0, duration))
        )
        start = max(start, 0.0)
        end = max(start + 0.25, min(next_start, duration))
        adjusted.append({
            "start_time": round(start, 3),
            "end_time": round(end, 3),
            "duration_sec": round(end - start, 3),
            "text": str(item.get("text") or "").strip(),
        })
    if not adjusted:
        raise RuntimeError("No lyric lines fall inside the selected music range")

    shot_count = max(len(shots), 1)
    expected_counts = [len(expected_lyrics_from_shots([shot])) for shot in shots]
    line_cursor = 0
    shot_timings: list[dict] = []
    for shot_index, shot in enumerate(shots):
        expected_count = expected_counts[shot_index] if shot_index < len(expected_counts) else 0
        if shot_index == len(shots) - 1:
            own_lines = adjusted[line_cursor:]
        else:
            own_lines = adjusted[line_cursor:line_cursor + expected_count]
        line_cursor += expected_count
        if not own_lines:
            start = duration * shot_index / shot_count
            end = duration * (shot_index + 1) / shot_count
            shot_timings.append({
                "shot_id": shot.get("id"),
                "shot_index": shot.get("shot_index"),
                "start_time": round(start, 3),
                "end_time": round(end, 3),
                "duration_sec": round(end - start, 3),
                "subtitle_timings": [],
            })
            continue
        subtitle_timings = []
        for subtitle_index, line in enumerate(own_lines, 1):
            subtitle_timings.append({
                "shot_id": shot.get("id"),
                "shot_index": shot.get("shot_index"),
                "subtitle_index": subtitle_index,
                **line,
            })
        shot_timings.append({
            "shot_id": shot.get("id"),
            "shot_index": shot.get("shot_index"),
            "start_time": subtitle_timings[0]["start_time"],
            "end_time": subtitle_timings[-1]["end_time"],
            "duration_sec": subtitle_timings[-1]["end_time"] - subtitle_timings[0]["start_time"],
            "subtitle_timings": subtitle_timings,
        })
    return shot_timings


def weighted_music_timeline_from_shots(shots: list[dict], duration: float) -> list[dict]:
    subtitle_timings = _weighted_subtitle_timeline(shots, duration)
    shot_timings: list[dict] = []
    for shot in shots:
        own_timings = [item for item in subtitle_timings if item.get("shot_id") == shot.get("id")]
        if not own_timings:
            continue
        shot_timings.append({
            "shot_id": shot.get("id"),
            "shot_index": shot.get("shot_index"),
            "start_time": own_timings[0]["start_time"],
            "end_time": own_timings[-1]["end_time"],
            "duration_sec": own_timings[-1]["end_time"] - own_timings[0]["start_time"],
            "subtitle_timings": own_timings,
        })
    return shot_timings


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


def _timed_export_subtitle_chunks(timing: dict, max_hanzi: int) -> list[dict]:
    raw_text = str(timing.get("text") or "")
    chunks = split_subtitle_text(raw_text, max_hanzi=max_hanzi)
    if not chunks:
        cleaned = subtitle_display_text(raw_text)
        chunks = [cleaned] if cleaned else []
    start = float(timing.get("start_time") or 0)
    end = float(timing.get("end_time") or start)
    if end <= start:
        end = start + 0.08
    weights = [max(chinese_char_count(chunk), 1) for chunk in chunks]
    total_weight = sum(weights) or 1
    elapsed_weight = 0
    subtitles: list[dict] = []
    for index, (chunk, weight) in enumerate(zip(chunks, weights)):
        chunk_text = subtitle_display_text(chunk)
        if not chunk_text:
            elapsed_weight += weight
            continue
        chunk_start = start + (end - start) * elapsed_weight / total_weight
        elapsed_weight += weight
        chunk_end = (
            end
            if index == len(chunks) - 1
            else start + (end - start) * elapsed_weight / total_weight
        )
        subtitles.append({
            "start_time": round(chunk_start, 3),
            "end_time": round(chunk_end, 3),
            "text": chunk_text,
            "hanzi_count": chinese_char_count(chunk_text),
        })
    return subtitles


def build_export_subtitles(shots: list[dict], max_hanzi: int = 9) -> list[dict]:
    subtitles: list[dict] = []
    for shot in shots:
        exact_timings = shot.get("subtitle_timings") or []
        if exact_timings:
            for timing in exact_timings:
                for chunk in _timed_export_subtitle_chunks(timing, max_hanzi):
                    subtitles.append({
                        "shot_id": shot.get("id"),
                        "shot_index": shot.get("shot_index"),
                        **chunk,
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
    return _normalize_export_subtitle_timings(subtitles)


def _normalize_export_subtitle_timings(subtitles: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    previous_end = 0.0
    for subtitle in sorted(subtitles, key=lambda item: (float(item.get("start_time") or 0), float(item.get("end_time") or 0))):
        text = str(subtitle.get("text") or "").strip()
        if not text:
            continue
        start = round(max(float(subtitle.get("start_time") or 0), 0.0), 3)
        end = round(max(float(subtitle.get("end_time") or 0), start + 0.08), 3)
        if normalized and start <= previous_end:
            start = round(previous_end + 0.001, 3)
        if end <= start:
            end = round(start + 0.08, 3)
        item = {
            **subtitle,
            "start_time": start,
            "end_time": end,
        }
        normalized.append(item)
        previous_end = end
    return normalized


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
