#!/usr/bin/env python3
"""
NAS Photo Cleaner — Hybrid Pipeline
=====================================
Optimizes Synology Photos storage by identifying and organizing "junk" images.

Pipeline:
  1. Metadata scan (EXIF, filename, dimensions) → instant classification
  2. Perceptual hash deduplication (pHash/dHash) → find duplicates & bursts
  3. Quality analysis (blur, darkness, size) → detect accidental photos
  4. Qwen3-VL-8B classification (only for ambiguous images) → AI-powered sorting

Safety: NEVER deletes files. Moves them to staging folders for review.

Requirements:
  pip install Pillow imagehash requests tqdm
  Ollama running with: ollama pull qwen3-vl:8b

Usage:
  python photo_cleaner.py --source "Z:/photo/PhotoLibrary" --output "Z:/photo/_cleanup"
  python photo_cleaner.py --source "/volume1/photo/PhotoLibrary" --output "/volume1/photo/_cleanup"
"""

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageStat, ExifTags
    from PIL.ExifTags import Tags as ExifTagNames
except ImportError:
    print("ERROR: Pillow is required. Install with: pip install Pillow")
    sys.exit(1)

try:
    import imagehash
except ImportError:
    print("ERROR: imagehash is required. Install with: pip install imagehash")
    sys.exit(1)

try:
    from tqdm import tqdm
except ImportError:
    # Fallback if tqdm not installed
    def tqdm(iterable, **kwargs):
        return iterable

try:
    import requests
except ImportError:
    print("ERROR: requests is required. Install with: pip install requests")
    sys.exit(1)


# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
    ".tiff", ".tif", ".heic", ".heif", ".raw", ".cr2",
    ".nef", ".arw", ".dng", ".orf", ".rw2",
}

# Screen aspect ratios (width/height) for screenshot detection
SCREEN_RATIOS = {
    # Phone screens (portrait)
    9/16,       # 16:9 standard
    9/18,       # 18:9 (Samsung, LG)
    9/18.5,     # 18.5:9 (Samsung Galaxy)
    9/19,       # 19:9 (common)
    9/19.5,     # 19.5:9 (iPhone X+)
    9/20,       # 20:9 (many Androids)
    9/21,       # 21:9 (Sony Xperia)
    # Tablets
    3/4,        # iPad
    # Desktop
    16/9,       # Standard monitor
    16/10,      # MacBook
    21/9,       # Ultrawide
}
RATIO_TOLERANCE = 0.02

# Common phone screen widths (pixels)
PHONE_SCREEN_WIDTHS = {
    720, 750, 828, 1080, 1125, 1170, 1179, 1242,
    1284, 1290, 1440, 1536, 2160, 2532, 2556,
    2688, 2778, 2796,
}

# WhatsApp/social media filename patterns
SOCIAL_MEDIA_PATTERNS = [
    r"^IMG-\d{8}-WA\d+",           # WhatsApp
    r"^VID-\d{8}-WA\d+",           # WhatsApp video
    r"^STK-\d{8}-WA\d+",           # WhatsApp sticker
    r"^PTT-\d{8}-WA\d+",           # WhatsApp voice
    r"^FB_IMG_\d+",                 # Facebook
    r"^received_\d+",              # Messenger
    r"^signal-\d{4}-\d{2}-\d{2}",  # Signal
    r"^telegram-cloud-photo",      # Telegram
    r"^InShot_\d+",                # InShot
    r"^Snapchat-\d+",              # Snapchat
]

# Screenshot filename patterns
SCREENSHOT_PATTERNS = [
    r"^Screenshot[\s_-]",
    r"^Captura[\s_-]",
    r"^Screen[\s_]Shot",
    r"^Pantallazo",
    r"^Pantalla[\s_-]",
    r"^Screen[\s_]Recording",
    r"^Grabaci[oó]n",
    r"^scr_\d+",
]

# Meme indicators in filenames
MEME_PATTERNS = [
    r"^meme",
    r"^sticker",
    r"^GIF[\s_-]",
    r"\.(gif)$",  # GIFs are often memes/stickers
]

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen3-vl:8b"


# ─────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────

