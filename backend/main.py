"""
main.py — FastAPI application entry point.
"""
import logging
import sys
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from config import get_settings
from storage.database import init_db
from scheduler.jobs import create_scheduler
from api.routes import router
from api.auth import router as auth_router

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)
logging.basicConfig(stream=sys.stdout, level=logging.INFO)

logger = structlog.get_logger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    logger.info("app_startup", env=settings.app_env)

    # Initialize database tables
    await init_db()
    logger.info("db_initialized")

    # Start scheduler
    _scheduler = create_scheduler()
    _scheduler.start()
    logger.info("scheduler_started")

    yield

    # Shutdown
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    logger.info("app_shutdown")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Jira Intelligence Platform",
    version="1.0.0",
    description="Enterprise Jira analytics, governance, risk scoring, and executive reporting.",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# Parse allowed origins from config
_allowed = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
if settings.app_env == "production" and "*" in _allowed:
    logger.warning("cors_allow_all_in_production",
                   message="CORS allow_origins=['*'] in production is a security risk.")
    # In production, require explicit origins — default to empty
    origins = [f"https://{s}" for s in _allowed if s != "*"] or []
else:
    origins = _allowed

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=bool(origins and origins != ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
)

logger.info("cors_configured", origins=origins, env=settings.app_env)

app.include_router(auth_router)
app.include_router(router)

# Serve frontend static files if they exist
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")

    @app.get("/", response_class=FileResponse)
    async def serve_frontend():
        return FileResponse(os.path.join(frontend_path, "index.html"))

    # Serve AI chat React app at /ai/
    ai_chat_dist = os.path.join(frontend_path, "ai-chat", "dist")
    if os.path.isdir(ai_chat_dist):
        app.mount("/ai/assets", StaticFiles(directory=os.path.join(ai_chat_dist, "assets")), name="ai_assets")

        @app.get("/ai/{full_path:path}", response_class=FileResponse)
        async def serve_ai_chat(full_path: str):
            file_path = os.path.join(ai_chat_dist, full_path)
            if os.path.isfile(file_path):
                return FileResponse(file_path)
            return FileResponse(os.path.join(ai_chat_dist, "index.html"))

        @app.get("/ai", response_class=FileResponse)
        async def serve_ai_chat_root():
            return FileResponse(os.path.join(ai_chat_dist, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=(settings.app_env == "development"),
        log_level=settings.log_level.lower(),
    )
