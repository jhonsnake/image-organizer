"""
NAS Photo Cleanup Pipeline - Hybrid Intelligent Approach
=========================================================
Combines fast metadata/hash/quality filters with local Qwen3-VL-8B
vision model classification. Designed for Synology NAS + RTX 5080.

Usage:
    python cleanup.py                    # Dry run (default)
    python cleanup.py --execute          # Actually move files
    python cleanup.py --step 1           # Run only step 1
    python cleanup.py --step 1-3         # Run steps 1 through 3
    python cleanup.py --resume           # Resume from last checkpoint
"""

import argparse
import asyncio
import base64
import fnmatch
import hashlib
import io
import json
import logging
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

import cv2
import httpx
import imagehash
import yaml
from PIL import Image, ExifTags
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.panel import Panel

# Try to import HEIF support
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False


# ============================================================
# Data Models
# ============================================================

class Action(Enum):
    KEEP = "keep"
    TRASH = "trash"
    REVIEW = "review"
    DOCUMENTS = "documents"


class Reason(Enum):
    # Step 1: Metadata
    SCREENSHOT_NO_EXIF = "screenshot_no_camera_exif"
    SCREENSHOT_OLD = "screenshot_older_than_threshold"
    MESSAGING_IMAGE = "messaging_app_image"
    TOO_SMALL_FILE = "file_too_small"
    # Step 2: Dedup
    DUPLICATE_BURST = "duplicate_or_burst"
    # Step 3: Quality
    BLURRY = "blurry_image"
    TOO_DARK = "too_dark"
    OVEREXPOSED = "overexposed"
    TOO_SMALL_DIM = "dimensions_too_small"
    # Step 4: Vision
    VISION_MEME = "vision_classified_meme"
    VISION_SCREENSHOT = "vision_classified_screenshot"
    VISION_DOCUMENT = "vision_classified_document"
    VISION_ACCIDENTAL = "vision_classified_accidental"
    VISION_AMBIGUOUS = "vision_ambiguous"
    # Default
    LEGITIMATE = "legitimate_photo"


@dataclass
class PhotoRecord:
    path: str
    filename: str
    size_bytes: int
    extension: str
    action: Action = Action.KEEP
    reason: Reason = Reason.LEGITIMATE
    step_decided: int = 0
    confidence: float = 1.0
    # Metadata
    width: int = 0
    height: int = 0
    has_camera_exif: bool = False
    date_taken: Optional[str] = None
    camera_make: Optional[str] = None
    # Quality
    blur_score: float = 0.0
    brightness: float = 128.0
    # Hash
    phash: Optional[str] = None
    # Vision
    vision_label: Optional[str] = None
    vision_confidence: float = 0.0


# ============================================================
# Pipeline Engine
# ============================================================

