from __future__ import annotations

import html
import json
import os
import urllib.error
import urllib.request
import wave
from pathlib import Path

from services.store import public_url


def generate_svg_placeholder(path: Path, shot: dict) -> str:
    prompt = build_image_prompt(shot)
    title = html.escape(shot.get("visual_need") or "历史纪实画面")
    text = html.escape((shot.get("voice_text") or "")[:38])
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1920" viewBox="0 0 1080 1920">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1f2937"/>
      <stop offset="0.42" stop-color="#5b5347"/>
      <stop offset="1" stop-color="#b8b2a4"/>
    </linearGradient>
    <filter id="grain"><feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves="3" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/></filter>
  </defs>
  <rect width="1080" height="1920" fill="url(#g)"/>
  <rect width="1080" height="1920" opacity="0.18" filter="url(#grain)"/>
  <rect x="92" y="210" width="896" height="1280" fill="#111827" opacity="0.28"/>
  <circle cx="540" cy="760" r="210" fill="#e5e7eb" opacity="0.16"/>
  <path d="M260 1180 C390 1040 710 1040 840 1180 L840 1340 L260 1340 Z" fill="#e5e7eb" opacity="0.14"/>
  <text x="92" y="1600" fill="#f8fafc" font-size="58" font-family="Arial, sans-serif">{title}</text>
  <text x="92" y="1690" fill="#e5e7eb" font-size="34" font-family="Arial, sans-serif">{text}</text>
  <text x="92" y="1760" fill="#d1d5db" font-size="28" font-family="Arial, sans-serif">AI placeholder · archival documentary style</text>
</svg>"""
    path.write_text(svg, encoding="utf-8")
    return prompt


def build_image_prompt(shot: dict) -> str:
    return (
        "真实历史纪实影像风格，档案照片质感，克制色彩，电影级构图，"
        f"画面需求：{shot.get('visual_need', '历史纪实画面')}。"
        f"旁白语境：{shot.get('voice_text', '')}。"
        "不要生成文字、水印、Logo，不要夸张奇幻，不要过度美化。"
    )


def ark_endpoint() -> str:
    return os.getenv("ARK_ENDPOINT", "https://ark.cn-beijing.volces.com/api/v3")


def ark_image_model() -> str:
    return os.getenv("ARK_IMAGE_MODEL", "doubao-seedream-4.5")


def image_size_for_ratio(video_ratio: str | None) -> str:
    if video_ratio == "16:9":
        return "1664x928"
    if video_ratio == "1:1":
        return "1328x1328"
    return "928x1664"


def generate_doubao_image(path: Path, shot: dict, video_ratio: str | None = "9:16") -> dict:
    api_key = os.getenv("ARK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ARK_API_KEY is not configured")

    prompt = build_image_prompt(shot)
    payload = {
        "model": ark_image_model(),
        "prompt": prompt,
        "negative_prompt": "文字，水印，logo，畸形手指，低清晰度，过曝，过度卡通，现代广告感",
        "size": image_size_for_ratio(video_ratio),
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


def write_silent_wav(path: Path, duration_sec: float) -> None:
    frame_rate = 22050
    frames = int(frame_rate * max(duration_sec, 1))
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(frame_rate)
        wav.writeframes(b"\x00\x00" * frames)


def format_srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h = ms // 3_600_000
    ms %= 3_600_000
    m = ms // 60_000
    ms %= 60_000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


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
