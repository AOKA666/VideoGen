from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from PIL import Image, ImageOps

from services.store import assets_dir, db_write_transaction, load_db, project_dir, public_url, save_db
from services.generation_service import (
    build_export_subtitles,
    generate_export_srt,
    strip_product_marker,
    synthesize_project_voice,
)
from services.media_service import AUDIO_SUFFIXES, assemble_clips, probe_audio, probe_video, render_video
from services.product_placement_service import normalize_product_placement

router = APIRouter(prefix="/api", tags=["media"])
ACTIVE: set[str] = set()
LOCK = threading.Lock()

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
PRODUCT_VIDEO_MAX_BYTES = 1024 * 1024 * 1024
PRODUCT_IMAGE_MAX_BYTES = 20 * 1024 * 1024
PRODUCT_IMAGE_MAX_PIXELS = 40_000_000


class AssemblePayload(BaseModel):
    audio_source: str | None = None


class ProductPipBox(BaseModel):
    x: float = 0.62
    y: float = 0.31
    w: float = 0.34
    h: float = 0.22


class ProductPlacementItem(BaseModel):
    asset_id: str
    duration_sec: float = 3.0
    pip_box: ProductPipBox | None = None


class ProductPlacementPayload(BaseModel):
    items: list[ProductPlacementItem] = Field(default_factory=list)
    transition_sec: float = 0.5
    pip_box: ProductPipBox = Field(default_factory=ProductPipBox)


def templates() -> list[dict]:
    root = assets_dir() / "templates"
    root.mkdir(parents=True, exist_ok=True)
    builtin = root / "daodejing.png"
    if not builtin.exists():
        shutil.copy2(Path(__file__).resolve().parents[3] / "assets" / "template-daodejing.png", builtin)
    plain = root / "plain.png"
    if not plain.exists():
        Image.new("RGB", (1080, 1920), "#073f3b").save(plain)
    defaults = [{"id": "daodejing", "name": "道德经", "local_path": str(builtin), "file_url": public_url(builtin)},
                {"id": "plain", "name": "纯色墨绿", "local_path": str(plain), "file_url": public_url(plain)}]
    return defaults + load_db().get("video_templates", [])


