# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import http.client
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from json import JSONDecoder
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont, ImageOps

from services.title_style import TITLE_FONT_SIZE_PX


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


def normalize_cover_title_positions(value: dict | None = None) -> dict:
    """Return safe, normalized positions and sizes for both editable title lines."""
    source = value if isinstance(value, dict) else {}
    defaults = {
        "line1": {"x": 0.5, "y": 0.18, "font_size": TITLE_FONT_SIZE_PX},
        "line2": {"x": 0.5, "y": 0.25, "font_size": TITLE_FONT_SIZE_PX},
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


def compose_uploaded_cover(
    source_path: Path,
    cover_path: Path,
    line1: str,
    line2: str,
    title_positions: dict | None = None,
    mask_opacity: float = 0.0,
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
    normalized_mask_opacity = max(0.0, min(1.0, float(mask_opacity)))
    if normalized_mask_opacity:
        canvas = Image.blend(
            canvas,
            Image.new("RGB", canvas.size, (0, 0, 0)),
            normalized_mask_opacity,
        )
    draw = ImageDraw.Draw(canvas)

    _draw_editable_cover_title(
        draw,
        canvas_width,
        canvas_height,
        line1,
        line2,
        normalize_cover_title_positions(title_positions),
    )

    cover_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(cover_path, format="PNG", optimize=True)


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


def _weighted_subtitle_timeline(shots: list[dict], duration: float, max_hanzi: int = 6) -> list[dict]:
    entries = []
    for shot in shots:
        for index, text in enumerate(split_subtitle_text(shot.get("voice_text", ""), max_hanzi=max_hanzi) or [shot.get("voice_text", "")], 1):
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


def align_voice_to_script(path: Path, shots: list[dict], duration: float, sentences: list[dict],
                          max_hanzi: int = 6) -> dict:
    words = _official_timestamp_words(sentences)

    full_text = "".join(str(shot.get("voice_text") or "") for shot in shots)
    characters = _character_timeline_from_words(words, full_text, duration) if words else []
    if not characters:
        subtitle_timings = _weighted_subtitle_timeline(shots, duration, max_hanzi=max_hanzi)
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
            for subtitle_index, subtitle_text in enumerate(split_subtitle_text(shot_text, max_hanzi=max_hanzi) or [shot_text], 1):
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
                    "character_timings": subtitle_chars,
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
    if characters:
        correct_subtitle_onsets(path, subtitle_timings)
        for shot in shot_timings:
            own = shot["subtitle_timings"]
            shot.update(start_time=own[0]["start_time"], end_time=own[-1]["end_time"],
                        duration_sec=own[-1]["end_time"] - own[0]["start_time"])
    return {
        "shot_timings": shot_timings,
        "subtitle_timings": subtitle_timings,
        "alignment_method": alignment_method,
    }


def correct_subtitle_onsets(path: Path, timings: list[dict]) -> int:
    """Recover late sentence onsets only in otherwise uncaptioned audio gaps.

    TTS can timestamp an initial phrase partway through its spoken audio.
    Keep ordinary word boundaries, but use the first speech onset after the
    preceding subtitle when a substantial silence-to-speech boundary exists.
    """
    if not timings or not path.is_file():
        return 0
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path),
         "-af", "silencedetect=noise=-35dB:d=0.18", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, creationflags=0x08000000 if os.name == "nt" else 0,
    )
    if result.returncode:
        raise RuntimeError("无法分析配音起声位置，请检查音频文件")
    boundaries = []
    silence_start = None
    for line in result.stderr.splitlines():
        start_match = re.search(r"silence_start: ([\d.]+)", line)
        end_match = re.search(r"silence_end: ([\d.]+)", line)
        if start_match:
            silence_start = float(start_match.group(1))
        if end_match and silence_start is not None:
            boundaries.append((silence_start, float(end_match.group(1))))
            silence_start = None
    return _correct_subtitle_onsets(timings, boundaries)


SUBTITLE_ONSET_MAX_LAG_SEC = 3.0


def _correct_subtitle_onsets(timings: list[dict], boundaries: list[tuple[float, float]]) -> int:
    corrected = 0
    previous_end = 0.0
    for cue in timings:
        start, end = float(cue["start_time"]), float(cue["end_time"])
        candidates = [
            onset for silence, onset in boundaries
            if silence >= previous_end - 0.08 and onset >= previous_end
            and 0.15 <= start - onset <= SUBTITLE_ONSET_MAX_LAG_SEC
        ]
        if candidates:
            onset = round(min(candidates), 3)
            cue.setdefault("original_start_time", start)
            cue["start_time"] = onset
            cue["duration_sec"] = end - onset
            cue["onset_alignment"] = "audio_silence_boundary"
            # Retain the corrected onset when this cue is split on export.
            if cue.get("character_timings"):
                cue["character_timings"][0]["start"] = onset
            corrected += 1
        previous_end = end
    return corrected


def synthesize_project_voice(path: Path, shots: list[dict], voice_type: str | None = None,
                             speech_rate: int | None = None, max_hanzi: int = 6) -> dict:
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
    alignment = align_voice_to_script(
        path, shots, final_duration, response.get("sentences") or [], max_hanzi=max_hanzi,
    )
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


def split_subtitle_text(text: str, max_hanzi: int = 6) -> list[str]:
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
    characters = timing.get("character_timings") or []
    exact_characters = (
        "".join(item.get("char", "") for item in characters) == alignment_text(raw_text)
        and bool(characters)
    )
    character_cursor = 0
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
        if exact_characters:
            count = len(alignment_text(chunk))
            matched = characters[character_cursor:character_cursor + count]
            character_cursor += count
            if matched:
                chunk_start = float(matched[0]["start"])
                chunk_end = float(matched[-1]["end"])
        subtitles.append({
            "start_time": round(chunk_start, 3),
            "end_time": round(chunk_end, 3),
            "text": chunk_text,
            "hanzi_count": chinese_char_count(chunk_text),
        })
    return subtitles


