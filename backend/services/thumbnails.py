"""Thumbnail generation for review UI."""

import hashlib
import os
from pathlib import Path

from PIL import Image

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass


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
