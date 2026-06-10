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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

# Serve frontend static files if they exist
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")

    @app.get("/", response_class=FileResponse)
    async def serve_frontend():
        return FileResponse(os.path.join(frontend_path, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=(settings.app_env == "development"),
        log_level=settings.log_level.lower(),
    )