class Category(Enum):
    KEEP = "keep"
    SCREENSHOT = "screenshot"
    MEME_STICKER = "meme_sticker"
    DOCUMENT = "document"
    DUPLICATE = "duplicate"
    LOW_QUALITY = "low_quality"
    ACCIDENTAL = "accidental"
    REVIEW = "review"

class Action(Enum):
    KEEP = "keep"
    DELETE = "delete"
    ARCHIVE = "archive"
    REVIEW = "review"

# Map categories to actions
CATEGORY_ACTIONS = {
    Category.KEEP: Action.KEEP,
    Category.SCREENSHOT: Action.DELETE,
    Category.MEME_STICKER: Action.DELETE,
    Category.DOCUMENT: Action.ARCHIVE,
    Category.DUPLICATE: Action.DELETE,
    Category.LOW_QUALITY: Action.DELETE,
    Category.ACCIDENTAL: Action.DELETE,
    Category.REVIEW: Action.REVIEW,
}

@dataclass
class PhotoResult:
    path: str
    filename: str
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    category: str = "unknown"
    action: str = "review"
    confidence: float = 0.0
    classified_by: str = "unknown"  # metadata | hash | quality | vision
    reason: str = ""
    phash: str = ""
    group_id: str = ""  # For duplicates


# ─────────────────────────────────────────────
# Step 1: Metadata Scanner
# ─────────────────────────────────────────────

class MetadataScanner:
    """Classifies images using only metadata — no pixel analysis needed."""

    def __init__(self, screenshot_age_days: int = 180):
        self.screenshot_age_days = screenshot_age_days
        self.social_re = [re.compile(p, re.IGNORECASE) for p in SOCIAL_MEDIA_PATTERNS]
        self.screenshot_re = [re.compile(p, re.IGNORECASE) for p in SCREENSHOT_PATTERNS]
        self.meme_re = [re.compile(p, re.IGNORECASE) for p in MEME_PATTERNS]

    def scan(self, filepath: Path) -> Optional[PhotoResult]:
        """Attempt to classify based on metadata alone. Returns None if unsure."""
        result = PhotoResult(
            path=str(filepath),
            filename=filepath.name,
            classified_by="metadata",
        )

        try:
            stat = filepath.stat()
            result.size_bytes = stat.st_size
        except OSError:
            return None

        # Check filename patterns first (cheapest check)
        fname = filepath.stem

        # Screenshots by filename
        for pattern in self.screenshot_re:
            if pattern.search(fname):
                result.category = Category.SCREENSHOT.value
                result.action = Action.DELETE.value
                result.confidence = 0.95
                result.reason = f"Screenshot filename pattern: {fname}"
                return result

        # Social media / WhatsApp
        for pattern in self.social_re:
            if pattern.search(fname):
                result.category = Category.MEME_STICKER.value
                result.action = Action.DELETE.value
                result.confidence = 0.85
                result.reason = f"Social media filename: {fname}"
                return result

        # Memes by filename
        for pattern in self.meme_re:
            if pattern.search(fname):
                result.category = Category.MEME_STICKER.value
                result.action = Action.DELETE.value
                result.confidence = 0.80
                result.reason = f"Meme/sticker filename: {fname}"
                return result

        # Try to open and check dimensions + EXIF
        try:
            with Image.open(filepath) as img:
                result.width, result.height = img.size

                # Very small images are likely thumbnails/stickers
                if result.width < 200 and result.height < 200:
                    result.category = Category.MEME_STICKER.value
                    result.action = Action.DELETE.value
                    result.confidence = 0.80
                    result.reason = f"Tiny image: {result.width}x{result.height}"
                    return result

                # Check for screenshot by dimensions
                if self._is_screenshot_dims(result.width, result.height):
                    # Check EXIF — real photos have camera data
                    exif = self._get_exif(img)
                    has_camera = exif.get("Make") or exif.get("Model")
                    has_gps = exif.get("GPSInfo")

                    if not has_camera and not has_gps:
                        result.category = Category.SCREENSHOT.value
                        result.action = Action.DELETE.value
                        result.confidence = 0.90
                        result.reason = (
                            f"Screen dimensions ({result.width}x{result.height}), "
                            f"no camera EXIF"
                        )
                        return result

        except Exception:
            pass  # Can't open image, will be handled by later stages

        return None  # Not sure — pass to next stage

    def _is_screenshot_dims(self, w: int, h: int) -> bool:
        """Check if dimensions match a known screen resolution."""
        # Check exact screen widths
        if w in PHONE_SCREEN_WIDTHS or h in PHONE_SCREEN_WIDTHS:
            ratio = min(w, h) / max(w, h)
            for screen_ratio in SCREEN_RATIOS:
                if abs(ratio - screen_ratio) < RATIO_TOLERANCE:
                    return True
        return False

    def _get_exif(self, img: Image.Image) -> dict:
        """Extract EXIF data as a simple dict."""
        exif_data = {}
        try:
            raw_exif = img.getexif()
            if raw_exif:
                for tag_id, value in raw_exif.items():
                    tag = ExifTags.TAGS.get(tag_id, str(tag_id))
                    exif_data[tag] = value
        except Exception:
            pass
        return exif_data


