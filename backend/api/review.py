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


class BatchByReasonRequest(BaseModel):
    job_id: int
    reason: str
    new_action: str  # "keep" | "trash" | "documents" | "review"


@router.put("/batch-by-reason", response_model=dict)
async def batch_by_reason(req: BatchByReasonRequest, db: AsyncSession = Depends(get_db)):
    """Reclassify all photos with a given reason in a job."""
    # For trash/documents: keep original reason so execute-group can find them by reason
    # For keep: mark as manual so they're excluded from future summaries
    action_map = {
        "keep": (PhotoAction.KEEP, PhotoReason.MANUAL_KEEP),
        "trash": (PhotoAction.TRASH, None),       # keep original reason
        "documents": (PhotoAction.DOCUMENTS, None),  # keep original reason
        "review": (PhotoAction.REVIEW, None),      # keep original reason
    }
    if req.new_action not in action_map:
        raise HTTPException(status_code=400, detail=f"Invalid action: {req.new_action}")

    try:
        target_reason = PhotoReason(req.reason)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid reason: {req.reason}")

    new_action, new_reason = action_map[req.new_action]

    result = await db.execute(
        select(Photo).where(
            Photo.job_id == req.job_id,
            Photo.reason == target_reason,
        )
    )
    photos = list(result.scalars().all())

    # Skip photos already manually decided
    manual_reasons = {PhotoReason.MANUAL_KEEP, PhotoReason.MANUAL_TRASH, PhotoReason.MANUAL_DOCUMENTS}
    updated = 0
    for photo in photos:
        if photo.reason in manual_reasons:
            continue
        photo.action = new_action
        if new_reason is not None:
            photo.reason = new_reason
        photo.confidence = 1.0
        updated += 1

    await db.commit()
    return {"updated": updated}


class ExecuteGroupRequest(BaseModel):
    job_id: int
    reason: str


