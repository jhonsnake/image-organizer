# ── Stage 1: Build frontend ──
FROM node:22-alpine AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY frontend/ ./
RUN npx tsc -b && npx vite build

# ── Stage 2: Production ──
FROM python:3.12-slim

# System deps for opencv-headless, Pillow, pillow-heif
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libheif-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ .

# Copy built frontend into backend static dir
COPY --from=frontend-build /app/frontend/dist ./static

# Create data directory
RUN mkdir -p /app/data /app/data/thumbnails

EXPOSE 8090

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]
