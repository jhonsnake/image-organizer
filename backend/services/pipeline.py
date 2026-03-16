"""
Pipeline orchestrator — runs stages 1-4 + execute moves.
Reports progress via a callback for WebSocket updates.
"""

import asyncio
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import (
    Job, Photo, VisionProviderConfig, JobStatus, PipelineStage,
    PhotoAction, PhotoReason, async_session,
)
from services.scanner import (
    scan_directory, classify_metadata, compute_phash,
    find_duplicate_groups, analyze_quality,
)
from services.vision import create_provider
from services.thumbnails import generate_thumbnail

logger = logging.getLogger(__name__)

# Type for progress callback: (job_id, event_type, data_dict)
ProgressCallback = Optional[Callable[[int, str, dict], None]]


class PipelineRunner:
    def __init__(self, job_id: int, on_progress: ProgressCallback = None):
        self.job_id = job_id
        self.on_progress = on_progress
        self._paused = False
        self._cancelled = False

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def cancel(self):
        self._cancelled = True

    async def _wait_if_paused(self):
        while self._paused and not self._cancelled:
            await asyncio.sleep(0.5)

    async def _emit(self, event: str, data: dict):
        if self.on_progress:
            try:
                self.on_progress(self.job_id, event, data)
            except Exception:
                pass

    async def _emit_with_counts(self, db: AsyncSession, event: str, data: dict):
        """Emit event with live classification counts."""
        from sqlalchemy import func
        counts = {}
        for action in PhotoAction:
            result = await db.execute(
                select(func.count(Photo.id)).where(
                    Photo.job_id == self.job_id,
                    Photo.action == action,
                )
            )
            counts[action.value] = result.scalar() or 0
        data["counts"] = counts
        await self._emit(event, data)

    async def run(self):
        async with async_session() as db:
            job = await db.get(Job, self.job_id)
            if not job:
                logger.error(f"Job {self.job_id} not found")
                return

            try:
                job.status = JobStatus.RUNNING
                job.started_at = datetime.utcnow()
                await db.commit()

                # Determine which stage to resume from
                start_stage = job.current_stage

                if start_stage in (PipelineStage.SCANNING, PipelineStage.METADATA):
                    await self._stage_scan(db, job)
                    if self._cancelled:
                        return
                    await self._stage_metadata(db, job)
                    if self._cancelled:
                        return

                if start_stage in (PipelineStage.SCANNING, PipelineStage.METADATA, PipelineStage.DEDUP):
                    await self._stage_dedup(db, job)
                    if self._cancelled:
                        return

                if start_stage in (PipelineStage.SCANNING, PipelineStage.METADATA, PipelineStage.DEDUP, PipelineStage.QUALITY):
                    await self._stage_quality(db, job)
                    if self._cancelled:
                        return

                if start_stage != PipelineStage.DONE:
                    await self._stage_vision(db, job)
                    if self._cancelled:
                        return

                    await self._stage_execute(db, job)

                # Done
                job.status = JobStatus.COMPLETED
                job.current_stage = PipelineStage.DONE
                job.completed_at = datetime.utcnow()
                await self._update_stats(db, job)
                await db.commit()
                await self._emit("completed", {"job_id": self.job_id})

            except Exception as e:
                logger.exception(f"Pipeline failed for job {self.job_id}")
                job.status = JobStatus.FAILED
                job.error_message = str(e)
                await db.commit()
                await self._emit("error", {"job_id": self.job_id, "error": str(e)})

    async def _stage_scan(self, db: AsyncSession, job: Job):
        job.current_stage = PipelineStage.SCANNING
        await db.commit()
        await self._emit("stage", {"stage": "scanning", "message": "Escaneando archivos..."})

        files = await asyncio.to_thread(scan_directory, job.source_dir)
        job.total_files = len(files)
        await db.commit()

        await self._emit("scan_complete", {"total": len(files)})

        # Bulk insert photo records
        for i, f in enumerate(files):
            photo = Photo(
                job_id=job.id,
                path=f["path"],
                filename=f["filename"],
                extension=f["extension"],
                size_bytes=f["size_bytes"],
            )
            db.add(photo)

            if i % 500 == 0:
                await db.flush()
                await self._emit("progress", {
                    "stage": "scanning",
                    "current": i,
                    "total": len(files),
                })

        await db.commit()

    async def _stage_metadata(self, db: AsyncSession, job: Job):
        await self._wait_if_paused()
        job.current_stage = PipelineStage.METADATA
        await db.commit()
        await self._emit("stage", {"stage": "metadata", "message": "Analizando metadata..."})

        result = await db.execute(
            select(Photo).where(Photo.job_id == job.id, Photo.action == PhotoAction.KEEP)
        )
        photos = list(result.scalars().all())

        classified = 0
        for i, photo in enumerate(photos):
            await self._wait_if_paused()
            if self._cancelled:
                return

            photo_dict = {
                "path": photo.path,
                "filename": photo.filename,
                "size_bytes": photo.size_bytes,
            }

            classification = await asyncio.to_thread(classify_metadata, photo_dict)

            if classification:
                action, reason, confidence = classification
                photo.action = action
                photo.reason = reason
                photo.confidence = confidence
                photo.stage_decided = 1
                photo.has_camera_exif = photo_dict.get("has_camera_exif", False)
                photo.camera_make = photo_dict.get("camera_make")
                photo.date_taken = photo_dict.get("date_taken")
                if "width" in photo_dict:
                    photo.width = photo_dict["width"]
                    photo.height = photo_dict["height"]
                classified += 1
            else:
                photo.has_camera_exif = photo_dict.get("has_camera_exif", False)
                photo.camera_make = photo_dict.get("camera_make")
                photo.date_taken = photo_dict.get("date_taken")
                if "width" in photo_dict:
                    photo.width = photo_dict["width"]
                    photo.height = photo_dict["height"]

            if i % 100 == 0:
                await db.flush()
                await self._emit_with_counts(db, "progress", {
                    "stage": "metadata",
                    "current": i,
                    "total": len(photos),
                    "classified": classified,
                })

        job.processed_files = classified
        await db.commit()
        await self._emit_with_counts(db, "stage_complete", {
            "stage": "metadata", "classified": classified, "total": len(photos),
        })

    async def _stage_dedup(self, db: AsyncSession, job: Job):
        await self._wait_if_paused()
        job.current_stage = PipelineStage.DEDUP
        await db.commit()
        await self._emit("stage", {"stage": "dedup", "message": "Buscando duplicados..."})

        result = await db.execute(
            select(Photo).where(Photo.job_id == job.id, Photo.action == PhotoAction.KEEP)
        )
        photos = list(result.scalars().all())

        # Compute hashes
        for i, photo in enumerate(photos):
            await self._wait_if_paused()
            if self._cancelled:
                return
            h = await asyncio.to_thread(compute_phash, photo.path)
            if h:
                photo.phash = h

            if i % 50 == 0:
                await self._emit("progress", {
                    "stage": "dedup",
                    "current": i,
                    "total": len(photos),
                    "substage": "hashing",
                })

        await db.flush()

        # Find groups
        photo_dicts = [{"path": p.path, "phash": p.phash, "size_bytes": p.size_bytes} for p in photos]
        groups = await asyncio.to_thread(find_duplicate_groups, photo_dicts, job.hash_threshold or 8)

        dup_count = 0
        for group in groups:
            group_id = f"dup_{job.id}_{groups.index(group)}"
            # First is best, rest are duplicates
            photos[group[0]].duplicate_group = group_id
            for idx in group[1:]:
                photos[idx].action = PhotoAction.REVIEW
                photos[idx].reason = PhotoReason.DUPLICATE
                photos[idx].confidence = 0.85
                photos[idx].stage_decided = 2
                photos[idx].duplicate_group = group_id
                dup_count += 1

        await db.commit()
        await self._emit_with_counts(db, "stage_complete", {
            "stage": "dedup", "groups": len(groups), "duplicates": dup_count,
        })

    async def _stage_quality(self, db: AsyncSession, job: Job):
        await self._wait_if_paused()
        job.current_stage = PipelineStage.QUALITY
        await db.commit()
        await self._emit("stage", {"stage": "quality", "message": "Analizando calidad..."})

        result = await db.execute(
            select(Photo).where(Photo.job_id == job.id, Photo.action == PhotoAction.KEEP)
        )
        photos = list(result.scalars().all())

        classified = 0
        for i, photo in enumerate(photos):
            await self._wait_if_paused()
            if self._cancelled:
                return

            qresult = await asyncio.to_thread(
                analyze_quality,
                photo.path,
                job.blur_threshold or settings.default_blur_threshold,
                settings.default_darkness_threshold,
                settings.default_brightness_threshold,
            )

            if qresult:
                action, reason, confidence, extra = qresult
                photo.action = action
                photo.reason = reason
                photo.confidence = confidence
                photo.stage_decided = 3
                photo.blur_score = extra.get("blur_score", 0)
                photo.brightness = extra.get("brightness", 128)
                photo.width = extra.get("width", photo.width)
                photo.height = extra.get("height", photo.height)
                classified += 1

            if i % 50 == 0:
                await db.flush()
                await self._emit_with_counts(db, "progress", {
                    "stage": "quality",
                    "current": i,
                    "total": len(photos),
                    "classified": classified,
                })

        await db.commit()
        await self._emit_with_counts(db, "stage_complete", {
            "stage": "quality", "classified": classified, "total": len(photos),
        })

    async def _stage_vision(self, db: AsyncSession, job: Job):
        await self._wait_if_paused()
        job.current_stage = PipelineStage.VISION
        await db.commit()

        # Process both KEEP (unclassified) and REVIEW (low-confidence from quality stage)
        result = await db.execute(
            select(Photo).where(
                Photo.job_id == job.id,
                Photo.action.in_([PhotoAction.KEEP, PhotoAction.REVIEW]),
            )
        )
        photos = list(result.scalars().all())

        if not photos:
            await self._emit("stage_complete", {"stage": "vision", "classified": 0, "total": 0})
            return

        confidence_threshold = job.confidence_threshold or settings.default_confidence_threshold

        # Always use provider registry — try each enabled provider in priority order
        provider = None
        provider_label = ""

        prov_result = await db.execute(
            select(VisionProviderConfig)
            .where(VisionProviderConfig.enabled == True)
            .order_by(VisionProviderConfig.priority)
        )
        prov_configs = list(prov_result.scalars().all())

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
                await self._emit("stage", {
                    "stage": "vision",
                    "message": f"Usando provider: {provider_label}",
                })
                break
            else:
                await candidate.close()
                await self._emit("stage", {
                    "stage": "vision",
                    "message": f"Provider '{pc.name}' no disponible, probando siguiente...",
                })

        if not provider:
            await self._emit("stage", {
                "stage": "vision",
                "message": "Ningun provider de vision disponible. Enviando restantes a review.",
            })
            for photo in photos:
                photo.action = PhotoAction.REVIEW
                photo.reason = PhotoReason.VISION_AMBIGUOUS
                photo.confidence = 0.0
                photo.stage_decided = 4
            await db.commit()
            return

        await self._emit("stage", {
            "stage": "vision",
            "message": f"Clasificando {len(photos)} imagenes con {provider_label}...",
        })

        classified = 0
        for i, photo in enumerate(photos):
            await self._wait_if_paused()
            if self._cancelled:
                await provider.close()
                return

            classification = await provider.classify(
                photo.path,
                max_size=settings.default_max_image_size,
            )

            if classification:
                cat = classification["category"]
                conf = classification["confidence"]
                photo.vision_label = cat
                photo.vision_confidence = conf

                # Map to action
                if conf < confidence_threshold:
                    photo.action = PhotoAction.REVIEW
                    photo.reason = PhotoReason.VISION_AMBIGUOUS
                elif cat == "screenshot":
                    photo.action = PhotoAction.TRASH
                    photo.reason = PhotoReason.VISION_SCREENSHOT
                elif cat == "meme":
                    photo.action = PhotoAction.TRASH
                    photo.reason = PhotoReason.VISION_MEME
                elif cat == "document":
                    photo.action = PhotoAction.DOCUMENTS
                    photo.reason = PhotoReason.VISION_DOCUMENT
                elif cat == "accidental":
                    photo.action = PhotoAction.TRASH
                    photo.reason = PhotoReason.VISION_ACCIDENTAL
                elif cat == "photo":
                    photo.action = PhotoAction.KEEP
                    photo.reason = PhotoReason.VISION_PHOTO

                photo.confidence = conf
                photo.stage_decided = 4
                classified += 1
            else:
                photo.action = PhotoAction.REVIEW
                photo.reason = PhotoReason.VISION_AMBIGUOUS
                photo.confidence = 0.0
                photo.stage_decided = 4

            # Generate thumbnail for review items
            if photo.action == PhotoAction.REVIEW:
                thumb = await asyncio.to_thread(
                    generate_thumbnail,
                    photo.path,
                    settings.thumbnail_dir,
                    settings.thumbnail_size,
                )
                if thumb:
                    photo.thumbnail_path = thumb

            if i % 5 == 0:
                await db.flush()
                await self._emit_with_counts(db, "progress", {
                    "stage": "vision",
                    "current": i,
                    "total": len(photos),
                    "classified": classified,
                })

        await provider.close()
        await db.commit()
        await self._emit_with_counts(db, "stage_complete", {
            "stage": "vision", "classified": classified, "total": len(photos),
        })

    async def _stage_execute(self, db: AsyncSession, job: Job):
        await self._wait_if_paused()
        job.current_stage = PipelineStage.EXECUTING
        await db.commit()
        await self._emit("stage", {"stage": "executing", "message": "Moviendo archivos..."})

        source_dir = Path(job.source_dir)
        cleanup_dir = source_dir / "_cleanup"

        action_dirs = {
            PhotoAction.TRASH: cleanup_dir / "trash",
            PhotoAction.DOCUMENTS: cleanup_dir / "documents",
        }

        # Create staging dirs
        for d in action_dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        (cleanup_dir / "review").mkdir(parents=True, exist_ok=True)

        # Move high-confidence trash and documents (NOT review — those stay for manual review)
        result = await db.execute(
            select(Photo).where(
                Photo.job_id == job.id,
                Photo.action.in_([PhotoAction.TRASH, PhotoAction.DOCUMENTS]),
                Photo.moved == False,
            )
        )
        to_move = list(result.scalars().all())

        moved = 0
        errors = 0
        for i, photo in enumerate(to_move):
            await self._wait_if_paused()
            if self._cancelled:
                return

            target_dir = action_dirs.get(photo.action)
            if not target_dir:
                continue

            src = Path(photo.path)
            if not src.exists():
                continue

            # Organize by year/month
            try:
                mtime = os.path.getmtime(photo.path)
                dt = datetime.fromtimestamp(mtime)
                sub_dir = f"{dt.year}/{dt.month:02d}"
            except Exception:
                sub_dir = "unknown"

            dst_dir = target_dir / sub_dir
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / src.name

            # Handle name conflicts
            if dst.exists():
                stem, suffix = dst.stem, dst.suffix
                counter = 1
                while dst.exists():
                    dst = dst_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            try:
                shutil.move(str(src), str(dst))
                photo.moved = True
                moved += 1
            except Exception as e:
                logger.error(f"Failed to move {src}: {e}")
                errors += 1

            if i % 50 == 0:
                await db.flush()
                await self._emit_with_counts(db, "progress", {
                    "stage": "executing",
                    "current": i,
                    "total": len(to_move),
                    "moved": moved,
                    "errors": errors,
                })

        await db.commit()
        await self._update_stats(db, job)
        await db.commit()
        await self._emit_with_counts(db, "stage_complete", {
            "stage": "executing", "moved": moved, "errors": errors,
        })

    async def _update_stats(self, db: AsyncSession, job: Job):
        result = await db.execute(select(Photo).where(Photo.job_id == job.id))
        photos = list(result.scalars().all())

        job.kept_count = sum(1 for p in photos if p.action == PhotoAction.KEEP)
        job.trash_count = sum(1 for p in photos if p.action == PhotoAction.TRASH)
        job.review_count = sum(1 for p in photos if p.action == PhotoAction.REVIEW)
        job.documents_count = sum(1 for p in photos if p.action == PhotoAction.DOCUMENTS)
        job.space_saved_bytes = sum(
            p.size_bytes for p in photos if p.action == PhotoAction.TRASH and p.moved
        )
