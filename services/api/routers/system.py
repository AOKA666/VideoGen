from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query


router = APIRouter(prefix="/api/system", tags=["system"])

API_DIR = Path(__file__).resolve().parents[1]
LOG_FILES = {
    "runtime": API_DIR / "logs" / "uvicorn.stdout.log",
    "errors": API_DIR / "logs" / "uvicorn.stderr.log",
}


def tail_text(path: Path, max_chars: int) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as file:
        file.seek(0, 2)
        size = file.tell()
        file.seek(max(0, size - max_chars * 2))
        data = file.read()
    return data.decode("utf-8", errors="replace")[-max_chars:]


@router.get("/logs")
def read_logs(
    stream: str = Query("runtime", pattern="^(runtime|errors)$"),
    max_chars: int = Query(12000, ge=1000, le=60000),
):
    path = LOG_FILES[stream]
    return {
        "stream": stream,
        "path": str(path),
        "exists": path.exists(),
        "content": tail_text(path, max_chars),
    }
