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


# ── AI Summary ──

REASON_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "too_dark": ("Fotos oscuras/negras", "Imágenes demasiado oscuras o completamente negras"),
    "blurry": ("Fotos borrosas", "Imágenes desenfocadas o movidas"),
    "overexposed": ("Fotos sobreexpuestas", "Imágenes con demasiada luz o quemadas"),
    "duplicate": ("Duplicados", "Copias idénticas o casi idénticas de otras fotos"),
    "vision_screenshot": ("Screenshots (IA)", "Capturas de pantalla detectadas por IA"),
    "vision_meme": ("Memes / Stickers", "Memes e imágenes virales de internet"),
    "vision_accidental": ("Fotos accidentales", "Fotos tomadas sin querer (bolsillo, piso, dedo)"),
    "vision_document": ("Documentos", "Documentos de texto detectados por IA"),
    "vision_invoice": ("Facturas / Recibos", "Facturas y recibos detectados por IA"),
    "vision_ambiguous": ("Clasificación ambigua", "Imágenes que la IA no pudo clasificar con confianza"),
    "vision_photo": ("Fotos personales", "Fotos legítimas detectadas por IA"),
    "screenshot_filename": ("Screenshots (nombre)", "Capturas de pantalla detectadas por nombre de archivo"),
    "screenshot_dims_no_exif": ("Screenshots (dimensiones)", "Capturas por dimensiones de pantalla sin EXIF"),
    "messaging_image": ("Imágenes de mensajería", "Imágenes recibidas por WhatsApp, Telegram, etc."),
    "tiny_image": ("Imágenes diminutas", "Imágenes muy pequeñas (iconos, avatares)"),
    "small_file": ("Archivos pequeños", "Archivos de tamaño muy reducido"),
    "whatsapp_sticker": ("Stickers WhatsApp", "Stickers descargados de WhatsApp"),
    "whatsapp_status": ("Estados WhatsApp", "Imágenes de estados/stories de WhatsApp"),
    "unclassified": ("Sin clasificar", "Archivos pendientes de clasificación"),
    "legitimate": ("Legítimas", "Fotos legítimas sin clasificación adicional"),
}


@router.get("/{job_id}/ai-summary")
async def ai_summary(job_id: int, db: AsyncSession = Depends(get_db)):
    """Grouped classification summary for post-pipeline AI review."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result = await db.execute(select(Photo).where(Photo.job_id == job_id))
    photos = list(result.scalars().all())

    # Group non-KEEP photos by reason (exclude manual decisions)
    manual_reasons = {PhotoReason.MANUAL_KEEP, PhotoReason.MANUAL_TRASH, PhotoReason.MANUAL_DOCUMENTS}
    groups_map: dict[str, list[Photo]] = {}
    for p in photos:
        if p.action == PhotoAction.KEEP and p.reason not in (PhotoReason.VISION_PHOTO,):
            continue
        if p.reason in manual_reasons:
            continue
        r = p.reason.value if p.reason else "unclassified"
        groups_map.setdefault(r, []).append(p)

    groups = []
    summary_parts = []
    for reason, items in sorted(groups_map.items(), key=lambda x: len(x[1]), reverse=True):
        label, description = REASON_DESCRIPTIONS.get(reason, (reason.replace("_", " ").title(), ""))
        action_val = items[0].action.value if items[0].action else "review"

        # Sample photos (top 5 by confidence desc)
        sorted_items = sorted(items, key=lambda p: p.confidence or 0, reverse=True)
        samples = [
            {
                "id": p.id,
                "filename": p.filename,
                "thumbnail_path": p.thumbnail_path,
                "confidence": round(p.confidence or 0, 2),
                "size_bytes": p.size_bytes or 0,
            }
            for p in sorted_items[:5]
        ]

        total_moved = sum(1 for p in items if p.moved)
        size_bytes = sum(p.size_bytes or 0 for p in items)
        avg_conf = sum(p.confidence or 0 for p in items) / len(items) if items else 0

        groups.append({
            "reason": reason,
            "label": label,
            "description": description,
            "suggested_action": action_val,
            "count": len(items),
            "total_moved": total_moved,
            "size_bytes": size_bytes,
            "avg_confidence": round(avg_conf, 2),
            "sample_photos": samples,
        })

        if action_val in ("trash", "documents", "review") and reason != "vision_photo":
            summary_parts.append(f"{len(items)} {label.lower()}")

    summary_text = ""
    if summary_parts:
        summary_text = "Encontré " + ", ".join(summary_parts[:6])
        if len(summary_parts) > 6:
            summary_text += f" y {len(summary_parts) - 6} categorías más"

    return {
        "job_id": job_id,
        "total_classified": len(photos),
        "groups": groups,
        "summary_text": summary_text,
    }
