from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image, ImageOps
from pydantic import BaseModel

from services.generation_service import (
    normalize_cover_title_positions,
    strip_product_marker,
    synthesize_volcengine_tts,
)
from services.product_placement_service import (
    MAX_ITEM_DURATION_SEC,
    MAX_ITEMS,
    MIN_ITEM_DURATION_SEC,
    normalize_pip_box,
)
from services.store import assets_dir, db_write_transaction, load_db, public_url, save_db
from services.text_service import generate_viral_title

router = APIRouter(prefix="/api/video-library", tags=["video-library"])

MAX_PRESET_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PRESET_IMAGE_PIXELS = 40_000_000
COVER_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class TitlePreviewPayload(BaseModel):
    script: str


class VoicePreviewPayload(BaseModel):
    script: str = ""
    text: str = ""
    voice_type: str | None = None
    speech_rate: int | None = None


class PlacementPresetItem(BaseModel):
    asset_id: str
    duration_sec: float = 3.0
    fill_duration: bool = True
    pip_box: dict | None = None


class PlacementPresetPayload(BaseModel):
    name: str
    preset_id: str | None = None
    items: list[PlacementPresetItem] = []
    transition_sec: float = 0.5
    pip_box: dict | None = None


def preset_dir() -> Path:
    root = assets_dir() / "presets"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sanitize_preset(preset: dict) -> dict:
    item = {key: value for key, value in preset.items() if key != "cover_source_path"}
    source = Path(str(preset.get("cover_source_path") or ""))
    item["cover_available"] = source.is_file()
    if not item["cover_available"]:
        item["cover_source_url"] = ""
    return item


def _first_sentence(text: str) -> str:
    compact = str(text or "").strip()
    if not compact:
        return ""
    match = re.search(r".+?[。！？!?；;](?:[”’\"']*)", compact, flags=re.S)
    return (match.group(0) if match else compact.splitlines()[0]).strip()[:160]


def _save_upload(file: UploadFile, destination_stem: Path) -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in COVER_SUFFIXES:
        raise ValueError("封面图片支持 PNG、JPG、WEBP")
    temporary = destination_stem.with_name(destination_stem.name + ".upload")
    total = 0
    with temporary.open("wb") as output:
        while chunk := file.file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_PRESET_IMAGE_BYTES:
                temporary.unlink(missing_ok=True)
                raise ValueError("封面图片不能超过 20 MB")
            output.write(chunk)
    if not total:
        temporary.unlink(missing_ok=True)
        raise ValueError("封面图片为空")
    try:
        with Image.open(temporary) as source:
            if source.width * source.height > MAX_PRESET_IMAGE_PIXELS:
                raise ValueError("封面图片像素不能超过 4000 万")
            ImageOps.exif_transpose(source).convert("RGB").load()
        target = destination_stem.with_name(
            f"{destination_stem.name}_{uuid4().hex[:8]}"
        ).with_suffix(suffix)
        os.replace(temporary, target)
        return target
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _resolve_music(db: dict, music_id: str) -> dict | None:
    value = str(music_id or "").strip()
    if not value:
        return None
    music = next((item for item in db.get("music_library", []) if item.get("id") == value), None)
    if not music:
        raise HTTPException(400, "背景音乐不存在")
    return music


@router.get("/presets")
def list_presets():
    db = load_db()
    presets = sorted(
        db.get("video_library_presets", []),
        key=lambda item: str(item.get("updated_at") or ""),
        reverse=True,
    )
    return {"presets": [sanitize_preset(item) for item in presets]}


