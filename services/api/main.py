import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from routers import assets, export, generation, matching, projects, shots, system
from services.env import load_env_local
from services.store import ensure_storage, load_db, save_db
from services.web_image_pipeline import recover_interrupted_searches

load_env_local()
ensure_storage()
startup_db = load_db()
if recover_interrupted_searches(startup_db, force=True):
    save_db(startup_db)


def configure_file_logging() -> None:
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "image-search.log"
    logger = logging.getLogger("uvicorn.error")
    resolved_path = str(log_path.resolve())
    if any(getattr(handler, "baseFilename", "") == resolved_path for handler in logger.handlers):
        return
    handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


configure_file_logging()

app = FastAPI(title="Real Material Video Draft API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/storage", StaticFiles(directory="../../storage"), name="storage")

app.include_router(projects.router)
app.include_router(assets.router)
app.include_router(shots.router)
app.include_router(matching.router)
app.include_router(generation.router)
app.include_router(export.router)
app.include_router(system.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
