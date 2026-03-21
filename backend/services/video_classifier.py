"""
Video classifier — classifies videos by metadata using ffprobe.
No LLM needed. Uses filename patterns, path, duration, resolution, and size.
"""

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

from models import PhotoAction, PhotoReason

logger = logging.getLogger(__name__)

WHATSAPP_VID_RE = re.compile(r"^VID-\d{8}-WA\d+", re.IGNORECASE)

# Common phone screen recording resolutions (portrait)
SCREEN_WIDTHS = {720, 750, 828, 1080, 1170, 1179, 1242, 1284, 1290, 1440, 1920}


def _probe_video(filepath: str) -> Optional[dict]:
    """Extract video metadata using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", filepath,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"ffprobe failed for {filepath}: {e}")
        return None


def classify_video(
    filepath: str, filename: str, size_bytes: int
) -> Optional[tuple[PhotoAction, PhotoReason, float, dict]]:
    """
    Classify a video by metadata. Returns (action, reason, confidence, meta) or None.
    meta contains: duration, width, height, codec.
    """
    fname = Path(filename).stem
    meta = {"duration": None, "width": 0, "height": 0, "codec": None}

    # Path-based rules (no ffprobe needed)
    if ".Statuses" in filepath or "/.Statuses/" in filepath:
        return PhotoAction.TRASH, PhotoReason.WHATSAPP_STATUS, 0.95, meta

    # Probe video metadata
    probe = _probe_video(filepath)
    if probe:
        # Extract video stream info
        for stream in probe.get("streams", []):
            if stream.get("codec_type") == "video":
                meta["width"] = int(stream.get("width", 0))
                meta["height"] = int(stream.get("height", 0))
                meta["codec"] = stream.get("codec_name")
                # Duration from stream or format
                dur = stream.get("duration")
                if not dur:
                    dur = probe.get("format", {}).get("duration")
                if dur:
                    meta["duration"] = float(dur)
                break

    # WhatsApp video pattern
    if WHATSAPP_VID_RE.search(fname):
        return PhotoAction.TRASH, PhotoReason.MESSAGING_IMAGE, 0.85, meta

    # Very short video (< 3s) — likely accidental or a boomerang
    if meta["duration"] is not None and meta["duration"] < 3:
        return PhotoAction.REVIEW, PhotoReason.VISION_ACCIDENTAL, 0.70, meta

    # Large WhatsApp-pattern video (> 500MB)
    if size_bytes > 500 * 1024 * 1024 and WHATSAPP_VID_RE.search(fname):
        return PhotoAction.REVIEW, PhotoReason.MESSAGING_IMAGE, 0.75, meta

    # Screen recording heuristic: phone screen width, h264, short, no EXIF camera
    if meta["duration"] is not None and meta["duration"] < 120:
        w, h = meta["width"], meta["height"]
        short_side = min(w, h) if w and h else 0
        if short_side in SCREEN_WIDTHS and meta["codec"] in ("h264", "hevc", "h265"):
            return PhotoAction.REVIEW, PhotoReason.VISION_SCREENSHOT, 0.75, meta

    return None
