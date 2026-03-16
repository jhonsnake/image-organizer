"""
Stages 1-3 of the pipeline: Metadata, Hash Dedup, Quality Analysis.
Adapted from cleanup.py with improvements.
"""

import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

import cv2
import imagehash
import numpy as np
from PIL import Image, ExifTags

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

from models import PhotoAction, PhotoReason

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
    ".tiff", ".tif", ".heic", ".heif",
}

# ── Screenshot detection ──

SCREEN_RATIOS = [
    (9, 16), (9, 18), (9, 18.5), (9, 19), (9, 19.5),
    (9, 20), (9, 21), (3, 4), (10, 16), (16, 9), (16, 10),
]

SCREENSHOT_FILENAME_RE = [
    re.compile(r"^Screenshot[\s_-]", re.IGNORECASE),
    re.compile(r"^Captura[\s_-]", re.IGNORECASE),
    re.compile(r"^Screen[\s_]Shot", re.IGNORECASE),
    re.compile(r"^Pantallazo", re.IGNORECASE),
    re.compile(r"^Pantalla[\s_-]", re.IGNORECASE),
    re.compile(r"^scr_\d+", re.IGNORECASE),
]

SOCIAL_MEDIA_RE = [
    re.compile(r"^IMG-\d{8}-WA\d+", re.IGNORECASE),
    re.compile(r"^VID-\d{8}-WA\d+", re.IGNORECASE),
    re.compile(r"^STK-\d{8}-WA\d+", re.IGNORECASE),
    re.compile(r"^FB_IMG_\d+", re.IGNORECASE),
    re.compile(r"^received_\d+", re.IGNORECASE),
    re.compile(r"^signal-\d{4}-\d{2}-\d{2}", re.IGNORECASE),
    re.compile(r"^telegram-cloud-photo", re.IGNORECASE),
    re.compile(r"^Snapchat-\d+", re.IGNORECASE),
]

MEME_RE = [
    re.compile(r"^meme", re.IGNORECASE),
    re.compile(r"^sticker", re.IGNORECASE),
]


def scan_directory(source_dir: str) -> list[dict]:
    """Walk source directory and return list of file info dicts."""
    files = []
    source = Path(source_dir)
    for root, dirs, filenames in os.walk(source):
        # Skip cleanup and Synology thumbnail dirs
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d != "@eaDir" and d != "_cleanup"
        ]
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            if ext in IMAGE_EXTENSIONS:
                fpath = os.path.join(root, fname)
                try:
                    stat = os.stat(fpath)
                    files.append({
                        "path": fpath,
                        "filename": fname,
                        "extension": ext,
                        "size_bytes": stat.st_size,
                    })
                except OSError:
                    pass
    return files


# ── Stage 1: Metadata ──

def classify_metadata(photo: dict, min_file_size: int = 15000) -> Optional[tuple[PhotoAction, PhotoReason, float]]:
    """Try to classify based on filename, EXIF, dimensions. Returns (action, reason, confidence) or None."""
    fname = Path(photo["filename"]).stem

    # Too small file
    if photo["size_bytes"] < min_file_size:
        return PhotoAction.TRASH, PhotoReason.SMALL_FILE, 0.90

    # Screenshot filename
    for pattern in SCREENSHOT_FILENAME_RE:
        if pattern.search(fname):
            return PhotoAction.TRASH, PhotoReason.SCREENSHOT_FILENAME, 0.95

    # Social media / messaging
    for pattern in SOCIAL_MEDIA_RE:
        if pattern.search(fname):
            return PhotoAction.TRASH, PhotoReason.MESSAGING_IMAGE, 0.85

    # Meme/sticker filename
    for pattern in MEME_RE:
        if pattern.search(fname):
            return PhotoAction.TRASH, PhotoReason.TINY_IMAGE, 0.80

    # Open image for dimensions + EXIF
    try:
        with Image.open(photo["path"]) as img:
            w, h = img.size
            photo["width"] = w
            photo["height"] = h

            # Very small images (stickers, icons)
            if w < 200 and h < 200:
                return PhotoAction.TRASH, PhotoReason.TINY_IMAGE, 0.85

            # Check EXIF
            has_camera = False
            camera_make = None
            date_taken = None
            try:
                exif = img.getexif()
                if exif:
                    make = exif.get(ExifTags.Base.Make, "")
                    model = exif.get(ExifTags.Base.Model, "")
                    camera_make = f"{make} {model}".strip() or None
                    has_camera = bool(make or model)
                    date_taken = exif.get(ExifTags.Base.DateTimeOriginal, "")
            except Exception:
                pass

            photo["has_camera_exif"] = has_camera
            photo["camera_make"] = camera_make
            photo["date_taken"] = date_taken or None

            # Screenshot by dimensions + no camera EXIF
            if not has_camera and _is_screen_ratio(w, h):
                return PhotoAction.TRASH, PhotoReason.SCREENSHOT_DIMS_NO_EXIF, 0.88

    except Exception:
        pass

    return None