class CleanupPipeline:
    def __init__(self, config_path: str = "config.yaml"):
        self.console = Console()
        self.config = self._load_config(config_path)
        self.records: list[PhotoRecord] = []
        self.stats = defaultdict(int)
        self.checkpoint_file = Path("checkpoint.json")
        self.dry_run = self.config["general"]["dry_run"]

        # Setup logging
        log_path = self.config["general"]["log_file"]
        self.log_file = open(log_path, "a", encoding="utf-8")

    def _load_config(self, path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _log_action(self, record: PhotoRecord):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "file": record.path,
            "action": record.action.value,
            "reason": record.reason.value,
            "step": record.step_decided,
            "confidence": record.confidence,
        }
        self.log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.log_file.flush()

    # --------------------------------------------------------
    # Step 0: Scan and inventory
    # --------------------------------------------------------
    def scan_files(self):
        """Walk the source directory and build the initial file inventory."""
        source = Path(self.config["source_dir"])
        extensions = set(self.config["general"]["extensions"])

        self.console.print(f"\n[bold]Scanning:[/bold] {source}")

        if not source.exists():
            self.console.print(f"[red]ERROR: Source directory not found: {source}[/red]")
            self.console.print("[yellow]Make sure your NAS drive is mapped and the path in config.yaml is correct.[/yellow]")
            sys.exit(1)

        count = 0
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed} files"),
            TimeElapsedColumn(),
            console=self.console,
        ) as progress:
            task = progress.add_task("Scanning files...", total=None)

            for root, _, files in os.walk(source):
                # Skip staging directories
                if "_cleanup" in root:
                    continue
                for fname in files:
                    ext = Path(fname).suffix.lower()
                    if ext in extensions:
                        fpath = os.path.join(root, fname)
                        try:
                            stat = os.stat(fpath)
                            self.records.append(PhotoRecord(
                                path=fpath,
                                filename=fname,
                                size_bytes=stat.st_size,
                                extension=ext,
                            ))
                            count += 1
                            progress.update(task, completed=count)
                        except OSError:
                            pass

        self.stats["total_files"] = count
        self.console.print(f"  Found [bold]{count:,}[/bold] images\n")

    # --------------------------------------------------------
    # Step 1: Metadata filter
    # --------------------------------------------------------
    def step1_metadata_filter(self):
        """Filter screenshots, messaging images, and tiny files using metadata only."""
        self.console.print("[bold cyan]Step 1:[/bold cyan] Metadata filter (no AI)")
        cfg = self.config["metadata"]
        screenshot_ratios = {tuple(r) for r in cfg["screenshot_ratios"]}
        patterns = cfg["messaging_patterns"]
        max_age = timedelta(days=cfg["screenshot_max_age_days"])
        min_size = cfg["min_file_size_bytes"]
        now = datetime.now()

        decided = 0
        pending = [r for r in self.records if r.action == Action.KEEP]

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(), console=self.console,
        ) as progress:
            task = progress.add_task("Analyzing metadata...", total=len(pending))

            for record in pending:
                progress.advance(task)

                # --- File size check ---
                if record.size_bytes < min_size:
                    record.action = Action.TRASH
                    record.reason = Reason.TOO_SMALL_FILE
                    record.step_decided = 1
                    decided += 1
                    continue

                # --- Messaging app filename patterns ---
                matched_messaging = False
                for pattern in patterns:
                    if fnmatch.fnmatch(record.filename.lower(), pattern.lower()):
                        record.action = Action.TRASH
                        record.reason = Reason.MESSAGING_IMAGE
                        record.step_decided = 1
                        matched_messaging = True
                        decided += 1
                        break
                if matched_messaging:
                    continue

                # --- Read image + EXIF ---
                try:
                    with Image.open(record.path) as img:
                        record.width, record.height = img.size

                        # Extract EXIF
                        exif_data = img.getexif() if hasattr(img, "getexif") else {}
                        if exif_data:
                            # Check for camera make/model
                            make = exif_data.get(ExifTags.Base.Make, "")
                            model = exif_data.get(ExifTags.Base.Model, "")
                            record.camera_make = f"{make} {model}".strip() or None
                            record.has_camera_exif = bool(make or model)

                            # Date taken
                            date_str = exif_data.get(ExifTags.Base.DateTimeOriginal, "")
                            if date_str:
                                record.date_taken = date_str
                except Exception:
                    continue

                # --- Screenshot detection ---
                if record.width > 0 and record.height > 0 and not record.has_camera_exif:
                    w, h = record.width, record.height
                    # Normalize to portrait
                    short, long = min(w, h), max(w, h)
                    # Check against known screen ratios (with tolerance)
                    is_screen_ratio = False
                    for rw, rh in screenshot_ratios:
                        expected = rh / rw
                        actual = long / short
                        if abs(actual - expected) < 0.05:
                            is_screen_ratio = True
                            break

                    if is_screen_ratio:
                        # Check age for old screenshots
                        file_age = now - datetime.fromtimestamp(
                            os.path.getmtime(record.path)
                        )
                        if file_age > max_age:
                            record.action = Action.TRASH
                            record.reason = Reason.SCREENSHOT_OLD
                            record.step_decided = 1
                            decided += 1
                        else:
                            record.action = Action.REVIEW
                            record.reason = Reason.SCREENSHOT_NO_EXIF
                            record.step_decided = 1
                            decided += 1

        self.stats["step1_decided"] = decided
        self.console.print(f"  Classified [bold]{decided:,}[/bold] images "
                          f"({decided * 100 / max(len(self.records), 1):.1f}%)\n")

    # --------------------------------------------------------
    # Step 2: Perceptual hash deduplication
    # --------------------------------------------------------
    def step2_dedup(self):
        """Find duplicate/burst photos using perceptual hashing."""
        self.console.print("[bold cyan]Step 2:[/bold cyan] Perceptual hash deduplication")
        cfg = self.config["dedup"]
        threshold = cfg["hash_threshold"]
        keep_strategy = cfg["keep_strategy"]

        pending = [r for r in self.records if r.action == Action.KEEP]
        hashes: list[tuple[PhotoRecord, imagehash.ImageHash]] = []

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(), console=self.console,
        ) as progress:
            task = progress.add_task("Computing hashes...", total=len(pending))

            for record in pending:
                progress.advance(task)
                try:
                    with Image.open(record.path) as img:
                        # Resize for faster hashing
                        img.thumbnail((256, 256))
                        h = imagehash.phash(img)
                        record.phash = str(h)
                        hashes.append((record, h))
                except Exception:
                    pass

        # Group similar hashes
        groups: list[list[PhotoRecord]] = []
        used = set()

        for i, (rec_a, hash_a) in enumerate(hashes):
            if i in used:
                continue
            group = [rec_a]
            for j, (rec_b, hash_b) in enumerate(hashes[i + 1:], start=i + 1):
                if j in used:
                    continue
                if hash_a - hash_b <= threshold:
                    group.append(rec_b)
                    used.add(j)
            if len(group) >= cfg["min_group_size"]:
                used.add(i)
                groups.append(group)

        # Pick best from each group, mark others
        decided = 0
        for group in groups:
            if keep_strategy == "sharpest":
                # Quick blur estimation for sorting
                scored = []
                for rec in group:
                    try:
                        img_cv = cv2.imread(rec.path, cv2.IMREAD_GRAYSCALE)
                        if img_cv is not None:
                            blur = cv2.Laplacian(img_cv, cv2.CV_64F).var()
                            scored.append((rec, blur))
                        else:
                            scored.append((rec, 0))
                    except Exception:
                        scored.append((rec, 0))
                scored.sort(key=lambda x: x[1], reverse=True)
                best = scored[0][0]
            else:
                # Keep largest file
                group.sort(key=lambda r: r.size_bytes, reverse=True)
                best = group[0]

            for rec in group:
                if rec is not best:
                    rec.action = Action.REVIEW
                    rec.reason = Reason.DUPLICATE_BURST
                    rec.step_decided = 2
                    decided += 1

        self.stats["step2_groups"] = len(groups)
        self.stats["step2_decided"] = decided
        self.console.print(f"  Found [bold]{len(groups):,}[/bold] duplicate groups, "
                          f"marked [bold]{decided:,}[/bold] for review\n")

    # --------------------------------------------------------
    # Step 3: Quality analysis
    # --------------------------------------------------------
    def step3_quality(self):
        """Detect blurry, dark, overexposed, and tiny images."""
        self.console.print("[bold cyan]Step 3:[/bold cyan] Quality analysis (no AI)")
        cfg = self.config["quality"]

        pending = [r for r in self.records if r.action == Action.KEEP]
        decided = 0

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(), console=self.console,
        ) as progress:
            task = progress.add_task("Analyzing quality...", total=len(pending))

            for record in pending:
                progress.advance(task)

                try:
                    img_cv = cv2.imread(record.path)
                    if img_cv is None:
                        continue

                    h, w = img_cv.shape[:2]
                    record.width = w
                    record.height = h

                    # Dimension check
                    if w < cfg["min_dimension_px"] or h < cfg["min_dimension_px"]:
                        record.action = Action.TRASH
                        record.reason = Reason.TOO_SMALL_DIM
                        record.step_decided = 3
                        decided += 1
                        continue

                    # Brightness check
                    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
                    avg_brightness = gray.mean()
                    record.brightness = avg_brightness

                    if avg_brightness < cfg["darkness_threshold"]:
                        record.action = Action.TRASH
                        record.reason = Reason.TOO_DARK
                        record.step_decided = 3
                        decided += 1
                        continue

                    if avg_brightness > cfg["brightness_threshold"]:
                        record.action = Action.REVIEW
                        record.reason = Reason.OVEREXPOSED
                        record.step_decided = 3
                        decided += 1
                        continue

                    # Blur check
                    blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
                    record.blur_score = blur_score

                    if blur_score < cfg["blur_threshold"]:
                        record.action = Action.REVIEW
                        record.reason = Reason.BLURRY
                        record.step_decided = 3
                        decided += 1

                except Exception:
                    pass

        self.stats["step3_decided"] = decided
        self.console.print(f"  Classified [bold]{decided:,}[/bold] images by quality\n")

    # --------------------------------------------------------
    # Step 4: Vision model classification (Qwen3-VL via Ollama)
    # --------------------------------------------------------
    def step4_vision(self):
        """Classify remaining ambiguous images using Qwen3-VL-8B locally."""
        self.console.print("[bold cyan]Step 4:[/bold cyan] Vision classification (Qwen3-VL-8B)")
        cfg = self.config["vision"]

        pending = [r for r in self.records if r.action == Action.KEEP]

        if not pending:
            self.console.print("  No images need vision classification!\n")
            return

        self.console.print(f"  Sending [bold]{len(pending):,}[/bold] images to Qwen3-VL-8B...")

        # Check Ollama connectivity
        try:
            resp = httpx.get(f"{cfg['ollama_url']}/api/tags", timeout=5)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            model_available = any(cfg["model"] in m for m in models)
            if not model_available:
                self.console.print(
                    f"[yellow]  Model '{cfg['model']}' not found in Ollama.[/yellow]\n"
                    f"  Run: [bold]ollama pull {cfg['model']}[/bold]\n"
                )
                return
        except Exception as e:
            self.console.print(
                f"[yellow]  Cannot connect to Ollama at {cfg['ollama_url']}[/yellow]\n"
                f"  Make sure Ollama is running: [bold]ollama serve[/bold]\n"
                f"  Error: {e}\n"
            )
            return

        decided = 0

        # Build the classification prompt
        think_prefix = "/think" if cfg["use_thinking"] else "/no_think"
        system_prompt = f"""{think_prefix}
You are an image classifier for a photo library cleanup tool.
Classify the image into EXACTLY ONE of these categories and respond with ONLY a JSON object:

- "photo": A legitimate personal photograph (people, places, events, nature, etc.)
- "screenshot": A screen capture from a phone or computer
- "meme": An internet meme, sticker, viral image, or image with overlaid text meant to be funny/shared
- "document": A photographed document (receipt, invoice, ID, ticket, menu, handwritten note, etc.)
- "accidental": An accidental photo (black, blurry pocket shot, floor, extremely dark/bright, finger over lens)

Respond ONLY with this JSON format, nothing else:
{{"category": "photo|screenshot|meme|document|accidental", "confidence": 0.0-1.0}}"""

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(), console=self.console,
        ) as progress:
            task = progress.add_task("Classifying with vision model...", total=len(pending))

            for record in pending:
                progress.advance(task)

                try:
                    # Resize image for faster inference
                    with Image.open(record.path) as img:
                        img.thumbnail((cfg["max_image_size"], cfg["max_image_size"]))
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=80)
                        img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

                    # Call Ollama API
                    payload = {
                        "model": cfg["model"],
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {
                                "role": "user",
                                "content": "Classify this image.",
                                "images": [img_b64],
                            },
                        ],
                        "stream": False,
                        "options": {
                            "temperature": 0.1,
                            "num_predict": 100,
                        },
                    }

                    resp = httpx.post(
                        f"{cfg['ollama_url']}/api/chat",
                        json=payload,
                        timeout=30,
                    )
                    resp.raise_for_status()
                    result_text = resp.json()["message"]["content"]

                    # Parse JSON response (handle thinking tags if present)
                    json_text = result_text
                    if "</think>" in json_text:
                        json_text = json_text.split("</think>")[-1]
                    json_text = json_text.strip()

                    # Extract JSON from potential markdown code blocks
                    if "```" in json_text:
                        json_text = json_text.split("```")[1]
                        if json_text.startswith("json"):
                            json_text = json_text[4:]
                        json_text = json_text.strip()

                    result = json.loads(json_text)
                    category = result.get("category", "photo")
                    confidence = float(result.get("confidence", 0.5))

                    record.vision_label = category
                    record.vision_confidence = confidence
                    record.confidence = confidence

                    # Map category to action
                    if confidence < cfg["confidence_threshold"]:
                        record.action = Action.REVIEW
                        record.reason = Reason.VISION_AMBIGUOUS
                        record.step_decided = 4
                        decided += 1
                    elif category == "screenshot":
                        record.action = Action.TRASH
                        record.reason = Reason.VISION_SCREENSHOT
                        record.step_decided = 4
                        decided += 1
                    elif category == "meme":
                        record.action = Action.TRASH
                        record.reason = Reason.VISION_MEME
                        record.step_decided = 4
                        decided += 1
                    elif category == "document":
                        record.action = Action.DOCUMENTS
                        record.reason = Reason.VISION_DOCUMENT
                        record.step_decided = 4
                        decided += 1
                    elif category == "accidental":
                        record.action = Action.TRASH
                        record.reason = Reason.VISION_ACCIDENTAL
                        record.step_decided = 4
                        decided += 1
                    # else: "photo" → stays KEEP

                except json.JSONDecodeError:
                    # Model didn't return valid JSON → review
                    record.action = Action.REVIEW
                    record.reason = Reason.VISION_AMBIGUOUS
                    record.step_decided = 4
                    decided += 1
                except Exception as e:
                    # Log but don't fail the whole pipeline
                    self.console.print(
                        f"  [yellow]Warning: Failed to classify {record.filename}: {e}[/yellow]"
                    )

        self.stats["step4_decided"] = decided
        self.console.print(f"  Classified [bold]{decided:,}[/bold] images with vision model\n")

    # --------------------------------------------------------
    # Step 5: Execute moves
    # --------------------------------------------------------
    def step5_execute(self):
        """Move files to staging directories based on classification."""
        self.console.print("[bold cyan]Step 5:[/bold cyan] Executing file operations")

        if self.dry_run:
            self.console.print("  [yellow]DRY RUN mode - no files will be moved.[/yellow]")
            self.console.print("  [yellow]Run with --execute to actually move files.[/yellow]\n")

        staging = self.config["staging"]
        action_dirs = {
            Action.TRASH: staging["trash"],
            Action.REVIEW: staging["review"],
            Action.DOCUMENTS: staging["documents"],
        }

        # Create staging directories
        for d in action_dirs.values():
            Path(d).mkdir(parents=True, exist_ok=True)

        moved = defaultdict(int)
        errors = 0

        to_move = [r for r in self.records if r.action != Action.KEEP]

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(), console=self.console,
        ) as progress:
            task = progress.add_task("Moving files...", total=len(to_move))

            for record in to_move:
                progress.advance(task)
                self._log_action(record)

                if self.dry_run:
                    moved[record.action] += 1
                    continue

                target_dir = action_dirs.get(record.action)
                if not target_dir:
                    continue

                src = Path(record.path)
                # Preserve some directory structure (year/month)
                try:
                    mtime = os.path.getmtime(record.path)
                    dt = datetime.fromtimestamp(mtime)
                    sub_dir = f"{dt.year}/{dt.month:02d}"
                except Exception:
                    sub_dir = "unknown"

                dst_dir = Path(target_dir) / sub_dir
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst = dst_dir / src.name

                # Handle name conflicts
                if dst.exists():
                    stem = dst.stem
                    suffix = dst.suffix
                    counter = 1
                    while dst.exists():
                        dst = dst_dir / f"{stem}_{counter}{suffix}"
                        counter += 1

                try:
                    src.rename(dst)
                    moved[record.action] += 1
                except OSError as e:
                    # Cross-device? Try copy + delete
                    try:
                        import shutil
                        shutil.move(str(src), str(dst))
                        moved[record.action] += 1
                    except Exception:
                        errors += 1

        self.stats["moved_trash"] = moved[Action.TRASH]
        self.stats["moved_review"] = moved[Action.REVIEW]
        self.stats["moved_documents"] = moved[Action.DOCUMENTS]
        self.stats["move_errors"] = errors

        prefix = "Would move" if self.dry_run else "Moved"
        self.console.print(f"  {prefix} [red]{moved[Action.TRASH]:,}[/red] to trash")
        self.console.print(f"  {prefix} [yellow]{moved[Action.REVIEW]:,}[/yellow] to review")
        self.console.print(f"  {prefix} [blue]{moved[Action.DOCUMENTS]:,}[/blue] to documents")
        if errors:
            self.console.print(f"  [red]{errors} errors during move[/red]")
        self.console.print()

    # --------------------------------------------------------
    # Summary & reporting
    # --------------------------------------------------------
    def print_summary(self):
        """Print a beautiful summary of the cleanup results."""
        total = self.stats.get("total_files", 0)
        keep_count = sum(1 for r in self.records if r.action == Action.KEEP)
        trash_count = sum(1 for r in self.records if r.action == Action.TRASH)
        review_count = sum(1 for r in self.records if r.action == Action.REVIEW)
        doc_count = sum(1 for r in self.records if r.action == Action.DOCUMENTS)

        # Space estimation
        trash_bytes = sum(r.size_bytes for r in self.records if r.action == Action.TRASH)
        review_bytes = sum(r.size_bytes for r in self.records if r.action == Action.REVIEW)

        table = Table(title="Cleanup Summary", show_header=True, header_style="bold")
        table.add_column("Category", style="bold")
        table.add_column("Count", justify="right")
        table.add_column("% of Total", justify="right")
        table.add_column("Size", justify="right")

        def fmt_size(b):
            if b >= 1e9:
                return f"{b / 1e9:.1f} GB"
            return f"{b / 1e6:.1f} MB"

        table.add_row(
            "[green]Keep[/green]", f"{keep_count:,}",
            f"{keep_count * 100 / max(total, 1):.1f}%",
            fmt_size(sum(r.size_bytes for r in self.records if r.action == Action.KEEP)),
        )
        table.add_row(
            "[red]Trash[/red]", f"{trash_count:,}",
            f"{trash_count * 100 / max(total, 1):.1f}%",
            fmt_size(trash_bytes),
        )
        table.add_row(
            "[yellow]Review[/yellow]", f"{review_count:,}",
            f"{review_count * 100 / max(total, 1):.1f}%",
            fmt_size(review_bytes),
        )
        table.add_row(
            "[blue]Documents[/blue]", f"{doc_count:,}",
            f"{doc_count * 100 / max(total, 1):.1f}%",
            fmt_size(sum(r.size_bytes for r in self.records if r.action == Action.DOCUMENTS)),
        )

        self.console.print()
        self.console.print(table)

        if trash_bytes > 0:
            self.console.print(
                f"\n  [bold green]Potential space savings:[/bold green] "
                f"{fmt_size(trash_bytes)} (trash) + "
                f"{fmt_size(review_bytes)} (if review confirmed)"
            )

        # Breakdown by reason
        reason_table = Table(title="\nBreakdown by Reason", show_header=True, header_style="bold")
        reason_table.add_column("Reason")
        reason_table.add_column("Count", justify="right")
        reason_table.add_column("Action")

        reason_counts = defaultdict(int)
        reason_action = {}
        for r in self.records:
            if r.action != Action.KEEP:
                reason_counts[r.reason] += 1
                reason_action[r.reason] = r.action

        for reason, count in sorted(reason_counts.items(), key=lambda x: x[1], reverse=True):
            action = reason_action[reason]
            action_style = {
                Action.TRASH: "[red]trash[/red]",
                Action.REVIEW: "[yellow]review[/yellow]",
                Action.DOCUMENTS: "[blue]documents[/blue]",
            }.get(action, str(action.value))
            reason_table.add_row(reason.value, f"{count:,}", action_style)

        self.console.print(reason_table)

    def save_checkpoint(self, step: int):
        """Save progress so pipeline can be resumed."""
        data = {
            "step": step,
            "timestamp": datetime.now().isoformat(),
            "records": [asdict(r) for r in self.records],
        }
        # Convert enums for serialization
        for rec in data["records"]:
            rec["action"] = rec["action"].value if isinstance(rec["action"], Action) else rec["action"]
            rec["reason"] = rec["reason"].value if isinstance(rec["reason"], Reason) else rec["reason"]

        with open(self.checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def load_checkpoint(self) -> int:
        """Load progress from checkpoint. Returns last completed step."""
        if not self.checkpoint_file.exists():
            return 0
        with open(self.checkpoint_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.records = []
        for rec_dict in data["records"]:
            rec_dict["action"] = Action(rec_dict["action"])
            rec_dict["reason"] = Reason(rec_dict["reason"])
            self.records.append(PhotoRecord(**rec_dict))

        step = data["step"]
        self.console.print(f"[green]Resumed from checkpoint (step {step} completed)[/green]")
        return step

    # --------------------------------------------------------
    # Main runner
    # --------------------------------------------------------
    def run(self, steps: Optional[tuple[int, int]] = None, resume: bool = False):
        start_step = 0

        if resume:
            start_step = self.load_checkpoint()

        step_range = steps or (1, 5)

        banner = Panel(
            "[bold]NAS Photo Cleanup Pipeline[/bold]\n"
            f"Mode: {'[yellow]DRY RUN[/yellow]' if self.dry_run else '[red]EXECUTE[/red]'}\n"
            f"Steps: {step_range[0]}-{step_range[1]}\n"
            f"Source: {self.config['source_dir']}",
            title="Photo Cleanup", border_style="cyan",
        )
        self.console.print(banner)

        t0 = time.time()

        # Scan (always needed)
        if not self.records:
            self.scan_files()

        if step_range[0] <= 1 <= step_range[1] and start_step < 1:
            self.step1_metadata_filter()
            self.save_checkpoint(1)

        if step_range[0] <= 2 <= step_range[1] and start_step < 2:
            self.step2_dedup()
            self.save_checkpoint(2)

        if step_range[0] <= 3 <= step_range[1] and start_step < 3:
            self.step3_quality()
            self.save_checkpoint(3)

        if step_range[0] <= 4 <= step_range[1] and start_step < 4:
            self.step4_vision()
            self.save_checkpoint(4)

        if step_range[0] <= 5 <= step_range[1]:
            self.step5_execute()

        elapsed = time.time() - t0
        self.console.print(f"[dim]Total time: {elapsed:.1f}s[/dim]")

        self.print_summary()
        self.log_file.close()


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="NAS Photo Cleanup Pipeline - Hybrid intelligent approach"
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually move files (default is dry-run)"
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to config file (default: config.yaml)"
    )
    parser.add_argument(
        "--step", default=None,
        help="Run specific step(s): '1', '1-3', '4', etc."
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from last checkpoint"
    )
    args = parser.parse_args()

    pipeline = CleanupPipeline(config_path=args.config)

    if args.execute:
        pipeline.dry_run = False

    steps = None
    if args.step:
        if "-" in args.step:
            parts = args.step.split("-")
            steps = (int(parts[0]), int(parts[1]))
        else:
            s = int(args.step)
            steps = (s, s)

    pipeline.run(steps=steps, resume=args.resume)


if __name__ == "__main__":
    main()
