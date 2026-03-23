"""
NAS Photo Cleaner — FastAPI Backend
WebSocket for real-time progress, REST API for config/jobs/review.
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import settings
from models import init_db, async_session, VisionProviderConfig, AppConfig, Job, JobStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── WebSocket connection manager ──

class ConnectionManager:
    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)
        logger.info(f"WebSocket connected ({len(self.connections)} total)")

    def disconnect(self, ws: WebSocket):
        self.connections.remove(ws)
        logger.info(f"WebSocket disconnected ({len(self.connections)} total)")

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.connections.remove(ws)


ws_manager = ConnectionManager()

# Event loop reference for thread-safe broadcasting
_loop: asyncio.AbstractEventLoop | None = None


def broadcast_progress(job_id: int, event: str, data: dict):
    """Thread-safe callback for pipeline progress — called from asyncio.to_thread."""
    msg = {"job_id": job_id, "event": event, **data}
    if _loop and not _loop.is_closed():
        asyncio.run_coroutine_threadsafe(ws_manager.broadcast(msg), _loop)


# ── Auto-migration ──

async def _auto_migrate_providers():
    """If there are AppConfig rows with llm_url but no providers, auto-create a provider."""
    from sqlalchemy import select, func
    async with async_session() as db:
        count_result = await db.execute(select(func.count(VisionProviderConfig.id)))
        provider_count = count_result.scalar() or 0
        if provider_count > 0:
            return

        result = await db.execute(select(AppConfig))
        configs = list(result.scalars().all())
        seen_urls = set()
        for cfg in configs:
            if cfg.llm_url and cfg.llm_url not in seen_urls:
                seen_urls.add(cfg.llm_url)
                provider = VisionProviderConfig(
                    name=f"Local LLM ({cfg.llm_model or 'auto'})",
                    provider_type="openai-compatible",
                    base_url=cfg.llm_url,
                    model=cfg.llm_model or "",
                    priority=10,
                    enabled=True,
                )
                db.add(provider)
                logger.info(f"Auto-migrated provider from AppConfig: {cfg.llm_url}")
        await db.commit()


async def _migrate_db():
    """Add missing columns to existing tables (poor-man's migration)."""
    from sqlalchemy import text
    async with async_session() as db:
        # Check which columns exist in jobs table
        result = await db.execute(text("PRAGMA table_info(jobs)"))
        existing = {row[1] for row in result.fetchall()}

        migrations = []
        if "stage_progress" not in existing:
            migrations.append("ALTER TABLE jobs ADD COLUMN stage_progress INTEGER DEFAULT 0")
        if "stage_total" not in existing:
            migrations.append("ALTER TABLE jobs ADD COLUMN stage_total INTEGER DEFAULT 0")

        for sql in migrations:
            await db.execute(text(sql))
            logger.info(f"Migration: {sql}")
        if migrations:
            await db.commit()

        # Check photos table for new columns
        result = await db.execute(text("PRAGMA table_info(photos)"))
        photo_cols = {row[1] for row in result.fetchall()}

        photo_migrations = []
        if "media_type" not in photo_cols:
            photo_migrations.append("ALTER TABLE photos ADD COLUMN media_type VARCHAR(16) DEFAULT 'image'")
        if "duration" not in photo_cols:
            photo_migrations.append("ALTER TABLE photos ADD COLUMN duration FLOAT")
        if "video_codec" not in photo_cols:
            photo_migrations.append("ALTER TABLE photos ADD COLUMN video_codec VARCHAR(32)")

        for sql in photo_migrations:
            await db.execute(text(sql))
            logger.info(f"Migration: {sql}")
        if photo_migrations:
            await db.commit()


async def _recover_crashed_jobs():
    """Mark any RUNNING jobs as PAUSED on startup — they crashed with the server."""
    from sqlalchemy import select
    async with async_session() as db:
        result = await db.execute(
            select(Job).where(Job.status == JobStatus.RUNNING)
        )
        crashed = list(result.scalars().all())
        for job in crashed:
            job.status = JobStatus.PAUSED
            job.error_message = "Interrumpido por reinicio del servidor"
            logger.warning(f"Job {job.id} was RUNNING at startup — marked as PAUSED")
        if crashed:
            await db.commit()
            logger.info(f"Recovered {len(crashed)} crashed job(s)")


# ── App lifecycle ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()

    # Create data directories
    os.makedirs("./data", exist_ok=True)
    os.makedirs(settings.thumbnail_dir, exist_ok=True)

    # Initialize database
    await init_db()
    await _migrate_db()
    logger.info("Database initialized")

    # Auto-migrate: if AppConfig has llm_url but no providers exist, create one
    await _auto_migrate_providers()

    # Auto-pause jobs that were RUNNING when the server crashed/restarted
    await _recover_crashed_jobs()

    logger.info(f"Server running on http://{settings.host}:{settings.port}")

    yield

    _loop = None


# ── FastAPI app ──

app = FastAPI(
    title="NAS Photo Cleaner",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # LAN only, no auth needed
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
from api.config import router as config_router
from api.jobs import router as jobs_router
from api.review import router as review_router
from api.providers import router as providers_router
from api.watcher import router as watcher_router
from api.analysis import router as analysis_router

app.include_router(config_router)
app.include_router(jobs_router)
app.include_router(review_router)
app.include_router(providers_router)
app.include_router(watcher_router)
app.include_router(analysis_router)


# ── WebSocket endpoint ──

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            # Keep connection alive, handle client messages if needed
            data = await ws.receive_text()
            # Could handle client commands here (e.g., pause/resume)
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


# ── Health check ──

@app.get("/api/health")
async def health():
    from services.watcher import get_watcher
    watcher = get_watcher()
    return {
        "status": "ok",
        "version": "2.0.0",
        "watcher": watcher.stats if watcher else None,
    }


# ── Serve React frontend (production) ──
# In production, the built React app is served from /app/static
# StaticFiles serves assets, and the catch-all below handles SPA routing
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    from fastapi.responses import FileResponse

    # Serve static assets (js, css, images, etc.)
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="static-assets")

    # SPA catch-all: serve index.html for any non-API route
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # If the path matches an actual file in static dir, serve it
        file_path = os.path.join(static_dir, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        # Otherwise serve index.html for client-side routing
        return FileResponse(os.path.join(static_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