PHONE_SCREEN_WIDTHS = {
    720, 750, 828, 1080, 1125, 1170, 1179, 1242,
    1284, 1290, 1440, 1536, 1920, 2160, 2532, 2556,
    2688, 2778, 2796,
}


def _is_screen_ratio(w: int, h: int) -> bool:
    """Check if dimensions match a known screen resolution.
    Requires ratio match AND at least one dimension matching a known screen width.
    Photos with both dims > 2500px are likely camera shots, not screenshots.
    """
    short, long = min(w, h), max(w, h)
    if short == 0:
        return False

    # Large photos in both dimensions are almost certainly camera shots
    if short > 2500:
        return False

    # At least one dimension must match a known screen width
    if w not in PHONE_SCREEN_WIDTHS and h not in PHONE_SCREEN_WIDTHS:
        return False

    actual = long / short
    for rw, rh in SCREEN_RATIOS:
        expected = rh / rw
        if abs(actual - expected) < 0.05:
            return True
    return False


# ── Stage 2: Hash Dedup ──

def compute_phash(filepath: str) -> Optional[str]:
    try:
        with Image.open(filepath) as img:
            img.thumbnail((256, 256))
            h = imagehash.phash(img)
            return str(h)
    except Exception:
        return None


def find_duplicate_groups(
    photos: list[dict], threshold: int = 8
) -> list[list[int]]:
    """
    Find groups of duplicate photos by pHash.
    Returns list of groups, each group is a list of indices into the photos list.
    The first element of each group is the "best" (sharpest) to keep.
    """
    hashes: list[tuple[int, imagehash.ImageHash]] = []
    for i, p in enumerate(photos):
        h_str = p.get("phash")
        if h_str:
            hashes.append((i, imagehash.hex_to_hash(h_str)))

    used = set()
    groups = []

    for idx, (i, h1) in enumerate(hashes):
        if i in used:
            continue
        group = [i]
        for jdx in range(idx + 1, len(hashes)):
            j, h2 = hashes[jdx]
            if j in used:
                continue
            if h1 - h2 <= threshold:
                group.append(j)
                used.add(j)

        if len(group) >= 2:
            used.add(i)
            # Sort by sharpness (blur score) descending — best first
            scored = []
            for gi in group:
                try:
                    img_cv = cv2.imread(photos[gi]["path"], cv2.IMREAD_GRAYSCALE)
                    blur = cv2.Laplacian(img_cv, cv2.CV_64F).var() if img_cv is not None else 0
                except Exception:
                    blur = 0
                scored.append((gi, blur))
            scored.sort(key=lambda x: x[1], reverse=True)
            groups.append([s[0] for s in scored])

    return groups


# ── Stage 3: Quality ──

def analyze_quality(
    filepath: str,
    blur_threshold: float = 50.0,
    darkness_threshold: float = 15.0,
    brightness_threshold: float = 245.0,
    min_dimension: int = 100,
) -> Optional[tuple[PhotoAction, PhotoReason, float, dict]]:
    """
    Analyze image quality. Returns (action, reason, confidence, extra_data) or None.
    extra_data contains blur_score, brightness, width, height.
    """
    try:
        img_cv = cv2.imread(filepath)
        if img_cv is None:
            return None

        h, w = img_cv.shape[:2]
        extra = {"width": w, "height": h, "blur_score": 0.0, "brightness": 128.0}

        # Too small dimensions
        if w < min_dimension or h < min_dimension:
            return PhotoAction.TRASH, PhotoReason.TINY_IMAGE, 0.90, extra

        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        avg_brightness = float(gray.mean())
        extra["brightness"] = avg_brightness

        # Too dark
        if avg_brightness < darkness_threshold:
            return PhotoAction.TRASH, PhotoReason.TOO_DARK, 0.90, extra

        # Overexposed
        if avg_brightness > brightness_threshold:
            return PhotoAction.REVIEW, PhotoReason.OVEREXPOSED, 0.80, extra

        # Blur
        blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        extra["blur_score"] = blur_score

        if blur_score < blur_threshold:
            return PhotoAction.REVIEW, PhotoReason.BLURRY, 0.70, extra

    except Exception:
        return None

    return None
