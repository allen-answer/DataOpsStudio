from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.utils.logging_config import setup_logging
from app.utils.paths import BASE_DIR, ensure_dirs


ensure_dirs()
setup_logging()

app = FastAPI(title="Lightweight Data Compare Tool", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(router)
