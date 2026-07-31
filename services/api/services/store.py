from __future__ import annotations

import json
import os
import re
import threading
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid5, NAMESPACE_URL

ROOT = Path(__file__).resolve().parents[3]
STORAGE = ROOT / "storage"
DB = STORAGE / "db.json"
ASSETS_DIR = STORAGE / "assets"
PROJECTS_DIR = STORAGE / "projects"
DEFAULT_DB: dict[str, Any] = {
    "projects": [],
    "shots": [],
    "assets": [],
    "project_assets": [],
    "generated_assets": [],
    "music_library": [],
    "promotion_books": ["女性人物传记", "历史深处的民国", "国之脊梁"],
    "promotion_books_catalog_initialized": True,
    "ai_script_people_history": {},
    "asset_library": None,
    "web_image_failures": [],
    "web_image_diagnostics": [],
}
_DB_LOCK = threading.RLock()
_DB_CACHE: dict[str, Any] | None = None
_DB_CACHE_MTIME_NS: int | None = None
_STORAGE_INITIALIZED = False
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_SHOT_DIR_PATTERN = re.compile(r"^shot_(\d+)$")
_SHOT_FILE_PATTERN = re.compile(r"^shot_(\d+)\.(?:png|jpe?g|webp)$", re.IGNORECASE)


def _current_storage_path(value: str | Path) -> Path:
    path = Path(value)
    if path.exists():
        return path.resolve()
    parts = list(path.parts)
    storage_indexes = [index for index, part in enumerate(parts) if part.lower() == "storage"]
    if not storage_indexes:
        return path
    relocated = STORAGE.joinpath(*parts[storage_indexes[-1] + 1:])
    return relocated.resolve() if relocated.exists() else path


def _asset_source(path: Path) -> str:
    if path.parent.name == "images" and _SHOT_FILE_PATTERN.match(path.name):
        return "ai_generated"
    if path.name.lower().startswith("manual_"):
        return "manual_upload"
    return "web_search"