def get_project(db: dict, project_id: str) -> dict:
    project = next((p for p in db["projects"] if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "项目不存在")
    if project.get("workflow_type") != "video_library":
        raise HTTPException(400, "请从项目页创建素材视频项目")
    return project


def write_upload(file: UploadFile, target: Path, limit: int):
    total = 0
    with target.open("wb") as output:
        while chunk := file.file.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValueError(f"文件不能超过 {limit // 1024 // 1024} MB")
            output.write(chunk)
    if not total:
        raise ValueError("上传文件为空")


@router.get("/media")
def list_media():
    db = load_db()
    return {
        "videos": [a for a in db.get("video_library", []) if Path(a["local_path"]).is_file()],
        "products": [a for a in db.get("product_library", []) if Path(a["local_path"]).is_file()],
        "templates": templates(),
    }


@router.post("/media/videos")
def upload_video(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
        raise HTTPException(400, "支持 MP4、MOV、MKV、WEBM、AVI、M4V 视频")
    root = assets_dir() / "videos"
    root.mkdir(parents=True, exist_ok=True)
    identifier = str(uuid4())
    target = root / (identifier + suffix)
    try:
        write_upload(file, target, 1024 * 1024 * 1024)
        metadata = probe_video(target)
        item = {"id": identifier, "file_name": Path(file.filename).name, "local_path": str(target),
                "file_url": public_url(target), "file_size": target.stat().st_size, **metadata}
        with db_write_transaction():
            db = load_db()
            db.setdefault("video_library", []).append(item)
            save_db(db)
        return item
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400, f"视频上传失败：{exc}") from exc


@router.delete("/media/videos/{asset_id}")
def delete_video(asset_id: str):
    with db_write_transaction():
        db = load_db()
        if any(p.get("status") in {"video_generating", "video_rendering"} for p in db["projects"]):
            raise HTTPException(409, "正在生成视频，请完成后再删除素材")
        if any(s.get("selected_asset_id") == asset_id for s in db["shots"]):
            raise HTTPException(409, "素材已被项目使用，暂时不能删除")
        asset = next((a for a in db.get("video_library", []) if a["id"] == asset_id), None)
        if not asset:
            raise HTTPException(404, "素材不存在")
        db["video_library"] = [a for a in db["video_library"] if a["id"] != asset_id]
        save_db(db)
        target = Path(asset["local_path"]).resolve()
        if target.is_relative_to((assets_dir() / "videos").resolve()):
            target.unlink(missing_ok=True)
    return {"status": "deleted"}


@router.post("/media/products")
def upload_product(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    identifier = str(uuid4())
    temporary = None
    target = None
    try:
        if suffix in VIDEO_SUFFIXES:
            root = assets_dir() / "product_library" / "videos"
            root.mkdir(parents=True, exist_ok=True)
            target = root / (identifier + suffix)
            write_upload(file, target, PRODUCT_VIDEO_MAX_BYTES)
            metadata = probe_video(target)
            item = {
                "id": identifier,
                "media_type": "video",
                "file_name": Path(file.filename or "带货视频").name,
                "local_path": str(target),
                "file_url": public_url(target),
                "file_size": target.stat().st_size,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                **metadata,
            }
        elif suffix in IMAGE_SUFFIXES:
            root = assets_dir() / "product_library" / "images"
            root.mkdir(parents=True, exist_ok=True)
            temporary = root / (identifier + ".upload")
            target = root / (identifier + ".png")
            write_upload(file, temporary, PRODUCT_IMAGE_MAX_BYTES)
            with Image.open(temporary) as source:
                if source.width * source.height > PRODUCT_IMAGE_MAX_PIXELS:
                    raise ValueError("图片像素不能超过 4000 万")
                image = ImageOps.exif_transpose(source).convert("RGBA")
                width, height = image.size
                image.save(target, format="PNG", optimize=True)
            item = {
                "id": identifier,
                "media_type": "image",
                "file_name": Path(file.filename or "带货图片").name,
                "local_path": str(target),
                "file_url": public_url(target),
                "file_size": target.stat().st_size,
                "width": width,
                "height": height,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        else:
            raise ValueError("支持 MP4/MOV/MKV/WEBM/AVI/M4V 视频，或 PNG/JPG/WEBP 图片")
        with db_write_transaction():
            db = load_db()
            db.setdefault("product_library", []).append(item)
            save_db(db)
        return item
    except Exception as exc:
        if target:
            target.unlink(missing_ok=True)
        raise HTTPException(400, f"带货素材上传失败：{exc}") from exc
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


@router.delete("/media/products/{asset_id}")
def delete_product(asset_id: str):
    with db_write_transaction():
        db = load_db()
        if any(
            item.get("asset_id") == asset_id
            for project in db.get("projects", [])
            for item in ((project.get("product_placement") or {}).get("items") or [])
        ):
            raise HTTPException(409, "带货素材已被项目使用，暂时不能删除")
        if any(
            item.get("asset_id") == asset_id
            for preset in db.get("product_placement_presets", [])
            for item in (preset.get("items") or [])
        ):
            raise HTTPException(409, "带货素材已被带货预设使用，暂时不能删除")
        asset = next((item for item in db.get("product_library", []) if item.get("id") == asset_id), None)
        if not asset:
            raise HTTPException(404, "带货素材不存在")
        db["product_library"] = [
            item for item in db.get("product_library", []) if item.get("id") != asset_id
        ]
        save_db(db)
        target = Path(asset["local_path"]).resolve()
        if target.is_relative_to((assets_dir() / "product_library").resolve()):
            target.unlink(missing_ok=True)
    return {"status": "deleted"}


@router.patch("/projects/{project_id}/product-placement")
def update_product_placement(project_id: str, payload: ProductPlacementPayload):
    db = load_db()
    project = get_project(db, project_id)
    try:
        placement = normalize_product_placement(
            db,
            project,
            [item.model_dump() for item in payload.items],
            payload.transition_sec,
            payload.pip_box.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    now = datetime.now().isoformat(timespec="seconds")
    placement["updated_at"] = now
    with db_write_transaction():
        current_db = load_db()
        current = get_project(current_db, project_id)
        current["product_placement"] = placement
        current["updated_at"] = now
        save_db(current_db)
    return {"status": "saved", "product_placement": placement}


@router.post("/media/templates")
def upload_template(file: UploadFile = File(...)):
    root = assets_dir() / "templates"
    root.mkdir(parents=True, exist_ok=True)
    identifier = str(uuid4())
    temporary = root / (identifier + ".upload")
    target = root / (identifier + ".png")
    try:
        write_upload(file, temporary, 20 * 1024 * 1024)
        with Image.open(temporary) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError("模板像素不能超过 4000 万")
            image = ImageOps.exif_transpose(source).convert("RGB")
            if abs(image.width / image.height - 9 / 16) > 0.02:
                raise ValueError("请上传 9:16 竖屏背景模板")
            image.resize((1080, 1920), Image.Resampling.LANCZOS).save(target)
        item = {"id": identifier, "name": Path(file.filename or "自定义模板").stem,
                "local_path": str(target), "file_url": public_url(target)}
        with db_write_transaction():
            db = load_db()
            db.setdefault("video_templates", []).append(item)
            save_db(db)
        return item
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400, f"模板上传失败：{exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def update_project(project_id: str, values: dict):
    with db_write_transaction():
        db = load_db()
        get_project(db, project_id).update({**values, "updated_at": datetime.now().isoformat(timespec="seconds")})
        save_db(db)


def generate_worker(project_id: str, audio_source: str = "tts"):
    try:
        db = load_db()
        project = get_project(db, project_id)
        assets = [a for a in db.get("video_library", []) if Path(a["local_path"]).is_file()]
        base = project_dir(project_id)
        voice_dir = base / "voice"
        voice_dir.mkdir(exist_ok=True)
        narration_script, _, _ = strip_product_marker(project["raw_script"])
        voice_shot = {"id": "narration", "shot_index": 1, "voice_text": narration_script}
        if audio_source == "upload":
            audio = Path(project.get("uploaded_audio_path") or "")
            if not audio.is_file():
                raise ValueError("上传的音频文件不存在，请重新上传")
            duration = float(project.get("uploaded_audio_duration_sec") or probe_audio(audio))
            # Uploaded voiceovers have no word timestamps, so subtitles are weighted by text length.
            result = {"duration_sec": duration, "subtitle_timings": [], "audio_format": audio.suffix.lstrip("."),
                      "provider": "uploaded_audio", "voice_type": project.get("voice_type"),
                      "timestamp_provider": "duration_weighted", "long_text_error": None}
        else:
            audio = voice_dir / (uuid4().hex + ".mp3")
            result = synthesize_project_voice(audio, [voice_shot], project.get("voice_type"), project.get("speech_rate", 0))
        shots = assemble_clips(assets, result["duration_sec"], project_id)
        voice_shot.update({"start_time": 0, "end_time": result["duration_sec"],
                           "subtitle_timings": result["subtitle_timings"]})
        subtitle = voice_dir / (audio.stem + ".srt")
        subtitle.write_text(generate_export_srt([voice_shot], max_hanzi=9), encoding="utf-8")
        timeline = voice_dir / (audio.stem + ".json")
        timeline.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        with db_write_transaction():
            db = load_db()
            p = get_project(db, project_id)
            db["shots"] = [s for s in db["shots"] if s["project_id"] != project_id] + shots
            p.update({"status": "video_ready", "audio_url": public_url(audio), "audio_path": str(audio),
                      "audio_duration_sec": result["duration_sec"], "audio_source": audio_source,
                      "voice_timeline_url": public_url(timeline),
                      "subtitle_url": public_url(subtitle), "render_url": None, "generation_error": "",
                      "updated_at": datetime.now().isoformat(timespec="seconds")})
            save_db(db)
        # One click from the script: voice, clip matching and the final MP4 all happen in this job.
        update_project(project_id, {"status": "video_rendering"})
        render_project(project_id)
    except Exception as exc:
        update_project(project_id, {"status": "video_failed", "generation_error": str(exc)})
    finally:
        with LOCK:
            ACTIVE.discard(project_id)


def start_job(project_id: str, worker, status: str):
    with LOCK:
        if project_id in ACTIVE:
            raise HTTPException(409, "项目正在处理中，请稍候")
        ACTIVE.add(project_id)
    try:
        update_project(project_id, {"status": status, "generation_error": "", "render_progress": None})
        threading.Thread(target=worker, args=(project_id,), daemon=True).start()
    except Exception:
        with LOCK:
            ACTIVE.discard(project_id)
        raise
    return {"status": status}


@router.post("/projects/{project_id}/audio")
def upload_project_audio(project_id: str, file: UploadFile = File(...)):
    db = load_db()
    project = get_project(db, project_id)
    with LOCK:
        if project_id in ACTIVE:
            raise HTTPException(409, "项目正在处理中，请稍候")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in AUDIO_SUFFIXES:
        raise HTTPException(400, "支持 MP3、WAV、M4A、AAC、OGG、FLAC、WMA 音频")
    voice_dir = project_dir(project_id) / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    target = voice_dir / (uuid4().hex + suffix)
    try:
        write_upload(file, target, 500 * 1024 * 1024)
        duration = probe_audio(target)
        values = {"audio_source": "upload", "uploaded_audio_path": str(target),
                  "uploaded_audio_url": public_url(target), "uploaded_audio_duration_sec": duration,
                  "uploaded_audio_name": Path(file.filename or "上传音频").name}
        update_project(project_id, values)
        previous = Path(project.get("uploaded_audio_path") or "").resolve()
        if previous.is_file() and previous != target.resolve() and previous.is_relative_to(project_dir(project_id).resolve()):
            previous.unlink(missing_ok=True)
        return {"file_name": values["uploaded_audio_name"], "duration_sec": duration,
                "file_url": values["uploaded_audio_url"], "audio_source": "upload"}
    except Exception as exc:
        target.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(400, f"音频上传失败：{exc}") from exc


@router.post("/projects/{project_id}/assemble-video")
def generate_video(project_id: str, payload: AssemblePayload | None = None):
    db = load_db()
    project = get_project(db, project_id)
    if not project.get("raw_script", "").strip():
        raise HTTPException(400, "请先输入文案")
    audio_source = (payload.audio_source if payload else None) or project.get("audio_source") or "tts"
    if audio_source not in {"tts", "upload"}:
        raise HTTPException(400, "音频来源无效")
    if audio_source == "upload" and not Path(project.get("uploaded_audio_path") or "").is_file():
        raise HTTPException(400, "请先上传音频")
    if not any(Path(a["local_path"]).is_file() for a in db.get("video_library", [])):
        raise HTTPException(400, "请先到素材库上传视频")
    if not any(t["id"] == project.get("background_template_id") for t in templates()):
        raise HTTPException(400, "背景模板不存在")
    update_project(project_id, {"audio_source": audio_source})
    return start_job(project_id, lambda pid: generate_worker(pid, audio_source), "video_generating")


@router.post("/projects/{project_id}/reshuffle-video")
def reshuffle_video(project_id: str):
    with LOCK, db_write_transaction():
        if project_id in ACTIVE:
            raise HTTPException(409, "项目正在处理中")
        db = load_db()
        p = get_project(db, project_id)
        try:
            shots = assemble_clips([a for a in db.get("video_library", []) if Path(a["local_path"]).is_file()],
                                   float(p.get("audio_duration_sec") or 0), project_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        db["shots"] = [s for s in db["shots"] if s["project_id"] != project_id] + shots
        p.update({"render_url": None, "status": "video_ready", "updated_at": datetime.now().isoformat(timespec="seconds")})
        save_db(db)
    return {"shots": shots}


def render_project(project_id: str):
    db = load_db()
    p = get_project(db, project_id)
    shots = sorted([s for s in db["shots"] if s["project_id"] == project_id], key=lambda s: s["shot_index"])
    template = next(t for t in templates() if t["id"] == p["background_template_id"])
    target = project_dir(project_id) / "exports" / (uuid4().hex + ".mp4")
    target.parent.mkdir(exist_ok=True)

    def report(done: int, total: int):
        try:
            update_project(project_id, {"render_progress": {"done": done, "total": total}})
        except Exception:
            pass  # Progress is cosmetic; never fail the render because of it.

    # Prefer the TTS word timestamps saved next to the audio; uploaded audio falls back to
    # character-count weighting inside build_export_subtitles.
    subtitle_timings = []
    timeline = Path(p["audio_path"]).with_suffix(".json")
    if timeline.is_file():
        try:
            subtitle_timings = json.loads(timeline.read_text(encoding="utf-8")).get("subtitle_timings") or []
        except (OSError, ValueError):
            subtitle_timings = []
    narration_script, _, _ = strip_product_marker(p.get("raw_script", ""))
    narration = {
        "id": "narration", "shot_index": 1, "voice_text": narration_script,
        "start_time": 0, "end_time": p["audio_duration_sec"], "subtitle_timings": subtitle_timings,
    }
    subtitles = build_export_subtitles([narration], max_hanzi=9)

    render_video(shots, Path(p["audio_path"]), Path(template["local_path"]), target,
                 p["audio_duration_sec"], progress=report, subtitles=subtitles)
    update_project(project_id, {"status": "video_done", "render_url": public_url(target),
                                "render_progress": None})


def render_worker(project_id: str):
    try:
        render_project(project_id)
    except Exception as exc:
        update_project(project_id, {"status": "video_failed", "generation_error": str(exc)})
    finally:
        with LOCK:
            ACTIVE.discard(project_id)


@router.post("/projects/{project_id}/render-video")
def export_video(project_id: str):
    db = load_db()
    p = get_project(db, project_id)
    if not p.get("audio_path") or not any(s["project_id"] == project_id for s in db["shots"]):
        raise HTTPException(400, "请先生成视频")
    return start_job(project_id, render_worker, "video_rendering")