@router.post("/execute-group", response_model=dict)
async def execute_group(req: ExecuteGroupRequest, db: AsyncSession = Depends(get_db)):
    """Move files for a specific reason group (TRASH → _cleanup/trash/, DOCUMENTS → Documentos/)."""
    import os
    import re
    import shutil
    from pathlib import Path
    from services.scanner import extract_date

    job = await db.get(Job, req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        target_reason = PhotoReason(req.reason)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid reason: {req.reason}")

    # Fetch unmoved photos matching the reason
    result = await db.execute(
        select(Photo).where(
            Photo.job_id == req.job_id,
            Photo.reason == target_reason,
            Photo.moved == False,
            Photo.action.in_([PhotoAction.TRASH, PhotoAction.DOCUMENTS]),
        )
    )
    photos = list(result.scalars().all())

    source_dir = Path(job.source_dir)
    cleanup_dir = source_dir / "_cleanup"

    # Reuse pipeline mappings
    TRASH_SUBDIR = {
        PhotoReason.SCREENSHOT_FILENAME: "screenshots",
        PhotoReason.SCREENSHOT_DIMS_NO_EXIF: "screenshots",
        PhotoReason.VISION_SCREENSHOT: "screenshots",
        PhotoReason.VISION_MEME: "memes",
        PhotoReason.MESSAGING_IMAGE: "whatsapp",
        PhotoReason.WHATSAPP_STICKER: "whatsapp",
        PhotoReason.WHATSAPP_STATUS: "whatsapp",
        PhotoReason.VISION_ACCIDENTAL: "accidental",
        PhotoReason.BLURRY: "accidental",
        PhotoReason.TOO_DARK: "accidental",
        PhotoReason.OVEREXPOSED: "accidental",
        PhotoReason.TINY_IMAGE: "otros",
        PhotoReason.SMALL_FILE: "otros",
        PhotoReason.DUPLICATE: "otros",
        PhotoReason.MANUAL_TRASH: "otros",
    }
    DOC_SUBDIR = {
        PhotoReason.VISION_INVOICE: "facturas",
        PhotoReason.VISION_DOCUMENT: "otros",
        PhotoReason.MANUAL_DOCUMENTS: "otros",
    }

    def get_date_subdir(photo) -> str:
        dt = extract_date(photo.date_taken, photo.filename, photo.path)
        if dt:
            return f"{dt.year}/{dt.month:02d}"
        return "sin_fecha"

    moved = 0
    errors = 0
    size_freed = 0

    for photo in photos:
        src = Path(photo.path)
        if not src.exists():
            continue

        if photo.action == PhotoAction.TRASH:
            trash_subdir = TRASH_SUBDIR.get(target_reason, "otros")
            sub_dir = get_date_subdir(photo)
            dst_dir = cleanup_dir / "trash" / trash_subdir / sub_dir
        elif photo.action == PhotoAction.DOCUMENTS:
            doc_subdir = DOC_SUBDIR.get(target_reason, "otros")
            sub_dir = get_date_subdir(photo)
            dst_dir = source_dir / "Documentos" / doc_subdir / sub_dir
        else:
            continue

        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name

        if dst.exists():
            stem, suffix = dst.stem, dst.suffix
            counter = 1
            while dst.exists():
                dst = dst_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        try:
            shutil.move(str(src), str(dst))
            size_freed += photo.size_bytes or 0
            photo.path = str(dst)
            photo.moved = True
            moved += 1
        except Exception as e:
            logger.error(f"Failed to move {src}: {e}")
            errors += 1

    await db.commit()
    return {"moved": moved, "errors": errors, "size_freed": size_freed}


class AiReclassifyRequest(BaseModel):
    photo_ids: Optional[list[int]] = None  # None = all review photos
    confidence_threshold: float = 0.7


class AiReclassifyStartResult(BaseModel):
    task_id: str
    total: int
    provider_used: str


# In-memory state for running AI reclassify tasks
_ai_tasks: dict[str, dict] = {}


def _broadcast_ai(event: str, data: dict):
    from main import ws_manager, _loop
    msg = {"event": event, **data}
    if _loop and not _loop.is_closed():
        asyncio.run_coroutine_threadsafe(ws_manager.broadcast(msg), _loop)


async def _run_ai_reclassify(
    task_id: str,
    job_id: int,
    photo_ids: list[int],
    confidence_threshold: float,
    provider_type: str,
    base_url: str,
    model: str,
    api_key: str,
    provider_label: str,
):
    """Background coroutine: classify photos one-by-one, broadcasting progress."""
    from models import async_session

    provider = create_provider(
        provider_type=provider_type,
        base_url=base_url,
        model=model,
        api_key=api_key,
    )

    state = _ai_tasks[task_id]

    try:
        async with async_session() as db:
            result = await db.execute(
                select(Photo).where(Photo.id.in_(photo_ids))
            )
            photos = list(result.scalars().all())
            state["total"] = len(photos)

            for i, photo in enumerate(photos):
                # Check cancellation
                if state.get("cancelled"):
                    _broadcast_ai("ai_reclassify_cancelled", {
                        "task_id": task_id, "job_id": job_id,
                        "processed": i, "total": len(photos),
                        **_state_counts(state),
                    })
                    state["status"] = "cancelled"
                    return

                # Wait while paused
                while state.get("paused") and not state.get("cancelled"):
                    await asyncio.sleep(0.5)
                # Re-check cancel after unpause
                if state.get("cancelled"):
                    _broadcast_ai("ai_reclassify_cancelled", {
                        "task_id": task_id, "job_id": job_id,
                        "processed": i, "total": len(photos),
                        **_state_counts(state),
                    })
                    state["status"] = "cancelled"
                    return

                # Skip videos
                if photo.media_type == "video":
                    state["still_review"] += 1
                    state["processed"] = i + 1
                    _broadcast_ai("ai_reclassify_progress", {
                        "task_id": task_id, "job_id": job_id,
                        "processed": i + 1, "total": len(photos),
                        "current_file": photo.filename,
                        "photo_id": photo.id,
                        "result": "skip_video",
                        **_state_counts(state),
                    })
                    continue

                state["current_file"] = photo.filename
                _broadcast_ai("ai_reclassify_progress", {
                    "task_id": task_id, "job_id": job_id,
                    "processed": i, "total": len(photos),
                    "current_file": photo.filename,
                    "photo_id": photo.id,
                    "result": "processing",
                    **_state_counts(state),
                })

                classification = await provider.classify(
                    photo.path,
                    max_size=settings.default_max_image_size,
                )

                action_taken = "review"
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
                        state["still_review"] += 1
                        action_taken = "review"
                    elif cat in ("screenshot", "meme", "accidental"):
                        photo.action = PhotoAction.TRASH
                        photo.reason = {
                            "screenshot": PhotoReason.VISION_SCREENSHOT,
                            "meme": PhotoReason.VISION_MEME,
                            "accidental": PhotoReason.VISION_ACCIDENTAL,
                        }[cat]
                        state["trashed"] += 1
                        action_taken = "trash"
                    elif cat in ("invoice", "document"):
                        photo.action = PhotoAction.DOCUMENTS
                        photo.reason = {
                            "invoice": PhotoReason.VISION_INVOICE,
                            "document": PhotoReason.VISION_DOCUMENT,
                        }[cat]
                        state["documents"] += 1
                        action_taken = "documents"
                    elif cat == "photo":
                        photo.action = PhotoAction.KEEP
                        photo.reason = PhotoReason.VISION_PHOTO
                        state["kept"] += 1
                        action_taken = "keep"

                    state["classified"] += 1
                else:
                    state["still_review"] += 1

                state["processed"] = i + 1
                await db.commit()

                _broadcast_ai("ai_reclassify_progress", {
                    "task_id": task_id, "job_id": job_id,
                    "processed": i + 1, "total": len(photos),
                    "current_file": photo.filename,
                    "photo_id": photo.id,
                    "result": action_taken,
                    **_state_counts(state),
                })

        state["status"] = "done"
        _broadcast_ai("ai_reclassify_done", {
            "task_id": task_id, "job_id": job_id,
            "total": len(photos),
            "provider_used": provider_label,
            **_state_counts(state),
        })
    except Exception as e:
        logger.error(f"AI reclassify task {task_id} failed: {e}")
        state["status"] = "error"
        _broadcast_ai("ai_reclassify_error", {
            "task_id": task_id, "job_id": job_id,
            "error": str(e),
        })
    finally:
        await provider.close()


def _state_counts(state: dict) -> dict:
    return {
        "classified": state["classified"],
        "kept": state["kept"],
        "trashed": state["trashed"],
        "documents": state["documents"],
        "still_review": state["still_review"],
    }


@router.post("/{job_id}/reclassify-ai", response_model=AiReclassifyStartResult)
async def reclassify_with_ai(
    job_id: int,
    req: AiReclassifyRequest = AiReclassifyRequest(),
    db: AsyncSession = Depends(get_db),
):
    """Start AI reclassification as background task with WebSocket progress."""
    # Check if already running
    for tid, st in _ai_tasks.items():
        if st.get("status") == "running" and st.get("job_id") == job_id:
            raise HTTPException(status_code=409, detail="Ya hay una clasificacion IA en curso para este job")

    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")

    # Find available provider
    prov_result = await db.execute(
        select(VisionProviderConfig)
        .where(VisionProviderConfig.enabled == True)
        .order_by(VisionProviderConfig.priority)
    )
    provider = None
    provider_label = ""
    prov_config = None
    for pc in prov_result.scalars().all():
        candidate = create_provider(
            provider_type=pc.provider_type,
            base_url=pc.base_url,
            model=pc.model,
            api_key=pc.api_key,
        )
        if await candidate.is_available():
            provider_label = f"{pc.name} ({pc.model or pc.provider_type})"
            prov_config = pc
            provider = candidate
            break
        else:
            await candidate.close()

    if not provider or not prov_config:
        raise HTTPException(
            status_code=503,
            detail="Ningun provider de vision disponible. Configura uno en la seccion de Providers.",
        )
    await provider.close()

    # Get photo IDs to process
    query = select(Photo.id).where(
        Photo.job_id == job_id,
        Photo.action == PhotoAction.REVIEW,
    )
    if req.photo_ids:
        query = query.where(Photo.id.in_(req.photo_ids))

    result = await db.execute(query)
    photo_ids = [row[0] for row in result.all()]

    if not photo_ids:
        raise HTTPException(status_code=404, detail="No hay fotos en review para reclasificar")

    # Create task
    import uuid
    task_id = str(uuid.uuid4())[:8]
    _ai_tasks[task_id] = {
        "status": "running",
        "job_id": job_id,
        "total": len(photo_ids),
        "processed": 0,
        "current_file": "",
        "provider_used": provider_label,
        "classified": 0,
        "kept": 0,
        "trashed": 0,
        "documents": 0,
        "still_review": 0,
        "cancelled": False,
        "paused": False,
    }

    # Launch background task
    asyncio.create_task(_run_ai_reclassify(
        task_id=task_id,
        job_id=job_id,
        photo_ids=photo_ids,
        confidence_threshold=req.confidence_threshold,
        provider_type=prov_config.provider_type,
        base_url=prov_config.base_url,
        model=prov_config.model,
        api_key=prov_config.api_key,
        provider_label=provider_label,
    ))

    return AiReclassifyStartResult(
        task_id=task_id,
        total=len(photo_ids),
        provider_used=provider_label,
    )


@router.post("/reclassify-ai/{task_id}/cancel")
async def cancel_ai_reclassify(task_id: str):
    """Cancel a running AI reclassification task."""
    task = _ai_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    if task["status"] != "running":
        raise HTTPException(status_code=400, detail="La tarea ya termino")
    task["cancelled"] = True
    return {"ok": True}


@router.post("/reclassify-ai/{task_id}/pause")
async def pause_ai_reclassify(task_id: str):
    """Pause a running AI reclassification task."""
    task = _ai_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    if task["status"] != "running":
        raise HTTPException(status_code=400, detail="La tarea ya termino")
    task["paused"] = True
    _broadcast_ai("ai_reclassify_paused", {"task_id": task_id, "job_id": task["job_id"]})
    return {"ok": True}


@router.post("/reclassify-ai/{task_id}/resume")
async def resume_ai_reclassify(task_id: str):
    """Resume a paused AI reclassification task."""
    task = _ai_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    if task["status"] != "running":
        raise HTTPException(status_code=400, detail="La tarea ya termino")
    task["paused"] = False
    _broadcast_ai("ai_reclassify_resumed", {"task_id": task_id, "job_id": task["job_id"]})
    return {"ok": True}


@router.get("/reclassify-ai/active")
async def get_active_ai_task():
    """Return the currently running/paused AI task, if any."""
    for task_id, state in _ai_tasks.items():
        if state["status"] == "running":
            return {
                "task_id": task_id,
                "status": "paused" if state.get("paused") else "running",
                "job_id": state["job_id"],
                "total": state.get("total", 0),
                "processed": state.get("processed", 0),
                "current_file": state.get("current_file", ""),
                "provider_used": state.get("provider_used", ""),
                **_state_counts(state),
            }
    return None


@router.get("/reclassify-ai/provider-info")
async def get_reclassify_provider_info(db: AsyncSession = Depends(get_db)):
    """Return the provider that would be used for AI reclassification."""
    prov_result = await db.execute(
        select(VisionProviderConfig)
        .where(VisionProviderConfig.enabled == True)
        .order_by(VisionProviderConfig.priority)
    )
    for pc in prov_result.scalars().all():
        p = create_provider(
            provider_type=pc.provider_type,
            base_url=pc.base_url,
            model=pc.model,
            api_key=pc.api_key,
        )
        available = await p.is_available()
        await p.close()
        if available:
            return {"name": pc.name, "model": pc.model, "available": True}
    return {"name": None, "model": None, "available": False}


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
