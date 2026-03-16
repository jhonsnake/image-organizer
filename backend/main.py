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
from models import init_db

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
    logger.info("Database initialized")
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

app.include_router(config_router)
app.include_router(jobs_router)
app.include_router(review_router)
app.include_router(providers_router)
app.include_router(watcher_router)


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
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
