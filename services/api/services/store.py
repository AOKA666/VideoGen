from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
STORAGE = ROOT / "storage"
DB = STORAGE / "db.json"
ASSETS_DIR = STORAGE / "assets"
PROJECTS_DIR = STORAGE / "projects"


def ensure_storage() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    if not DB.exists():
        save_db({"projects": [], "shots": [], "assets": [], "project_assets": [], "generated_assets": [], "asset_library": None, "web_image_failures": []})
        return
    data = json.loads(DB.read_text(encoding="utf-8"))
    changed = False
    for key, default in {
        "projects": [],
        "shots": [],
        "assets": [],
        "project_assets": [],
        "generated_assets": [],
        "asset_library": None,
        "web_image_failures": [],
    }.items():
        if key not in data:
            data[key] = default
            changed = True
    if changed:
        save_db(data)


def load_db() -> dict[str, Any]:
    ensure_storage()
    return json.loads(DB.read_text(encoding="utf-8"))


def save_db(data: dict[str, Any]) -> None:
    STORAGE.mkdir(parents=True, exist_ok=True)
    DB.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def public_url(path: Path) -> str:
    rel = path.resolve().relative_to(STORAGE.resolve()).as_posix()
    return f"/storage/{rel}"


def project_dir(project_id: str) -> Path:
    path = PROJECTS_DIR / project_id
    for name in ["images", "videos", "audio", "subtitles", "exports"]:
        (path / name).mkdir(parents=True, exist_ok=True)
    return path
