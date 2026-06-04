from __future__ import annotations

import html
import base64
import http.client
import json
import mimetypes
import os
import urllib.error
import urllib.request
import wave
from json import JSONDecoder
from pathlib import Path
from uuid import uuid4

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


def ark_image_edit_model() -> str:
    return os.getenv("ARK_IMAGE_EDIT_MODEL", ark_image_model())


def image_size_for_ratio(video_ratio: str | None) -> str:
    if video_ratio == "16:9":
        return "2560x1440"
    if video_ratio == "1:1":
        return "1920x1920"
    return "1440x2560"


def _multipart_form_data(fields: dict[str, str], files: list[tuple[str, str, str, bytes]]) -> tuple[bytes, str]:
    boundary = f"----VideoGenArkBoundary{uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode("ascii"),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    for name, filename, mime, data in files:
        chunks.extend([
            f"--{boundary}\r\n".encode("ascii"),
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("utf-8"),
            f"Content-Type: {mime}\r\n\r\n".encode("ascii"),
            data,
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def remove_watermark_with_seedream(path: Path, shot: dict | None = None) -> dict:
    api_key = os.getenv("ARK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ARK_API_KEY is not configured")
    if not path.exists():
        raise RuntimeError(f"Image does not exist: {path}")

    prompt = (
        "请对这张图片做写实修复：只移除可见水印、logo、平台署名、角落文字、覆盖式文字标记；"
        "保持主体、构图、色彩、清晰度和画面内容不变，不要新增文字，不要改成插画。"
    )
    if shot:
        prompt += f" 分镜画面语境：{shot.get('visual_need') or shot.get('voice_text') or ''}"
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    body, content_type = _multipart_form_data(
        {
            "model": ark_image_edit_model(),
            "prompt": prompt,
            "response_format": "url",
            "watermark": "false",
        },
        [("image", path.name, mime, path.read_bytes())],
    )
    req = urllib.request.Request(
        f"{ark_endpoint().rstrip('/')}/images/edits",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
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

    path.write_bytes(edited)
    return {
        "watermark_checked_by_ai": True,
        "watermark_removed": True,
        "provider": "volcengine_ark",
        "model": ark_image_edit_model(),
        "remote_url": image_url or "",
    }


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


def extract_audio_from_tts_response(body: bytes) -> bytes:
    text = body.decode("utf-8", errors="replace").strip()
    audio = bytearray()
    for item in parse_chunked_json_objects(text):
        if item.get("code") not in (None, 0, 200, 20000000):
            raise RuntimeError(f"Volcengine TTS error: {item}")
        data_obj = item.get("data")
        payload_obj = item.get("payload") or {}
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
    return bytes(audio)


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


def synthesize_volcengine_tts(text: str, voice_type: str | None = None) -> bytes:
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
                "speech_rate": 0,
                "loudness_rate": 0,
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
            return extract_audio_from_tts_response(read_tts_response(response))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Volcengine TTS API {exc.code}: {error_body}") from exc


def synthesize_project_voice(path: Path, shots: list[dict], voice_type: str | None = None) -> dict:
    chunks = []
    for shot in shots:
        text = str(shot.get("voice_text") or "").strip()
        if not text:
            continue
        chunks.append(synthesize_volcengine_tts(text, voice_type))
    if not chunks:
        raise RuntimeError("No voice text to synthesize")
    path.write_bytes(b"".join(chunks))
    return {
        "audio_format": "mp3",
        "voice_type": voice_type or volc_tts_voice(),
        "provider": "volcengine_tts",
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