# ─────────────────────────────────────────────
# Step 2: Perceptual Hash Deduplicator
# ─────────────────────────────────────────────

class HashDeduplicator:
    """Find duplicate and near-duplicate images using perceptual hashing."""

    def __init__(self, hash_size: int = 16, threshold: int = 10):
        """
        hash_size: Resolution of the hash (16 = 16x16 = 256 bits)
        threshold: Max hamming distance to consider as duplicate
        """
        self.hash_size = hash_size
        self.threshold = threshold
        self.hashes: dict[str, list[PhotoResult]] = defaultdict(list)

    def compute_hash(self, filepath: Path) -> Optional[str]:
        """Compute perceptual hash for an image."""
        try:
            with Image.open(filepath) as img:
                # Convert to RGB to normalize
                if img.mode != "RGB":
                    img = img.convert("RGB")
                phash = imagehash.phash(img, hash_size=self.hash_size)
                return str(phash)
        except Exception:
            return None

    def find_duplicates(self, results: list[PhotoResult]) -> list[PhotoResult]:
        """
        Given a list of PhotoResults (those not yet classified),
        find groups of duplicates. Returns results with duplicates marked.
        """
        # Compute hashes
        hash_map: dict[str, list[PhotoResult]] = defaultdict(list)
        for r in results:
            h = self.compute_hash(Path(r.path))
            if h:
                r.phash = h
                hash_map[h].append(r)

        # Find groups (exact hash matches first, then near-matches)
        processed = set()
        group_id = 0
        duplicates_found = []

        hash_list = list(hash_map.keys())
        for i, h1 in enumerate(hash_list):
            if h1 in processed:
                continue

            group = list(hash_map[h1])
            ih1 = imagehash.hex_to_hash(h1)

            # Check for near-matches
            for j in range(i + 1, len(hash_list)):
                h2 = hash_list[j]
                if h2 in processed:
                    continue
                ih2 = imagehash.hex_to_hash(h2)
                if ih1 - ih2 <= self.threshold:
                    group.extend(hash_map[h2])
                    processed.add(h2)

            processed.add(h1)

            if len(group) > 1:
                group_id += 1
                # Keep the largest file (usually best quality)
                group.sort(key=lambda r: r.size_bytes, reverse=True)

                # First one is the "keeper"
                group[0].group_id = f"dup_group_{group_id}"
                group[0].category = Category.KEEP.value
                group[0].reason = f"Best in duplicate group {group_id} ({len(group)} images)"

                # Rest are duplicates
                for dup in group[1:]:
                    dup.category = Category.DUPLICATE.value
                    dup.action = Action.DELETE.value
                    dup.confidence = 0.85
                    dup.classified_by = "hash"
                    dup.group_id = f"dup_group_{group_id}"
                    dup.reason = (
                        f"Duplicate of {group[0].filename} "
                        f"(group {group_id}, {len(group)} images)"
                    )
                    duplicates_found.append(dup)

        return duplicates_found


# ─────────────────────────────────────────────
# Step 3: Quality Analyzer
# ─────────────────────────────────────────────

