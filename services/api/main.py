import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routers import export, generation, music, projects, settings, media, video_library
from services.env import load_env_local
from services import store

load_env_local()
store.ensure_storage()

# Bump when routes change so start-local.ps1 does not reuse an outdated backend process.
API_VERSION = 21


def silence_windows_connection_resets() -> None:
    """Windows' Proactor loop logs WinError 10054 when a browser aborts a media request.

    Browsers routinely cancel range requests for videos (seeking, closing a preview),
    which is expected and harmless, so keep the default handler from logging it.
    """
    if sys.platform != "win32":
        return
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()

    def handle(loop, context):
        if isinstance(context.get("exception"), ConnectionResetError):
            return
        if previous is None:
            loop.default_exception_handler(context)
        else:
            previous(loop, context)

    loop.set_exception_handler(handle)


@asynccontextmanager
async def lifespan(app: FastAPI):
    silence_windows_connection_resets()
    yield


app = FastAPI(title="AI Video Draft API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(generation.router)
app.include_router(music.router)
app.include_router(export.router)
app.include_router(settings.router)
app.include_router(media.router)
app.include_router(video_library.router)


@app.get("/storage/{relative_path:path}", include_in_schema=False)
def read_storage_file(relative_path: str):
    storage_root = store.storage_dir().resolve()
    target = (storage_root / relative_path).resolve()
    if target == store.DB.resolve() or not target.is_relative_to(storage_root) or not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(target)


@app.get("/api/health")
def health():
    return {"status": "ok", "api_version": API_VERSION}


WEB_DIST = Path(__file__).resolve().parents[2] / "apps" / "web" / "dist"
if (WEB_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIST / "assets")), name="web-assets")


@app.get("/{relative_path:path}", include_in_schema=False)
def local_web_app(relative_path: str):
    if relative_path.startswith(("api/", "storage/")):
        raise HTTPException(404, "Not found")
    target = (WEB_DIST / relative_path).resolve()
    if WEB_DIST.is_dir() and target.is_relative_to(WEB_DIST.resolve()) and target.is_file():
        return FileResponse(target)
    index = WEB_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(404, "Frontend build not found. Run npm run build in apps/web.")
