from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROJECT_DIR = ROOT / "storage"
_WINDOWS_CONFIG_ROOT = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
CONFIG_DIR = Path(os.getenv("VIDEOGEN_CONFIG_DIR") or (
    _WINDOWS_CONFIG_ROOT / "VideoGen" if os.name == "nt" else Path.home() / ".videogen"
))
CONFIG_FILE = CONFIG_DIR / "settings.json"
_LOCK = threading.RLock()


def _normalized_directory(value: object, fallback: Path | None = None) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return fallback.resolve() if fallback else None
    return Path(text).expanduser().resolve()


def load_settings() -> dict[str, str]:
    with _LOCK:
        data: dict[str, Any] = {}
        if CONFIG_FILE.is_file():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        project_directory = _normalized_directory(data.get("project_directory"), DEFAULT_PROJECT_DIR)
        jianying_directory = _normalized_directory(data.get("jianying_drafts_directory"))
        return {
            "project_directory": str(project_directory),
            "jianying_drafts_directory": str(jianying_directory) if jianying_directory else "",
        }


def save_settings(**updates: str) -> dict[str, str]:
    with _LOCK:
        settings = load_settings()
        settings.update({key: str(value or "").strip() for key, value in updates.items()})
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        temp_path = CONFIG_FILE.with_suffix(".tmp")
        temp_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, CONFIG_FILE)
        return settings


def project_storage_directory() -> Path:
    return Path(load_settings()["project_directory"]).resolve()


def configured_jianying_directory() -> Path | None:
    value = load_settings()["jianying_drafts_directory"]
    return Path(value).resolve() if value else None


def choose_directory(title: str, initial_directory: str = "") -> str:
    if os.name == "nt":
        env = dict(os.environ)
        env["VIDEOGEN_PICKER_TITLE"] = title
        env["VIDEOGEN_PICKER_INITIAL"] = initial_directory
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$dialog.Description = $env:VIDEOGEN_PICKER_TITLE; "
            "$dialog.ShowNewFolderButton = $true; "
            "if ($env:VIDEOGEN_PICKER_INITIAL -and (Test-Path -LiteralPath $env:VIDEOGEN_PICKER_INITIAL)) "
            "{ $dialog.SelectedPath = $env:VIDEOGEN_PICKER_INITIAL }; "
            "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
            "{ [Console]::OutputEncoding = [Text.Encoding]::UTF8; Write-Output $dialog.SelectedPath }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=0x08000000,
            check=False,
        )
        return result.stdout.strip()

    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title=title, initialdir=initial_directory or None)
        root.destroy()
        return selected
    except (ImportError, tkinter.TclError):
        return ""