class QualityAnalyzer:
    """Detect low-quality and accidental photos using image statistics."""

    def __init__(
        self,
        blur_threshold: float = 50.0,
        dark_threshold: float = 25.0,
        bright_threshold: float = 240.0,
        min_dimension: int = 100,
    ):
        self.blur_threshold = blur_threshold
        self.dark_threshold = dark_threshold
        self.bright_threshold = bright_threshold
        self.min_dimension = min_dimension

    def analyze(self, filepath: Path) -> Optional[PhotoResult]:
        """Analyze image quality. Returns result if it's junk, None if OK."""
        try:
            with Image.open(filepath) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")

                w, h = img.size
                stat = ImageStat.Stat(img)

                # Mean brightness across channels
                mean_brightness = sum(stat.mean) / 3

                # Check for completely dark images (pocket shots)
                if mean_brightness < self.dark_threshold:
                    return PhotoResult(
                        path=str(filepath),
                        filename=filepath.name,
                        size_bytes=filepath.stat().st_size,
                        width=w,
                        height=h,
                        category=Category.ACCIDENTAL.value,
                        action=Action.DELETE.value,
                        confidence=0.90,
                        classified_by="quality",
                        reason=f"Very dark image (brightness: {mean_brightness:.1f})",
                    )

                # Check for completely white/overexposed
                if mean_brightness > self.bright_threshold:
                    return PhotoResult(
                        path=str(filepath),
                        filename=filepath.name,
                        size_bytes=filepath.stat().st_size,
                        width=w,
                        height=h,
                        category=Category.ACCIDENTAL.value,
                        action=Action.DELETE.value,
                        confidence=0.85,
                        classified_by="quality",
                        reason=f"Overexposed image (brightness: {mean_brightness:.1f})",
                    )

                # Check for blur using Laplacian variance
                # Convert to grayscale for blur detection
                gray = img.convert("L")
                blur_score = self._laplacian_variance(gray)
                if blur_score < self.blur_threshold:
                    return PhotoResult(
                        path=str(filepath),
                        filename=filepath.name,
                        size_bytes=filepath.stat().st_size,
                        width=w,
                        height=h,
                        category=Category.LOW_QUALITY.value,
                        action=Action.REVIEW.value,  # Blurry → review, not auto-delete
                        confidence=0.70,
                        classified_by="quality",
                        reason=f"Blurry image (score: {blur_score:.1f})",
                    )

        except Exception as e:
            logging.debug(f"Quality analysis failed for {filepath}: {e}")

        return None

    def _laplacian_variance(self, gray_img: Image.Image) -> float:
        """
        Compute Laplacian variance as a measure of image sharpness.
        Lower values = more blur.
        """
        import numpy as np
        arr = np.array(gray_img, dtype=np.float64)

        # Simple Laplacian kernel convolution
        # We use a basic approach: compare each pixel to its neighbors
        if arr.shape[0] < 3 or arr.shape[1] < 3:
            return 0.0

        laplacian = (
            arr[:-2, 1:-1] + arr[2:, 1:-1] +
            arr[1:-1, :-2] + arr[1:-1, 2:] -
            4 * arr[1:-1, 1:-1]
        )
        return float(laplacian.var())


# ─────────────────────────────────────────────
# Step 4: Vision Model Classifier (Qwen3-VL)
# ─────────────────────────────────────────────