@router.post("/presets")
def upsert_preset(
    name: str = Form(...),
    preset_id: str = Form(""),
    voice_type: str = Form(""),
    speech_rate: int = Form(0),
    voice_volume: float = Form(1.0),
    background_template_id: str = Form("daodejing"),
    background_music_id: str = Form(""),
    background_music_start_sec: float = Form(0),
    background_music_volume: float = Form(0.2),
    cover_title_positions: str = Form(""),
    cover_mask_opacity: float = Form(0.35),
    cover_source_preset_id: str = Form(""),
    file: UploadFile | None = File(None),
):
    clean_name = str(name or "").strip()
    if not clean_name:
        raise HTTPException(400, "请填写预设名称")
    if len(clean_name) > 60:
        raise HTTPException(400, "预设名称不能超过 60 个字")

    db = load_db()
    from routers.media import templates  # Local import avoids a router import cycle.

    template_id = str(background_template_id or "daodejing")
    if not any(item["id"] == template_id for item in templates()):
        raise HTTPException(400, "背景模板不存在")

    music = _resolve_music(db, background_music_id)
    music_id = music.get("id") if music else None
    music_start = max(0.0, float(background_music_start_sec or 0))
    if music:
        music_start = min(music_start, max(float(music.get("duration_sec") or 0) - 0.1, 0))
    music_volume = max(0.0, min(float(background_music_volume), 1.0))

    try:
        positions = json.loads(cover_title_positions) if cover_title_positions else {}
    except ValueError as exc:
        raise HTTPException(400, "封面文字位置格式错误") from exc
    positions = normalize_cover_title_positions(positions)

    existing = None
    if preset_id:
        existing = next(
            (item for item in db.get("video_library_presets", []) if item.get("id") == preset_id),
            None,
        )
        if not existing:
            raise HTTPException(404, "预设不存在")
    identifier = preset_id or str(uuid4())
    stem = preset_dir() / f"preset_{identifier}"

    new_cover_path: Path | None = None
    try:
        if file is not None:
            new_cover_path = _save_upload(file, stem)
        elif existing and Path(str(existing.get("cover_source_path") or "")).is_file():
            new_cover_path = Path(str(existing["cover_source_path"]))
        elif cover_source_preset_id:
            source_preset = next(
                (
                    item for item in db.get("video_library_presets", [])
                    if item.get("id") == cover_source_preset_id
                ),
                None,
            )
            source = Path(str((source_preset or {}).get("cover_source_path") or ""))
            if not source.is_file() or not source.resolve().is_relative_to(preset_dir().resolve()):
                raise HTTPException(400, "所选预设的封面图片不存在")
            suffix = source.suffix.lower() if source.suffix.lower() in COVER_SUFFIXES else ".png"
            new_cover_path = stem.with_name(f"{stem.name}_{uuid4().hex[:8]}").with_suffix(suffix)
            if source.resolve() != new_cover_path.resolve():
                shutil.copy2(source, new_cover_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"封面图片处理失败：{exc}") from exc

    now = datetime.now().isoformat(timespec="seconds")
    record = {
        "id": identifier,
        "name": clean_name,
        "voice_type": str(voice_type or "").strip(),
        "speech_rate": max(-50, min(int(speech_rate or 0), 50)),
        "voice_volume": round(max(0.0, min(float(voice_volume), 3.0)), 3),
        "background_template_id": template_id,
        "background_music_id": music_id,
        "background_music_start_sec": round(music_start, 3),
        "background_music_volume": round(music_volume, 3),
        "cover_source_path": str(new_cover_path) if new_cover_path else "",
        "cover_source_url": public_url(new_cover_path) if new_cover_path else "",
        "cover_title_positions": positions,
        "cover_mask_opacity": round(max(0.0, min(float(cover_mask_opacity), 0.8)), 3),
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }
    try:
        with db_write_transaction():
            current_db = load_db()
            rows = current_db.setdefault("video_library_presets", [])
            if existing:
                index = next(i for i, item in enumerate(rows) if item.get("id") == identifier)
                rows[index] = record
            else:
                rows.append(record)
            save_db(current_db)
    except Exception:
        if new_cover_path and new_cover_path != Path(str((existing or {}).get("cover_source_path") or "")):
            new_cover_path.unlink(missing_ok=True)
        raise

    old_path = Path(str((existing or {}).get("cover_source_path") or ""))
    if old_path.is_file() and old_path != new_cover_path and old_path.is_relative_to(preset_dir().resolve()):
        old_path.unlink(missing_ok=True)
    return {"status": "success", "preset": sanitize_preset(record)}


@router.delete("/presets/{preset_id}")
def delete_preset(preset_id: str):
    removed = None
    with db_write_transaction():
        db = load_db()
        rows = db.get("video_library_presets", [])
        removed = next((item for item in rows if item.get("id") == preset_id), None)
        if not removed:
            raise HTTPException(404, "预设不存在")
        db["video_library_presets"] = [item for item in rows if item.get("id") != preset_id]
        save_db(db)
    source = Path(str((removed or {}).get("cover_source_path") or ""))
    if source.is_file() and source.is_relative_to(preset_dir().resolve()):
        source.unlink(missing_ok=True)
    return {"status": "deleted", "preset_id": preset_id}


