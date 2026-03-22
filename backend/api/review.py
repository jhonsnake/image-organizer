"""Review endpoints — browse, reclassify, and batch-update review photos."""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import Photo, PhotoAction, PhotoReason, VisionProviderConfig, Job, get_db
from services.thumbnails import generate_thumbnail
from services.vision import create_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review", tags=["review"])


class PhotoResponse(BaseModel):
    id: int
    job_id: int
    path: str
    filename: str
    extension: Optional[str]
    size_bytes: int
    width: int
    height: int
    action: str
    reason: str
    confidence: float
    stage_decided: int
    vision_label: Optional[str]
    vision_confidence: float
    blur_score: float
    brightness: float
    duplicate_group: Optional[str]
    thumbnail_path: Optional[str]

    class Config:
        from_attributes = True


class ReclassifyRequest(BaseModel):
    action: str  # "keep" | "trash" | "documents"


class BatchReclassifyRequest(BaseModel):
    photo_ids: list[int]
    action: str  # "keep" | "trash" | "documents"


@router.get("/{job_id}/photos", response_model=list[PhotoResponse])
async def list_review_photos(
    job_id: int,
    min_confidence: float = 0.0,
    max_confidence: float = 1.0,
    reason: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List photos in review for a job, with optional filters."""
    query = (
        select(Photo)
        .where(
            Photo.job_id == job_id,
            Photo.action == PhotoAction.REVIEW,
            Photo.confidence >= min_confidence,
            Photo.confidence <= max_confidence,
        )
        .order_by(Photo.confidence.asc())  # Least confident first
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    if reason:
        query = query.where(Photo.reason == reason)

    result = await db.execute(query)
    photos = list(result.scalars().all())

    # Ensure thumbnails exist
    for photo in photos:
        if not photo.thumbnail_path:
            thumb = generate_thumbnail(
                photo.path, settings.thumbnail_dir, settings.thumbnail_size
            )
            if thumb:
                photo.thumbnail_path = thumb
                await db.flush()

    await db.commit()
    return photos


@router.get("/{job_id}/photos/count")
async def count_review_photos(job_id: int, db: AsyncSession = Depends(get_db)):
    """Count photos in review."""
    result = await db.execute(
        select(func.count(Photo.id)).where(
            Photo.job_id == job_id,
            Photo.action == PhotoAction.REVIEW,
        )
    )
    return {"count": result.scalar()}


@router.put("/photo/{photo_id}", response_model=PhotoResponse)
async def reclassify_photo(
    photo_id: int, req: ReclassifyRequest, db: AsyncSession = Depends(get_db),
):
    """Reclassify a single photo."""
    photo = await db.get(Photo, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    action_map = {
        "keep": (PhotoAction.KEEP, PhotoReason.MANUAL_KEEP),
        "trash": (PhotoAction.TRASH, PhotoReason.MANUAL_TRASH),
        "documents": (PhotoAction.DOCUMENTS, PhotoReason.MANUAL_DOCUMENTS),
    }

    if req.action not in action_map:
        raise HTTPException(status_code=400, detail=f"Invalid action: {req.action}")

    action, reason = action_map[req.action]
    photo.action = action
    photo.reason = reason
    photo.confidence = 1.0  # Manual = full confidence

    await db.commit()
    await db.refresh(photo)
    return photo


@router.put("/batch", response_model=dict)
async def batch_reclassify(
    req: BatchReclassifyRequest, db: AsyncSession = Depends(get_db),
):
    """Reclassify multiple photos at once."""
    action_map = {
        "keep": (PhotoAction.KEEP, PhotoReason.MANUAL_KEEP),
        "trash": (PhotoAction.TRASH, PhotoReason.MANUAL_TRASH),
        "documents": (PhotoAction.DOCUMENTS, PhotoReason.MANUAL_DOCUMENTS),
    }

    if req.action not in action_map:
        raise HTTPException(status_code=400, detail=f"Invalid action: {req.action}")

    action, reason = action_map[req.action]

    result = await db.execute(
        select(Photo).where(Photo.id.in_(req.photo_ids))
    )
    photos = list(result.scalars().all())

    updated = 0
    for photo in photos:
        photo.action = action
        photo.reason = reason
        photo.confidence = 1.0
        updated += 1

    await db.commit()
    return {"updated": updated}


class AiReclassifyRequest(BaseModel):
    photo_ids: Optional[list[int]] = None  # None = all review photos
    confidence_threshold: float = 0.7


class AiReclassifyResult(BaseModel):
    total: int
    classified: int
    kept: int
    trashed: int
    documents: int
    still_review: int
    provider_used: str


@router.post("/{job_id}/reclassify-ai", response_model=AiReclassifyResult)
async def reclassify_with_ai(
    job_id: int,
    req: AiReclassifyRequest = AiReclassifyRequest(),
    db: AsyncSession = Depends(get_db),
):
    """Re-run AI vision classification on review photos from a completed job."""
    # Verify job exists
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")

    # Find an available provider
    prov_result = await db.execute(
        select(VisionProviderConfig)
        .where(VisionProviderConfig.enabled == True)
        .order_by(VisionProviderConfig.priority)
    )
    prov_configs = list(prov_result.scalars().all())

    provider = None
    provider_label = ""
    for pc in prov_configs:
        candidate = create_provider(
            provider_type=pc.provider_type,
            base_url=pc.base_url,
            model=pc.model,
            api_key=pc.api_key,
        )
        if await candidate.is_available():
            provider = candidate
            provider_label = f"{pc.name} ({pc.model or pc.provider_type})"
            break
        else:
            await candidate.close()

    if not provider:
        raise HTTPException(
            status_code=503,
            detail="Ningun provider de vision disponible. Configura uno en la seccion de Providers.",
        )

    # Get review photos to reclassify
    query = select(Photo).where(
        Photo.job_id == job_id,
        Photo.action == PhotoAction.REVIEW,
    )
    if req.photo_ids:
        query = query.where(Photo.id.in_(req.photo_ids))

    result = await db.execute(query)
    photos = list(result.scalars().all())

    if not photos:
        await provider.close()
        raise HTTPException(status_code=404, detail="No hay fotos en review para reclasificar")

    confidence_threshold = req.confidence_threshold
    classified = 0
    kept = 0
    trashed = 0
    documents = 0
    still_review = 0

    try:
        for photo in photos:
            # Only classify images, not videos
            if photo.media_type == "video":
                still_review += 1
                continue

            classification = await provider.classify(
                photo.path,
                max_size=settings.default_max_image_size,
            )

            if classification:
                cat = classification["category"]
                conf = classification["confidence"]
                photo.vision_label = cat
                photo.vision_confidence = conf
                photo.confidence = conf
                photo.stage_decided = 4

                if conf < confidence_threshold:
                    photo.action = PhotoAction.REVIEW
                    photo.reason = PhotoReason.VISION_AMBIGUOUS
                    still_review += 1
                elif cat == "screenshot":
                    photo.action = PhotoAction.TRASH
                    photo.reason = PhotoReason.VISION_SCREENSHOT
                    trashed += 1
                elif cat == "meme":
                    photo.action = PhotoAction.TRASH
                    photo.reason = PhotoReason.VISION_MEME
                    trashed += 1
                elif cat == "invoice":
                    photo.action = PhotoAction.DOCUMENTS
                    photo.reason = PhotoReason.VISION_INVOICE
                    documents += 1
                elif cat == "document":
                    photo.action = PhotoAction.DOCUMENTS
                    photo.reason = PhotoReason.VISION_DOCUMENT
                    documents += 1
                elif cat == "accidental":
                    photo.action = PhotoAction.TRASH
                    photo.reason = PhotoReason.VISION_ACCIDENTAL
                    trashed += 1
                elif cat == "photo":
                    photo.action = PhotoAction.KEEP
                    photo.reason = PhotoReason.VISION_PHOTO
                    kept += 1

                classified += 1
            else:
                still_review += 1

        await db.commit()
    finally:
        await provider.close()

    return AiReclassifyResult(
        total=len(photos),
        classified=classified,
        kept=kept,
        trashed=trashed,
        documents=documents,
        still_review=still_review,
        provider_used=provider_label,
    )


@router.get("/thumbnail/{filename}")
async def get_thumbnail(filename: str):
    """Serve a thumbnail image."""
    import os
    thumb_path = os.path.join(settings.thumbnail_dir, filename)
    if not os.path.exists(thumb_path):
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(thumb_path, media_type="image/jpeg")


@router.get("/full/{photo_id}")
async def get_full_image(photo_id: int, db: AsyncSession = Depends(get_db)):
    """Serve the full-resolution image (for lightbox zoom)."""
    photo = await db.get(Photo, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    import os
    if not os.path.exists(photo.path):
        raise HTTPException(status_code=404, detail="Image file not found on disk")

    # Determine media type
    ext = photo.extension or ".jpg"
    media_types = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
        ".heic": "image/heic", ".heif": "image/heif",
    }
    media_type = media_types.get(ext.lower(), "image/jpeg")

    return FileResponse(photo.path, media_type=media_type)