class VisionClassifier:
    """Use Qwen3-VL-8B via Ollama for ambiguous image classification."""

    CLASSIFICATION_PROMPT = """/no_think
Classify this image into exactly ONE of these categories. Respond with ONLY the JSON object, nothing else.

Categories:
- "screenshot": Screen capture, app interface, chat screenshot, notification, map screenshot
- "meme_sticker": Meme, sticker, reaction image, viral image, image with overlaid text for humor
- "document": Receipt, invoice, ID, ticket, menu, handwritten note, business card, form
- "low_quality": Very blurry, accidental pocket photo, completely dark, finger over lens
- "photo": Legitimate personal photo worth keeping (people, places, events, food, pets, scenery)

Respond ONLY with this exact JSON format:
{"category": "<one of the above>", "confidence": <0.0-1.0>}"""

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_MODEL,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def is_available(self) -> bool:
        """Check if Ollama is running and model is available."""
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if r.status_code == 200:
                models = [m["name"] for m in r.json().get("models", [])]
                # Check for exact or partial match
                for m in models:
                    if self.model.split(":")[0] in m:
                        return True
                logging.warning(
                    f"Model '{self.model}' not found. Available: {models}"
                )
                logging.warning(f"Pull it with: ollama pull {self.model}")
            return False
        except Exception as e:
            logging.warning(f"Ollama not reachable: {e}")
            return False

    def classify(self, filepath: Path) -> Optional[PhotoResult]:
        """Classify a single image using the vision model."""
        import base64

        try:
            # Read and encode image
            with open(filepath, "rb") as f:
                img_bytes = f.read()

            # Resize if too large (save tokens/time)
            img_b64 = self._prepare_image(filepath, img_bytes)

            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": self.CLASSIFICATION_PROMPT,
                        "images": [img_b64],
                    }
                ],
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 100,
                },
            }

            r = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            )

            if r.status_code != 200:
                logging.warning(f"Ollama returned {r.status_code} for {filepath.name}")
                return None

            response_text = r.json().get("message", {}).get("content", "")
            return self._parse_response(response_text, filepath)

        except requests.Timeout:
            logging.warning(f"Timeout classifying {filepath.name}")
            return None
        except Exception as e:
            logging.warning(f"Vision classification failed for {filepath.name}: {e}")
            return None

    def _prepare_image(self, filepath: Path, img_bytes: bytes) -> str:
        """Resize image if needed and return base64."""
        import base64

        try:
            with Image.open(filepath) as img:
                # Resize to max 1024px on longest side for speed
                max_side = max(img.size)
                if max_side > 1024:
                    ratio = 1024 / max_side
                    new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                    img = img.resize(new_size, Image.LANCZOS)

                if img.mode != "RGB":
                    img = img.convert("RGB")

                from io import BytesIO
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=85)
                return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception:
            # Fallback: send original
            return base64.b64encode(img_bytes).decode("utf-8")

    def _parse_response(self, text: str, filepath: Path) -> Optional[PhotoResult]:
        """Parse the model's JSON response."""
        try:
            # Try to extract JSON from response
            text = text.strip()
            # Handle potential markdown code blocks
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            # Find JSON object
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
            else:
                logging.warning(f"No JSON found in response: {text[:100]}")
                return None

            cat = data.get("category", "").lower()
            conf = float(data.get("confidence", 0.5))

            # Map vision model categories
            category_map = {
                "screenshot": Category.SCREENSHOT,
                "meme_sticker": Category.MEME_STICKER,
                "meme": Category.MEME_STICKER,
                "sticker": Category.MEME_STICKER,
                "document": Category.DOCUMENT,
                "low_quality": Category.LOW_QUALITY,
                "photo": Category.KEEP,
            }

            category = category_map.get(cat, Category.REVIEW)
            action = CATEGORY_ACTIONS.get(category, Action.REVIEW)

            stat = filepath.stat()
            try:
                with Image.open(filepath) as img:
                    w, h = img.size
            except Exception:
                w, h = 0, 0

            return PhotoResult(
                path=str(filepath),
                filename=filepath.name,
                size_bytes=stat.st_size,
                width=w,
                height=h,
                category=category.value,
                action=action.value,
                confidence=conf,
                classified_by="vision",
                reason=f"Qwen3-VL classified as '{cat}' (confidence: {conf:.2f})",
            )

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logging.warning(f"Failed to parse vision response for {filepath.name}: {e}")
            return None


# ─────────────────────────────────────────────
# Pipeline Orchestrator
# ─────────────────────────────────────────────