def _recover_orphaned_generated_assets(data: dict[str, Any]) -> bool:
    known_projects = {item.get("id") for item in data.get("projects", [])}
    known_paths = {
        str(_current_storage_path(item["local_path"])).lower()
        for item in data.get("generated_assets", [])
        if item.get("local_path")
    }
    shots_by_project_index = {
        (shot.get("project_id"), int(shot.get("shot_index", 0))): shot
        for shot in data.get("shots", [])
        if shot.get("project_id") and shot.get("shot_index")
    }
    recovered_by_shot: dict[str, list[dict[str, Any]]] = {}
    now = datetime.now().isoformat(timespec="seconds")

    for project_id in known_projects:
        images_dir = PROJECTS_DIR / str(project_id) / "images"
        if not images_dir.exists():
            continue
        for path in sorted(images_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            resolved = path.resolve()
            if str(resolved).lower() in known_paths:
                continue
            shot_index = None
            folder_match = _SHOT_DIR_PATTERN.match(path.parent.name)
            file_match = _SHOT_FILE_PATTERN.match(path.name)
            if folder_match:
                shot_index = int(folder_match.group(1))
            elif path.parent == images_dir and file_match:
                shot_index = int(file_match.group(1))
            if shot_index is None:
                continue
            shot = shots_by_project_index.get((project_id, shot_index))
            if not shot:
                continue
            source = _asset_source(path)
            asset_id = str(uuid5(NAMESPACE_URL, resolved.as_posix().lower()))
            asset = {
                "id": asset_id,
                "project_id": project_id,
                "shot_id": shot["id"],
                "type": "image",
                "file_type": "image",
                "file_name": path.name,
                "asset_source": source,
                "provider": "recovered_local_file",
                "file_size": path.stat().st_size,
                "file_url": public_url(resolved),
                "local_path": str(resolved),
                "status": "success",
                "created_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                "recovered_at": now,
            }
            data.setdefault("generated_assets", []).append(asset)
            data.setdefault("project_assets", []).append({
                "project_id": project_id,
                "shot_id": shot["id"],
                "asset_id": asset_id,
                "asset_source": source,
                "match_score": 100 if source != "web_search" else 80,
                "match_reason": "Recovered from the project image directory",
                "created_at": now,
            })
            known_paths.add(str(resolved).lower())
            recovered_by_shot.setdefault(shot["id"], []).append(asset)

    if not recovered_by_shot:
        return False

    valid_asset_ids = {item.get("id") for item in data.get("generated_assets", [])}
    for shot in data.get("shots", []):
        recovered = recovered_by_shot.get(shot.get("id"))
        if not recovered:
            continue
        if shot.get("selected_asset_id") not in valid_asset_ids:
            preferred = next(
                (item for item in recovered if item.get("asset_source") in {"ai_generated", "manual_upload"}),
                recovered[0],
            )
            shot["selected_asset_id"] = preferred["id"]
            shot["asset_source"] = preferred["asset_source"]
        shot["downloaded_image_count"] = len(recovered)
        shot["status"] = {
            "ai_generated": "ai_generated",
            "manual_upload": "uploaded",
        }.get(shot.get("asset_source"), "web_downloaded")
        shot["updated_at"] = now
    return True


def ensure_storage() -> None:
    global _STORAGE_INITIALIZED
    with _DB_LOCK:
        if _STORAGE_INITIALIZED:
            return
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        if not DB.exists():
            save_db(dict(DEFAULT_DB))
            _STORAGE_INITIALIZED = True
            return
        data = _read_db_file()
        changed = False
        for key, default in DEFAULT_DB.items():
            if key not in data:
                data[key] = default
                changed = True
        for asset in data.get("generated_assets", []):
            local_path = asset.get("local_path")
            if not local_path:
                continue
            current_path = _current_storage_path(local_path)
            if not current_path.exists() or str(current_path) == str(local_path):
                continue
            asset["local_path"] = str(current_path)
            asset["file_url"] = public_url(current_path)
            changed = True
        stale_asset_ids = {
            item.get("id")
            for item in data.get("generated_assets", [])
            if item.get("local_path") and not _current_storage_path(str(item["local_path"])).exists()
        }
        if stale_asset_ids:
            data["generated_assets"] = [
                item for item in data.get("generated_assets", [])
                if item.get("id") not in stale_asset_ids
            ]
            data["project_assets"] = [
                item for item in data.get("project_assets", [])
                if item.get("asset_id") not in stale_asset_ids
            ]
            remaining_by_shot = {
                item.get("shot_id")
                for item in data.get("generated_assets", [])
                if item.get("shot_id")
            }
            for shot in data.get("shots", []):
                if shot.get("status") not in {"web_downloaded", "uploaded"} or shot.get("id") in remaining_by_shot:
                    continue
                shot["status"] = "no_image"
                shot["selected_asset_id"] = None
                shot["asset_source"] = None
                shot["downloaded_image_count"] = 0
                shot["match_score"] = 0
                shot["image_score"] = None
                shot["search_attempts"] = 0
            changed = True
        if _recover_orphaned_generated_assets(data):
            changed = True
        if changed:
            save_db(data)
        _STORAGE_INITIALIZED = True


def load_db(copy_data: bool = True) -> dict[str, Any]:
    with _DB_LOCK:
        ensure_storage()
        return _read_db_file(copy_data=copy_data)


def save_db(data: dict[str, Any]) -> None:
    global _DB_CACHE, _DB_CACHE_MTIME_NS
    with _DB_LOCK:
        STORAGE.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        tmp = DB.with_name(f"{DB.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, DB)
        _DB_CACHE = deepcopy(data)
        _DB_CACHE_MTIME_NS = DB.stat().st_mtime_ns


def _read_db_file(copy_data: bool = True) -> dict[str, Any]:
    global _DB_CACHE, _DB_CACHE_MTIME_NS
    if _DB_CACHE is not None and DB.exists():
        mtime_ns = DB.stat().st_mtime_ns
        if _DB_CACHE_MTIME_NS == mtime_ns:
            return deepcopy(_DB_CACHE) if copy_data else _DB_CACHE
    for attempt in range(3):
        try:
            text = DB.read_text(encoding="utf-8")
            if not text.strip():
                raise json.JSONDecodeError("Empty database file", text, 0)
            data = json.loads(text)
            _DB_CACHE = deepcopy(data)
            _DB_CACHE_MTIME_NS = DB.stat().st_mtime_ns
            return deepcopy(_DB_CACHE) if copy_data else _DB_CACHE
        except json.JSONDecodeError:
            if attempt == 2:
                raise
            time.sleep(0.05)
    return dict(DEFAULT_DB)


def public_url(path: Path) -> str:
    rel = path.resolve().relative_to(STORAGE.resolve()).as_posix()
    return f"/storage/{rel}"


def project_dir(project_id: str) -> Path:
    path = PROJECTS_DIR / project_id
    for name in ["images", "videos", "audio", "subtitles", "exports"]:
        (path / name).mkdir(parents=True, exist_ok=True)
    return path
