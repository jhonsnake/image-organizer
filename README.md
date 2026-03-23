# NAS Photo Cleaner

Web app that automatically classifies and cleans junk from your photo library on a Synology NAS.
Runs a multi-stage pipeline (metadata, deduplication, quality, AI vision) and moves trash/documents
to a `_cleanup` folder — **nothing is ever deleted automatically**.

## Features

- **6-stage classification pipeline**: metadata analysis, perceptual hash deduplication, image quality analysis, AI vision classification, video classification, and file organization
- **Video support**: scans and classifies videos (WhatsApp clips, screen recordings, duplicates via SHA256), generates video thumbnails with ffmpeg
- **Multi-provider AI support**: local (LM Studio, Ollama, vLLM) and cloud (Anthropic, Gemini, OpenAI) with automatic fallback
- **Real-time progress**: WebSocket-based live updates during pipeline execution
- **AI reclassification from Review**: re-run AI vision on review photos with real-time progress bar, live stats, cancel support, and per-photo WebSocket updates — classify all or just selected photos
- **Space analysis**: per-job breakdown by category, media type, and reason with recovery recommendations and charts
- **AI Review Summary**: post-pipeline grouped summary of findings (dark photos, blurry, screenshots, etc.) with sample thumbnails — approve or reject entire categories with one click, files are moved immediately
- **Manual review queue**: review uncertain classifications with thumbnail previews, lightbox, keyboard shortcuts, and batch actions
- **WhatsApp detection**: identifies stickers, statuses, and forwarded media by filename and path patterns
- **Auto-organize by date**: all photos organized into `YYYY/MM/` folders using EXIF date, filename patterns, or file modification time — respects existing subfolder names
- **Documents folder**: invoices and documents are moved to `Photos/Documentos/` instead of `_cleanup`
- **Docker deployment**: single container with host networking, runs directly on your NAS
- **Synology Photos compatible**: moves files without touching the Synology database

## Requirements