class PhotoCleanerPipeline:
    """Orchestrates the full hybrid cleaning pipeline."""

    def __init__(
        self,
        source_dir: str,
        output_dir: str,
        dry_run: bool = True,
        vision_enabled: bool = True,
        screenshot_age_days: int = 180,
        hash_threshold: int = 10,
        blur_threshold: float = 50.0,
        max_vision_batch: int = 500,
    ):
        self.source_dir = Path(source_dir)
        self.output_dir = Path(output_dir)
        self.dry_run = dry_run
        self.vision_enabled = vision_enabled
        self.max_vision_batch = max_vision_batch

        # Initialize stages
        self.metadata_scanner = MetadataScanner(screenshot_age_days=screenshot_age_days)
        self.deduplicator = HashDeduplicator(threshold=hash_threshold)
        self.quality_analyzer = QualityAnalyzer(blur_threshold=blur_threshold)
        self.vision_classifier = VisionClassifier() if vision_enabled else None

        # Results
        self.all_results: list[PhotoResult] = []
        self.stats = {
            "total": 0,
            "classified_metadata": 0,
            "classified_hash": 0,
            "classified_quality": 0,
            "classified_vision": 0,
            "kept": 0,
            "to_delete": 0,
            "to_archive": 0,
            "to_review": 0,
            "space_recoverable_mb": 0,
        }

    def discover_images(self) -> list[Path]:
        """Find all image files in source directory."""
        images = []
        logging.info(f"Scanning {self.source_dir} for images...")

        for root, dirs, files in os.walk(self.source_dir):
            # Skip hidden dirs and @eaDir (Synology thumbnail cache)
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d != "@eaDir" and d != "_cleanup"
            ]
            for f in files:
                if Path(f).suffix.lower() in IMAGE_EXTENSIONS:
                    images.append(Path(root) / f)

        logging.info(f"Found {len(images)} images")
        self.stats["total"] = len(images)
        return images

    def run(self):
        """Execute the full pipeline."""
        start_time = time.time()

        print("\n" + "=" * 60)
        print("  NAS Photo Cleaner — Hybrid Pipeline")
        print("=" * 60)
        print(f"  Source:  {self.source_dir}")
        print(f"  Output:  {self.output_dir}")
        print(f"  Mode:    {'DRY RUN (no files moved)' if self.dry_run else 'LIVE (files will be moved)'}")
        print("=" * 60 + "\n")

        # Discover
        images = self.discover_images()
        if not images:
            print("No images found. Check your source path.")
            return

        # ── Stage 1: Metadata ──
        print(f"\n{'─' * 40}")
        print(f"  Stage 1/4: Metadata scan")
        print(f"{'─' * 40}")
        unclassified = []
        for img_path in tqdm(images, desc="Scanning metadata", unit="img"):
            result = self.metadata_scanner.scan(img_path)
            if result:
                self.all_results.append(result)
                self.stats["classified_metadata"] += 1
            else:
                # Create a basic result for unclassified
                try:
                    stat = img_path.stat()
                    with Image.open(img_path) as img:
                        w, h = img.size
                except Exception:
                    stat = img_path.stat()
                    w, h = 0, 0

                r = PhotoResult(
                    path=str(img_path),
                    filename=img_path.name,
                    size_bytes=stat.st_size if stat else 0,
                    width=w,
                    height=h,
                )
                unclassified.append(r)

        print(f"  → Classified {self.stats['classified_metadata']} images by metadata")
        print(f"  → {len(unclassified)} remain unclassified")

        # ── Stage 2: Deduplication ──
        print(f"\n{'─' * 40}")
        print(f"  Stage 2/4: Perceptual hash deduplication")
        print(f"{'─' * 40}")
        print("  Computing perceptual hashes...")
        duplicates = self.deduplicator.find_duplicates(unclassified)
        self.stats["classified_hash"] = len(duplicates)

        # Remove duplicates from unclassified
        dup_paths = {d.path for d in duplicates}
        self.all_results.extend(duplicates)
        unclassified = [r for r in unclassified if r.path not in dup_paths]

        print(f"  → Found {len(duplicates)} duplicates")
        print(f"  → {len(unclassified)} remain unclassified")

        # ── Stage 3: Quality Analysis ──
        print(f"\n{'─' * 40}")
        print(f"  Stage 3/4: Quality analysis")
        print(f"{'─' * 40}")
        still_unclassified = []
        for r in tqdm(unclassified, desc="Analyzing quality", unit="img"):
            quality_result = self.quality_analyzer.analyze(Path(r.path))
            if quality_result:
                self.all_results.append(quality_result)
                self.stats["classified_quality"] += 1
            else:
                still_unclassified.append(r)

        unclassified = still_unclassified
        print(f"  → Classified {self.stats['classified_quality']} as low quality/accidental")
        print(f"  → {len(unclassified)} remain unclassified")

        # ── Stage 4: Vision Model ──
        print(f"\n{'─' * 40}")
        print(f"  Stage 4/4: Qwen3-VL classification")
        print(f"{'─' * 40}")

        if self.vision_enabled and self.vision_classifier:
            if self.vision_classifier.is_available():
                # Only send ambiguous images to the model
                to_classify = unclassified[: self.max_vision_batch]
                if len(unclassified) > self.max_vision_batch:
                    print(
                        f"  ⚠ Limiting to {self.max_vision_batch} images "
                        f"({len(unclassified)} available)"
                    )

                for r in tqdm(to_classify, desc="AI classification", unit="img"):
                    vision_result = self.vision_classifier.classify(Path(r.path))
                    if vision_result:
                        self.all_results.append(vision_result)
                        self.stats["classified_vision"] += 1
                    else:
                        # Couldn't classify → send to review
                        r.category = Category.REVIEW.value
                        r.action = Action.REVIEW.value
                        r.classified_by = "fallback"
                        r.reason = "Could not be classified by any stage"
                        self.all_results.append(r)

                # Handle overflow
                for r in unclassified[self.max_vision_batch:]:
                    r.category = Category.REVIEW.value
                    r.action = Action.REVIEW.value
                    r.classified_by = "overflow"
                    r.reason = "Exceeded vision batch limit"
                    self.all_results.append(r)

                print(f"  → AI classified {self.stats['classified_vision']} images")
            else:
                print("  ⚠ Ollama not available — sending remaining to review")
                for r in unclassified:
                    r.category = Category.REVIEW.value
                    r.action = Action.REVIEW.value
                    r.classified_by = "no_vision"
                    r.reason = "Vision model not available"
                    self.all_results.append(r)
        else:
            print("  → Vision disabled — sending remaining to review")
            for r in unclassified:
                r.category = Category.REVIEW.value
                r.action = Action.REVIEW.value
                r.classified_by = "disabled"
                r.reason = "Vision classification disabled"
                self.all_results.append(r)

        # ── Compute stats ──
        for r in self.all_results:
            if r.action == Action.KEEP.value:
                self.stats["kept"] += 1
            elif r.action == Action.DELETE.value:
                self.stats["to_delete"] += 1
                self.stats["space_recoverable_mb"] += r.size_bytes / (1024 * 1024)
            elif r.action == Action.ARCHIVE.value:
                self.stats["to_archive"] += 1
            elif r.action == Action.REVIEW.value:
                self.stats["to_review"] += 1

        # ── Report ──
        elapsed = time.time() - start_time
        self._print_report(elapsed)

        # ── Execute moves ──
        if not self.dry_run:
            self._execute_moves()
        else:
            print("\n  ℹ DRY RUN — no files were moved.")
            print(f"  Run with --execute to move files.\n")

        # ── Export CSV ──
        self._export_csv()

    def _print_report(self, elapsed: float):
        """Print final summary report."""
        print(f"\n{'=' * 60}")
        print(f"  RESULTS")
        print(f"{'=' * 60}")
        print(f"  Total images scanned:      {self.stats['total']:>8,}")
        print(f"  ─────────────────────────────────────")
        print(f"  Classified by metadata:     {self.stats['classified_metadata']:>8,}")
        print(f"  Classified by hash:         {self.stats['classified_hash']:>8,}")
        print(f"  Classified by quality:      {self.stats['classified_quality']:>8,}")
        print(f"  Classified by AI:           {self.stats['classified_vision']:>8,}")
        print(f"  ─────────────────────────────────────")
        print(f"  ✓ Keep:                     {self.stats['kept']:>8,}")
        print(f"  ✗ Delete (trash):           {self.stats['to_delete']:>8,}")
        print(f"  ▸ Archive (documents):      {self.stats['to_archive']:>8,}")
        print(f"  ? Review (manual):          {self.stats['to_review']:>8,}")
        print(f"  ─────────────────────────────────────")
        print(f"  Space recoverable:          {self.stats['space_recoverable_mb']:>7,.1f} MB")
        print(f"  Time elapsed:               {elapsed:>7.1f} s")
        print(f"{'=' * 60}")

    def _execute_moves(self):
        """Move files to staging directories."""
        print("\n  Moving files to staging directories...")

        dirs = {
            Action.DELETE.value: self.output_dir / "trash",
            Action.ARCHIVE.value: self.output_dir / "documents",
            Action.REVIEW.value: self.output_dir / "review",
        }

        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)

        moved = 0
        errors = 0
        for r in tqdm(self.all_results, desc="Moving files", unit="file"):
            if r.action == Action.KEEP.value:
                continue

            target_dir = dirs.get(r.action)
            if not target_dir:
                continue

            src = Path(r.path)
            if not src.exists():
                continue

            # Preserve relative path structure inside staging
            try:
                rel = src.relative_to(self.source_dir)
            except ValueError:
                rel = Path(src.name)

            dst = target_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)

            # Handle name collisions
            if dst.exists():
                stem = dst.stem
                suffix = dst.suffix
                counter = 1
                while dst.exists():
                    dst = dst.parent / f"{stem}_{counter}{suffix}"
                    counter += 1

            try:
                shutil.move(str(src), str(dst))
                moved += 1
            except Exception as e:
                logging.error(f"Failed to move {src}: {e}")
                errors += 1

        print(f"  → Moved {moved} files ({errors} errors)")

    def _export_csv(self):
        """Export results as CSV for review."""
        csv_path = self.output_dir / "classification_report.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "filename", "path", "size_bytes", "width", "height",
                    "category", "action", "confidence", "classified_by",
                    "reason", "phash", "group_id",
                ],
            )
            writer.writeheader()
            for r in sorted(self.all_results, key=lambda x: (x.action, x.category)):
                writer.writerow(asdict(r))

        print(f"\n  📄 Report saved to: {csv_path}")


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="NAS Photo Cleaner — Hybrid Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run (safe — just scan and report)
  python photo_cleaner.py --source "Z:/photo/PhotoLibrary" --output "Z:/photo/_cleanup"

  # Live run (moves files to staging)
  python photo_cleaner.py --source "Z:/photo/PhotoLibrary" --output "Z:/photo/_cleanup" --execute

  # Without AI (just metadata + hashes + quality)
  python photo_cleaner.py --source "/mnt/nas/photo" --output "/mnt/nas/_cleanup" --no-vision

  # Custom thresholds
  python photo_cleaner.py --source "Z:/photo" --output "Z:/_cleanup" --blur-threshold 80 --hash-threshold 8
        """,
    )

    parser.add_argument(
        "--source", required=True,
        help="Path to your photo library (e.g., Z:/photo/PhotoLibrary)",
    )
    parser.add_argument(
        "--output", required=True,
        help="Path for staging folders (trash/review/documents)",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually move files (default is dry-run)",
    )
    parser.add_argument(
        "--no-vision", action="store_true",
        help="Skip AI classification (faster, less accurate)",
    )
    parser.add_argument(
        "--ollama-url", default=OLLAMA_BASE_URL,
        help=f"Ollama API URL (default: {OLLAMA_BASE_URL})",
    )
    parser.add_argument(
        "--model", default=OLLAMA_MODEL,
        help=f"Vision model to use (default: {OLLAMA_MODEL})",
    )
    parser.add_argument(
        "--screenshot-age", type=int, default=180,
        help="Days after which old screenshots are auto-deleted (default: 180)",
    )
    parser.add_argument(
        "--hash-threshold", type=int, default=10,
        help="Hamming distance for duplicate detection (lower=stricter, default: 10)",
    )
    parser.add_argument(
        "--blur-threshold", type=float, default=50.0,
        help="Laplacian variance below which images are 'blurry' (default: 50.0)",
    )
    parser.add_argument(
        "--max-vision", type=int, default=500,
        help="Max images to send to AI model (default: 500)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Update globals if custom model/url
    global OLLAMA_BASE_URL, OLLAMA_MODEL
    OLLAMA_BASE_URL = args.ollama_url
    OLLAMA_MODEL = args.model

    pipeline = PhotoCleanerPipeline(
        source_dir=args.source,
        output_dir=args.output,
        dry_run=not args.execute,
        vision_enabled=not args.no_vision,
        screenshot_age_days=args.screenshot_age,
        hash_threshold=args.hash_threshold,
        blur_threshold=args.blur_threshold,
        max_vision_batch=args.max_vision,
    )

    pipeline.run()


if __name__ == "__main__":
    main()
