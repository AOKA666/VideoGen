from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from services.settings_service import project_storage_directory

STORAGE = project_storage_directory()
DB = STORAGE / "db.json"
ASSETS_DIR = STORAGE / "assets"
PROJECTS_DIR = STORAGE / "projects"
DEFAULT_DB: dict[str, Any] = {
    "projects": [],
    "shots": [],
    "music_library": [],
    "video_library_presets": [],
    "product_library": [],
    "product_placement_presets": [],
}
_DB_LOCK = threading.RLock()
_DB_CACHE: dict[str, Any] | None = None
_DB_CACHE_MTIME_NS: int | None = None
_STORAGE_INITIALIZED = False


@contextmanager
def db_write_transaction():
    """Serialize a complete load/modify/save database transaction."""
    with _DB_LOCK:
        yield


def configure_storage(path: str | Path) -> None:
    global STORAGE, DB, ASSETS_DIR, PROJECTS_DIR
    global _DB_CACHE, _DB_CACHE_MTIME_NS, _STORAGE_INITIALIZED
    root = Path(path).expanduser().resolve()
    STORAGE = root
    DB = root / "db.json"
    ASSETS_DIR = root / "assets"
    PROJECTS_DIR = root / "projects"
    _DB_CACHE = None
    _DB_CACHE_MTIME_NS = None
    _STORAGE_INITIALIZED = False


def storage_dir() -> Path:
    return STORAGE


def projects_dir() -> Path:
    return PROJECTS_DIR


def assets_dir() -> Path:
    return ASSETS_DIR


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
        for library_key in ("product_library", "music_library", "video_library", "video_templates"):
            for item in data.get(library_key, []):
                local_path = str(item.get("local_path") or "")
                if not local_path:
                    continue
                current_path = _current_storage_path(local_path)
                if current_path.exists() and str(current_path) != local_path:
                    item["local_path"] = str(current_path)
                    item["file_url"] = public_url(current_path)
                    changed = True
        for preset in data.get("video_library_presets", []):
            cover_path = str(preset.get("cover_source_path") or "")
            if not cover_path:
                continue
            current_cover = _current_storage_path(cover_path)
            if not current_cover.exists():
                preset["cover_source_path"] = ""
                preset["cover_source_url"] = ""
                changed = True
            elif str(current_cover) != cover_path:
                preset["cover_source_path"] = str(current_cover)
                preset["cover_source_url"] = public_url(current_cover)
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
