"""Space analysis endpoint — breakdown by category with recommendations."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Job, Photo, PhotoAction, PhotoReason, get_db

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get("/{job_id}/space-breakdown")
async def space_breakdown(job_id: int, db: AsyncSession = Depends(get_db)):
    """Space usage breakdown by reason with actionable recommendations."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result = await db.execute(select(Photo).where(Photo.job_id == job_id))
    photos = list(result.scalars().all())

    # Group by reason
    by_reason: dict[str, dict] = {}
    for p in photos:
        r = p.reason.value if p.reason else "unknown"
        if r not in by_reason:
            by_reason[r] = {"count": 0, "size_bytes": 0, "action": p.action.value if p.action else "keep"}
        by_reason[r]["count"] += 1
        by_reason[r]["size_bytes"] += p.size_bytes or 0

    # Group by action
    by_action: dict[str, dict] = {}
    for action in PhotoAction:
        items = [p for p in photos if p.action == action]
        by_action[action.value] = {
            "count": len(items),
            "size_bytes": sum(p.size_bytes or 0 for p in items),
        }

    # Group by media_type
    by_media_type: dict[str, dict] = {}
    for p in photos:
        mt = p.media_type or "image"
        if mt not in by_media_type:
            by_media_type[mt] = {"count": 0, "size_bytes": 0}
        by_media_type[mt]["count"] += 1
        by_media_type[mt]["size_bytes"] += p.size_bytes or 0

    # Recommendations: group trash items by reason, sorted by recoverable space
    recommendations = []
    trash_photos = [p for p in photos if p.action == PhotoAction.TRASH]

    reason_groups: dict[str, list] = {}
    for p in trash_photos:
        r = p.reason.value if p.reason else "unknown"
        reason_groups.setdefault(r, []).append(p)

    for reason, items in sorted(reason_groups.items(), key=lambda x: sum(p.size_bytes or 0 for p in x[1]), reverse=True):
        total_size = sum(p.size_bytes or 0 for p in items)
        recommendations.append({
            "reason": reason,
            "count": len(items),
            "size_bytes": total_size,
            "moved": sum(1 for p in items if p.moved),
        })

    # Top 20 largest trash files
    top_files = sorted(trash_photos, key=lambda p: p.size_bytes or 0, reverse=True)[:20]
    top_large = [
        {
            "id": p.id,
            "filename": p.filename,
            "size_bytes": p.size_bytes,
            "reason": p.reason.value if p.reason else "unknown",
            "media_type": p.media_type or "image",
            "thumbnail_path": p.thumbnail_path,
        }
        for p in top_files
    ]

    return {
        "job_id": job_id,
        "total_files": len(photos),
        "total_size_bytes": sum(p.size_bytes or 0 for p in photos),
        "recoverable_bytes": sum(p.size_bytes or 0 for p in trash_photos),
        "by_reason": by_reason,
        "by_action": by_action,
        "by_media_type": by_media_type,
        "recommendations": recommendations,
        "top_large_files": top_large,
    }
