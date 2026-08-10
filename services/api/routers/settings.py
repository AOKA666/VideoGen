from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.settings_service import CONFIG_FILE, choose_directory, load_settings, save_settings
from services.store import configure_storage, ensure_storage


router = APIRouter(prefix="/api/settings", tags=["settings"])


class DirectorySelectionPayload(BaseModel):
    kind: str


def _settings_response() -> dict:
    settings = load_settings()
    return {
        "settings": settings,
        "config_file": str(CONFIG_FILE),
    }


@router.get("")
def get_settings():
    return _settings_response()


@router.post("/select-directory")
def select_directory(payload: DirectorySelectionPayload):
    if payload.kind not in {"project", "jianying"}:
        raise HTTPException(400, "Directory kind must be project or jianying")
    settings = load_settings()
    key = "project_directory" if payload.kind == "project" else "jianying_drafts_directory"
    title = "选择 VideoGen 项目目录" if payload.kind == "project" else "选择剪映草稿目录"
    selected = choose_directory(title, settings.get(key, ""))
    if not selected:
        return {**_settings_response(), "cancelled": True}
    directory = Path(selected).resolve()
    if not directory.is_dir():
        raise HTTPException(400, "Selected directory does not exist")
    if payload.kind == "project":
        previous_directory = Path(settings["project_directory"])
        try:
            configure_storage(directory)
            ensure_storage()
        except Exception as exc:
            configure_storage(previous_directory)
            ensure_storage()
            raise HTTPException(400, f"The selected project directory is not usable: {exc}") from exc
    save_settings(**{key: str(directory)})
    return {**_settings_response(), "cancelled": False, "changed": payload.kind}
