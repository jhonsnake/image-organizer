"""V2: File watcher API — start/stop real-time monitoring, view events."""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from models import WatcherEvent, VisionProviderConfig, PhotoAction, get_db
from services.watcher import get_watcher, start_watcher, stop_watcher
from services.vision import create_provider
from services.scanner import classify_metadata, analyze_quality

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watcher", tags=["watcher"])

# Reference to the auto-classify callback
_auto_classify_enabled = False


class WatcherConfig(BaseModel):
    poll_interval: int = 30
    auto_classify: bool = True


class WatcherEventResponse(BaseModel):
    id: int
    filepath: str
    filename: str
    nas_user: str
    action: Optional[str]
    reason: Optional[str]
    confidence: float
    provider_used: Optional[str]
    processed: bool
    moved: bool
    detected_at: datetime
    processed_at: Optional[datetime]

    class Config:
        from_attributes = True


@router.get("/status")
async def watcher_status():
    """Get watcher status."""
    watcher = get_watcher()
    if not watcher:
        return {"running": False, "known_files": 0, "watched_dirs": 0}
    return watcher.stats


@router.post("/start")
async def start_watching(config: WatcherConfig):
    """Start the file watcher."""
    global _auto_classify_enabled
    _auto_classify_enabled = config.auto_classify

    from main import broadcast_progress

    def on_new_file(filepath: str, username: str):
        """Callback when a new file is detected."""
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(
                _handle_new_file(filepath, username), loop
            )

    watcher = await start_watcher(
        on_new_file=on_new_file,
        poll_interval=config.poll_interval,
    )

    if not watcher:
        raise HTTPException(status_code=400, detail="No valid watch directories found")

    return {"status": "started", **watcher.stats}


@router.post("/stop")
async def stop_watching():
    """Stop the file watcher."""
    await stop_watcher()
    return {"status": "stopped"}


@router.get("/events", response_model=list[WatcherEventResponse])
async def list_events(
    nas_user: Optional[str] = None,
    processed: Optional[bool] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List recent watcher events."""
    query = select(WatcherEvent).order_by(desc(WatcherEvent.detected_at)).limit(limit)
    if nas_user:
        query = query.where(WatcherEvent.nas_user == nas_user)
    if processed is not None:
        query = query.where(WatcherEvent.processed == processed)

    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/events/stats")
async def event_stats(db: AsyncSession = Depends(get_db)):
    """Get watcher event statistics."""
    total = await db.execute(select(func.count(WatcherEvent.id)))
    processed = await db.execute(
        select(func.count(WatcherEvent.id)).where(WatcherEvent.processed == True)
    )
    pending = await db.execute(
        select(func.count(WatcherEvent.id)).where(WatcherEvent.processed == False)
    )

    return {
        "total": total.scalar(),
        "processed": processed.scalar(),
        "pending": pending.scalar(),
    }


async def _handle_new_file(filepath: str, username: str):
    """Process a newly detected file — classify and optionally auto-move."""
    from models import async_session
    from pathlib import Path

    async with async_session() as db:
        # Log the event
        event = WatcherEvent(
            filepath=filepath,
            filename=Path(filepath).name,
            nas_user=username,
        )
        db.add(event)
        await db.flush()

        if not _auto_classify_enabled:
            await db.commit()
            return

        # Try metadata classification first (instant)
        photo_dict = {
            "path": filepath,
            "filename": Path(filepath).name,
            "size_bytes": Path(filepath).stat().st_size,
        }

        result = classify_metadata(photo_dict)
        if result:
            action, reason, confidence = result
            event.action = action
            event.reason = reason.value
            event.confidence = confidence
            event.processed = True
            event.processed_at = datetime.utcnow()
            event.provider_used = "metadata"
            await db.commit()

            from main import broadcast_progress
            broadcast_progress(0, "watcher_classified", {
                "filename": event.filename,
                "action": action.value,
                "reason": reason.value,
                "provider": "metadata",
            })
            return

        # Try quality analysis
        quality_result = analyze_quality(filepath)
        if quality_result:
            action, reason, confidence, _ = quality_result
            event.action = action
            event.reason = reason.value
            event.confidence = confidence
            event.processed = True
            event.processed_at = datetime.utcnow()
            event.provider_used = "quality"
            await db.commit()
            return

        # Use vision provider (pick best available)
        providers_result = await db.execute(
            select(VisionProviderConfig)
            .where(VisionProviderConfig.enabled == True)
            .order_by(VisionProviderConfig.priority)
        )
        providers = list(providers_result.scalars().all())

        for prov_config in providers:
            provider = create_provider(
                provider_type=prov_config.provider_type,
                base_url=prov_config.base_url,
                model=prov_config.model,
                api_key=prov_config.api_key,
            )
            try:
                if not await provider.is_available():
                    continue

                classification = await provider.classify(filepath)
                if classification:
                    cat = classification["category"]
                    conf = classification["confidence"]

                    action_map = {
                        "photo": PhotoAction.KEEP,
                        "screenshot": PhotoAction.TRASH,
                        "meme": PhotoAction.TRASH,
                        "document": PhotoAction.DOCUMENTS,
                        "accidental": PhotoAction.TRASH,
                    }

                    event.action = action_map.get(cat, PhotoAction.REVIEW)
                    event.reason = f"vision_{cat}"
                    event.confidence = conf
                    event.processed = True
                    event.processed_at = datetime.utcnow()
                    event.provider_used = prov_config.name
                    await db.commit()

                    from main import broadcast_progress
                    broadcast_progress(0, "watcher_classified", {
                        "filename": event.filename,
                        "action": event.action.value,
                        "reason": event.reason,
                        "provider": prov_config.name,
                        "confidence": conf,
                    })
                    return
            except Exception as e:
                logger.warning(f"Provider {prov_config.name} failed: {e}")
            finally:
                await provider.close()

        # No provider could classify
        event.action = PhotoAction.REVIEW
        event.reason = "no_provider"
        event.processed = True
        event.processed_at = datetime.utcnow()
        await db.commit()
