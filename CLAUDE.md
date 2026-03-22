# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NAS Photo Cleaner — a web app that classifies and organizes photos/videos on a Synology NAS using a multi-stage pipeline (metadata, dedup, quality, AI vision, video classification, file organization). Nothing is deleted; files are moved to `_cleanup/` folders or organized into `YYYY/MM/` date folders.

## Development Commands

### Backend (FastAPI + Python 3.12)
```bash
cd backend
pip install -r requirements.txt
python main.py                  # runs uvicorn with reload on :8090
```

### Frontend (React 19 + Vite + TypeScript + Tailwind v4)
```bash
cd frontend
npm install --legacy-peer-deps  # needed due to peer dep conflicts
npm run dev                     # dev server, proxies API to localhost:8090
npm run build                   # tsc -b && vite build
npm run lint                    # eslint
```

### Docker (production)
```bash
docker compose build            # multi-stage: builds frontend, then Python image
docker compose up -d            # serves on :8090
```

## Architecture

**Two-part app**: FastAPI backend + React SPA (served as static files in production).

### Backend (`backend/`)
- **`main.py`** — FastAPI app setup, WebSocket connection manager for real-time progress, DB migrations, crash recovery for interrupted jobs
- **`config.py`** — Pydantic Settings with `CLEANER_` env prefix
- **`models.py`** — SQLAlchemy async models (SQLite + aiosqlite, WAL mode). Key models: `Job`, `Photo`, `AppConfig`, `VisionProviderConfig`, `WatcherEvent`
- **`services/pipeline.py`** — `PipelineRunner` class orchestrates the 6-stage pipeline with pause/resume/cancel support; reports progress via callback for WebSocket broadcast
- **`services/scanner.py`** — file scanning, metadata extraction, perceptual hashing, quality analysis, date extraction
- **`services/vision.py`** — multi-provider AI vision (OpenAI-compatible, Anthropic, Gemini) with fallback chain
- **`services/video_classifier.py`** — video classification using ffprobe metadata
- **`services/thumbnails.py`** — image/video thumbnail generation (ffmpeg for videos)
- **`services/watcher.py`** — filesystem watcher for auto-detecting new files
- **`api/`** — REST route modules: `config`, `jobs`, `review`, `providers`, `watcher`, `analysis`

### Frontend (`frontend/src/`)
- **Pages**: Dashboard, Progress (WebSocket live updates), Review (manual classification queue), History, SpaceAnalysis (recharts), Providers, Watcher
- **`hooks/useWebSocket.ts`** — shared WebSocket hook for real-time pipeline events
- **`lib/api.ts`** — API client functions

### Standalone Scripts (root)
- **`photo_cleaner.py`** — original standalone CLI version of the pipeline (not used by the web app)
- **`cleanup.py`** — standalone CLI cleanup script (not used by the web app)

## Key Patterns

- All environment variables use the `CLEANER_` prefix (e.g., `CLEANER_HOMES_MOUNT`, `CLEANER_NAS_USERS`)
- Database migrations are "poor-man's" style in `main.py` using `PRAGMA table_info` + `ALTER TABLE`
- Pipeline progress uses thread-safe broadcasting: `asyncio.run_coroutine_threadsafe` from worker threads to the event loop
- AI providers are tried in priority order (lower number = higher priority) with automatic fallback
- File moves go to `_cleanup/trash/`, `_cleanup/docs/`, or `YYYY/MM/` date organization — never deletes
- Some user-facing strings are in Spanish (e.g., error messages)