@router.get("/placement-presets")
def list_placement_presets():
    presets = sorted(
        load_db().get("product_placement_presets", []),
        key=lambda item: str(item.get("updated_at") or ""),
        reverse=True,
    )
    return {"presets": presets}


@router.post("/placement-presets")
def upsert_placement_preset(payload: PlacementPresetPayload):
    name = str(payload.name or "").strip()
    if not name:
        raise HTTPException(400, "请填写带货预设名称")
    if len(name) > 60:
        raise HTTPException(400, "带货预设名称不能超过 60 个字")
    if len(payload.items) > MAX_ITEMS:
        raise HTTPException(400, f"带货预设最多 {MAX_ITEMS} 个素材")

    db = load_db()
    assets = {item.get("id"): item for item in db.get("product_library", [])}
    default_box = normalize_pip_box(payload.pip_box)
    items = []
    for index, item in enumerate(payload.items):
        asset = assets.get(str(item.asset_id or ""))
        if not asset:
            raise HTTPException(400, f"第 {index + 1} 个带货素材不存在")
        duration = max(MIN_ITEM_DURATION_SEC, min(float(item.duration_sec or 0), MAX_ITEM_DURATION_SEC))
        source_duration = float(asset.get("duration_sec") or 0)
        if asset.get("media_type") == "video" and source_duration > 0 and not item.fill_duration:
            duration = min(duration, source_duration)
        items.append({
            "asset_id": asset["id"],
            "duration_sec": round(duration, 3),
            "fill_duration": bool(item.fill_duration),
            "pip_box": normalize_pip_box(item.pip_box or default_box),
        })

    existing = None
    if payload.preset_id:
        existing = next(
            (item for item in db.get("product_placement_presets", [])
             if item.get("id") == payload.preset_id),
            None,
        )
        if not existing:
            raise HTTPException(404, "带货预设不存在")
    identifier = payload.preset_id or str(uuid4())
    now = datetime.now().isoformat(timespec="seconds")
    record = {
        "id": identifier,
        "name": name,
        "items": items,
        "transition_sec": round(max(0.0, min(float(payload.transition_sec or 0), 2.0)), 3),
        "pip_box": default_box,
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }
    with db_write_transaction():
        current_db = load_db()
        rows = current_db.setdefault("product_placement_presets", [])
        if existing:
            index = next(i for i, item in enumerate(rows) if item.get("id") == identifier)
            rows[index] = record
        else:
            rows.append(record)
        save_db(current_db)
    return {"status": "success", "preset": record}


@router.delete("/placement-presets/{preset_id}")
def delete_placement_preset(preset_id: str):
    with db_write_transaction():
        db = load_db()
        rows = db.get("product_placement_presets", [])
        if not any(item.get("id") == preset_id for item in rows):
            raise HTTPException(404, "带货预设不存在")
        db["product_placement_presets"] = [
            item for item in rows if item.get("id") != preset_id
        ]
        save_db(db)
    return {"status": "deleted", "preset_id": preset_id}


@router.post("/title-preview")
def title_preview(payload: TitlePreviewPayload):
    script, _, _ = strip_product_marker(str(payload.script or ""))
    script = script.strip()
    if not script:
        raise HTTPException(400, "请先输入文案")
    if len(script) > 50_000:
        raise HTTPException(400, "文案过长，无法生成标题")
    result = generate_viral_title(script)
    if result.get("error"):
        raise HTTPException(502, f"标题生成失败：{result['error']}")
    candidates = result.get("candidates") or []
    if not candidates:
        raise HTTPException(502, "标题生成没有返回候选")
    return {"candidates": candidates}


@router.post("/voice-preview")
def voice_preview(payload: VoicePreviewPayload):
    text = str(payload.text or "").strip() or _first_sentence(payload.script)
    if not text:
        raise HTTPException(400, "没有可试听的文案")
    try:
        result = synthesize_volcengine_tts(text, payload.voice_type, payload.speech_rate)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    return StreamingResponse(
        BytesIO(result["audio"]),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store", "X-Preview-Text-Length": str(len(text))},
    )
