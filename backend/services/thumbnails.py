"""Thumbnail generation for review UI (images + videos)."""

import hashlib
import logging
import os
import subprocess
from pathlib import Path

from PIL import Image

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

logger = logging.getLogger(__name__)


def generate_thumbnail(
    image_path: str, output_dir: str, size: int = 300
) -> str | None:
    """
    Generate a JPEG thumbnail. Returns the thumbnail filename or None.
    Thumbnails are named by hash to avoid collisions.
    """
    try:
        # Create deterministic filename from source path
        path_hash = hashlib.md5(image_path.encode()).hexdigest()
        thumb_name = f"{path_hash}.jpg"
        thumb_path = os.path.join(output_dir, thumb_name)

        if os.path.exists(thumb_path):
            return thumb_name

        os.makedirs(output_dir, exist_ok=True)

        with Image.open(image_path) as img:
            img.thumbnail((size, size))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(thumb_path, format="JPEG", quality=80)

        return thumb_name

    except Exception:
        return None


def generate_video_thumbnail(
    video_path: str, output_dir: str, size: int = 300
) -> str | None:
    """
    Extract a frame at 1s from a video using ffmpeg and save as JPEG thumbnail.
    Returns the thumbnail filename or None.
    """
    try:
        path_hash = hashlib.md5(video_path.encode()).hexdigest()
        thumb_name = f"{path_hash}.jpg"
        thumb_path = os.path.join(output_dir, thumb_name)

        if os.path.exists(thumb_path):
            return thumb_name

        os.makedirs(output_dir, exist_ok=True)

        result = subprocess.run(
            [
                "ffmpeg", "-i", video_path,
                "-ss", "1", "-vframes", "1",
                "-vf", f"scale={size}:-1",
                "-y", thumb_path,
            ],
            capture_output=True, timeout=15,
        )

        if result.returncode != 0 or not os.path.exists(thumb_path):
            # Try at 0s if 1s fails (very short video)
            subprocess.run(
                [
                    "ffmpeg", "-i", video_path,
                    "-ss", "0", "-vframes", "1",
                    "-vf", f"scale={size}:-1",
                    "-y", thumb_path,
                ],
                capture_output=True, timeout=15,
            )

        if os.path.exists(thumb_path):
            return thumb_name
        return None

    except Exception as e:
        logger.warning(f"Video thumbnail failed for {video_path}: {e}")
        return None