- **Synology NAS** with Container Manager (Docker) installed
- **AI provider** — at least one of:
  - Local: [LM Studio](https://lmstudio.ai/), [Ollama](https://ollama.com), or any OpenAI-compatible server with a vision model (e.g. `qwen3-vl-8b-instruct`)
  - Cloud: Anthropic, Google Gemini, or OpenAI API key

## Quick Start

### 1. Clone the repo on your NAS

```bash
ssh your-user@your-nas-ip
cd /volume1/docker
git clone https://github.com/your-user/nas-image-cleaner.git
cd nas-image-cleaner
```

### 2. Configure docker-compose.yml

Edit `docker-compose.yml` to match your setup:

```yaml
services:
  photo-cleaner:
    build: .
    container_name: nas-photo-cleaner
    restart: unless-stopped
    network_mode: host
    volumes:
      # Persistent database and thumbnails
      - app-data:/app/data
      # Mount your NAS homes directory (read-write for moving files)
      - /volume1/homes:/data/homes
    environment:
      - CLEANER_HOMES_MOUNT=/data/homes
      - CLEANER_NAS_USERS=["user1","user2"]

volumes:
  app-data:
```

**Key settings:**
- `/volume1/homes` — adjust to your NAS shared folder path
- `CLEANER_NAS_USERS` — JSON array of NAS usernames whose photos you want to manage
- `network_mode: host` — required so the container can reach local AI providers on your LAN

### 3. Build and run

```bash
sudo docker compose build
sudo docker compose up -d
```

### 4. Open the web UI

Navigate to `http://your-nas-ip:8090` in your browser.

### 5. Configure an AI provider

Go to **Providers** in the nav bar and add at least one:

- **Local provider**: point to your LM Studio / Ollama server URL (e.g. `http://192.168.1.100:1234/v1`)
- **Cloud provider**: select type (Anthropic / Gemini / OpenAI), enter your API key and model

Providers are tried in priority order — the first one that responds is used.
You can configure multiple providers for automatic fallback.

### 6. Run a cleanup

Go to **Config**, select a user and source directory, then click **Start**.
Monitor progress in real-time on the **Progress** page.

## Architecture

```
Synology NAS (Docker)
├── FastAPI backend (Python)
│   ├── REST API + WebSocket
│   ├── SQLite database
│   └── Pipeline engine (6 stages)
└── React frontend (served as static files)
    ├── Dashboard — configure and start jobs
    ├── Progress — real-time pipeline monitoring
    ├── Review — manual review queue + AI reclassification with live progress
    ├── AI Summary — grouped classification results with batch approve/reject per category
    ├── History — past job results
    ├── Space Analysis — per-job storage breakdown and charts
    └── Providers — manage AI providers
```

## Pipeline Stages

```
File → Stage 1: Metadata ──────────────────────────────
       │ Filename patterns (WhatsApp, Screenshot)      │
       │ Screen dimensions + no camera EXIF            │ → ~30% classified
       │ Tiny images (stickers, icons)                 │
       │ WhatsApp stickers/statuses                    │
       ─────────────────────────────────────────────────
            ↓ (unclassified)
       Stage 2: Hash Deduplication ─────────────────────
       │ Perceptual hash for images (configurable)     │ → ~10-15% more
       │ SHA256 hash for videos (exact match)          │
       │ Groups bursts, keeps the best one             │
       ─────────────────────────────────────────────────
            ↓ (unclassified)
       Stage 3: Quality Analysis ───────────────────────
       │ Laplacian variance (blur detection)           │ → ~5-10% more
       │ Extreme brightness (dark / overexposed)       │
       ─────────────────────────────────────────────────
            ↓ (unclassified)
       Stage 4: AI Vision ──────────────────────────────
       │ Multi-provider with fallback                  │ → rest of images
       │ Categories: photo, screenshot, meme,          │
       │   document, invoice, accidental               │
       ─────────────────────────────────────────────────
            ↓
       Stage 5: Video Classification ───────────────────
       │ ffprobe metadata analysis (no LLM needed)     │ → videos
       │ WhatsApp videos, screen recordings            │
       │ Duration/resolution-based heuristics          │
       ─────────────────────────────────────────────────
            ↓
       Stage 6: Execute & Organize ─────────────────────
       │ Move TRASH → _cleanup/trash/subcategory/      │
       │ Move DOCUMENTS → _cleanup/docs/cat/YYYY/MM/   │
       │ Organize KEEP → YYYY/MM/ by date              │
       │ Respects existing subfolders                  │
       │ REVIEW items stay for manual review           │
       ─────────────────────────────────────────────────
```

## Configuration

All settings can be overridden via environment variables with the `CLEANER_` prefix:

| Variable | Default | Description |
|---|---|---|
| `CLEANER_HOMES_MOUNT` | `/data/homes` | Path where NAS homes are mounted |
| `CLEANER_NAS_USERS` | `[]` | JSON array of NAS usernames |
| `CLEANER_DEFAULT_LLM_URL` | `http://100.127.43.94:1234/v1` | Default LM Studio endpoint |
| `CLEANER_DEFAULT_MODEL` | `qwen3-vl-8b-instruct` | Default vision model |
| `CLEANER_DATABASE_URL` | `sqlite+aiosqlite:///./data/photo_cleaner.db` | Database path |
| `CLEANER_PORT` | `8090` | Server port |

Advanced thresholds (configurable in the web UI):

| Setting | Default | Description |
|---|---|---|
| Blur threshold | 50 | Lower = more sensitive to blur |
| Hash threshold | 8 | Hamming distance for duplicates (lower = stricter) |
| Darkness threshold | 15 | Below this = too dark |
| Brightness threshold | 245 | Above this = overexposed |
| Confidence threshold | 0.7 | Below this = sent to review |

## Development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
python main.py
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend dev server proxies API requests to `http://localhost:8090`.

## Synology Photos Compatibility

This tool does **not** modify the Synology Photos database.
Moved files will disappear from Photos after the next automatic re-index
(or you can force it from the Synology control panel).

To prevent Synology from indexing the `_cleanup` folder:
1. Go to **Synology Photos → Settings → Indexing**
2. Exclude the `_cleanup` folder

## Troubleshooting

**Container won't start:**
```bash
sudo docker logs nas-photo-cleaner
```

**AI provider not available:**
- Go to Providers → click the test button next to your provider
- For local providers: ensure the LLM server is running and reachable from the NAS
- For cloud providers: verify your API key is correct

**Permission errors moving files:**
- The container needs read-write access to the mounted homes directory
- Check that the volume mount in `docker-compose.yml` points to the correct path

**Slow classification:**
- Use a local GPU server for the vision model (much faster than cloud for large batches)
- The first 3 stages (metadata, dedup, quality) are fast and don't need AI
- Adjust the confidence threshold to reduce the number of items needing AI review

## License

MIT