VIDEO_LIBRARY_SUBTITLE_MAX_HANZI = 9
PRODUCT_MARKER = "【带货开始】"


def strip_product_marker(text: str) -> tuple[str, bool, int]:
    """Remove all placement markers and return the spoken-character offset of the first one."""
    raw = str(text or "")
    index = raw.find(PRODUCT_MARKER)
    if index < 0:
        return raw, False, -1
    clean = raw.replace(PRODUCT_MARKER, "")
    offset = len(alignment_text(raw[:index]))
    return clean, True, offset


def marker_time_from_timings(offset: int, timings: list[dict], duration: float) -> float:
    """Map a spoken-character offset to seconds using chunk-level subtitle timings."""
    if offset <= 0:
        return 0.0
    seen = 0
    ordered = sorted(
        (item for item in (timings or []) if str(item.get("text") or "").strip()),
        key=lambda item: float(item.get("start_time") or 0),
    )
    for timing in ordered:
        count = len(alignment_text(str(timing.get("text") or "")))
        if count <= 0:
            continue
        if offset <= seen + count:
            start = float(timing.get("start_time") or 0)
            end = float(timing.get("end_time") or start)
            return start + (end - start) * (offset - seen) / count
        seen += count
    return max(float(duration or 0), 0.0)


def marker_time_by_weight(text: str, offset: int, duration: float,
                          max_hanzi: int = VIDEO_LIBRARY_SUBTITLE_MAX_HANZI) -> float:
    """Approximate the marker time when the audio has no word/chunk timings."""
    if offset <= 0:
        return 0.0
    chunks = split_subtitle_text(text, max_hanzi=max_hanzi)
    weights = [max(chinese_char_count(chunk), 1) for chunk in chunks]
    total_weight = sum(weights) or 1
    seen_chars = 0
    elapsed_weight = 0
    for chunk, weight in zip(chunks, weights):
        count = len(alignment_text(chunk))
        if count and offset <= seen_chars + count:
            start = duration * elapsed_weight / total_weight
            end = duration * (elapsed_weight + weight) / total_weight
            return start + (end - start) * (offset - seen_chars) / count
        seen_chars += count
        elapsed_weight += weight
    return max(float(duration or 0), 0.0)


def marker_start_sec(text: str, offset: int, duration: float,
                     subtitle_timings: list[dict] | None = None) -> float | None:
    try:
        total_duration = float(duration or 0)
    except (TypeError, ValueError):
        return None
    if offset is None or offset < 0 or total_duration <= 0:
        return None
    if subtitle_timings and any(str(item.get("text") or "").strip() for item in subtitle_timings):
        value = marker_time_from_timings(offset, subtitle_timings, total_duration)
    else:
        value = marker_time_by_weight(text, offset, total_duration)
    return round(max(0.0, min(value, total_duration)), 3)


def strip_marker_from_timings(timings: list[dict]) -> list[dict]:
    cleaned = []
    for timing in timings or []:
        text = str(timing.get("text") or "").replace(PRODUCT_MARKER, "")
        if text.strip():
            cleaned.append({**timing, "text": text})
    return cleaned


def narration_shot(project: dict) -> dict:
    """The video-library workflow narrates the whole script as one continuous track."""
    stored = str(project.get("narration_script") or "")
    if stored:
        script = stored.replace(PRODUCT_MARKER, "")
    else:
        script, _, _ = strip_product_marker(
            str(project.get("raw_script") or "")
        )
    timings = strip_marker_from_timings(
        project.get("narration_subtitle_timings") or []
    )
    audio_path = Path(str(project.get("audio_path") or ""))
    if timings and audio_path.is_file():
        # Projects voiced before onset correction keep stale timings on disk.
        # Re-derive the onsets from the audio so export fixes them as well.
        try:
            correct_subtitle_onsets(audio_path, timings)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass
    return {
        "id": "narration",
        "shot_index": 1,
        "voice_text": script,
        "start_time": 0.0,
        "end_time": float(project.get("audio_duration_sec") or 0),
        "subtitle_timings": timings,
    }


def build_export_subtitles(shots: list[dict], max_hanzi: int = 6) -> list[dict]:
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
    for subtitle in sorted(subtitles, key=lambda item: (float(item.get("start_time") or 0), float(item.get("end_time") or 0))):
        text = str(subtitle.get("text") or "").strip()
        if not text:
            continue
        start = round(max(float(subtitle.get("start_time") or 0), 0.0), 3)
        end = round(max(float(subtitle.get("end_time") or 0), start + 0.001), 3)
        # Preserve the next spoken onset. Moving it after the previous end
        # causes cumulative lag; shorten the previous display instead.
        if normalized and start == normalized[-1]["start_time"]:
            normalized[-1]["text"] += text
            normalized[-1]["end_time"] = max(normalized[-1]["end_time"], end)
            continue
        if normalized and normalized[-1]["end_time"] > start:
            normalized[-1]["end_time"] = start
        item = {
            **subtitle,
            "start_time": start,
            "end_time": end,
        }
        normalized.append(item)
    return normalized


def generate_export_srt(shots: list[dict], max_hanzi: int = 6) -> str:
    blocks = []
    for idx, subtitle in enumerate(build_export_subtitles(shots, max_hanzi=max_hanzi), 1):
        start = format_srt_time(subtitle["start_time"])
        end = format_srt_time(subtitle["end_time"])
        blocks.append(f"{idx}\n{start} --> {end}\n{subtitle['text']}")
    return "\n\n".join(blocks) + "\n"
