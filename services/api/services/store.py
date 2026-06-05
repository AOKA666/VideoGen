from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

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
    "asset_library": None,
    "web_image_failures": [],
}
_DB_LOCK = threading.RLock()


def ensure_storage() -> None:
    with _DB_LOCK:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        if not DB.exists():
            save_db(dict(DEFAULT_DB))
            return
        data = _read_db_file()
        changed = False
        for key, default in DEFAULT_DB.items():
            if key not in data:
                data[key] = default
                changed = True
        if changed:
            save_db(data)


def load_db() -> dict[str, Any]:
    with _DB_LOCK:
        ensure_storage()
        return _read_db_file()


def save_db(data: dict[str, Any]) -> None:
    with _DB_LOCK:
        STORAGE.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        tmp = DB.with_name(f"{DB.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, DB)


def _read_db_file() -> dict[str, Any]:
    for attempt in range(3):
        try:
            text = DB.read_text(encoding="utf-8")
            if not text.strip():
                raise json.JSONDecodeError("Empty database file", text, 0)
            return json.loads(text)
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
