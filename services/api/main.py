from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from routers import assets, export, generation, matching, projects, shots, system
from services.env import load_env_local
from services.store import STORAGE, ensure_storage

load_env_local()
ensure_storage()

app = FastAPI(title="Real Material Video Draft API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/storage", StaticFiles(directory=str(STORAGE)), name="storage")

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
