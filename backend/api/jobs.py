"""Job management endpoints — create, pause, resume, list jobs."""

import asyncio
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import Job, Photo, JobStatus, PipelineStage, PhotoAction, get_db
from services.pipeline import PipelineRunner

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# Active pipeline runners indexed by job_id
_active_runners: dict[int, PipelineRunner] = {}


class CreateJobRequest(BaseModel):
    nas_user: str
    source_dir: str
    llm_url: str = settings.default_llm_url
    llm_model: str = settings.default_model
    blur_threshold: float = settings.default_blur_threshold
    hash_threshold: int = settings.default_hash_threshold
    confidence_threshold: float = settings.default_confidence_threshold


class JobResponse(BaseModel):
    id: int
    nas_user: str
    source_dir: str
    status: str
    current_stage: str
    total_files: int
    processed_files: int
    kept_count: int
    trash_count: int
    review_count: int
    documents_count: int
    space_saved_bytes: int
    llm_model: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_message: Optional[str]

    class Config:
        from_attributes = True


@router.post("/", response_model=JobResponse)
async def create_job(req: CreateJobRequest, db: AsyncSession = Depends(get_db)):
    """Create and start a new cleanup job."""
    # Check for existing running jobs for this user
    result = await db.execute(
        select(Job).where(
            Job.nas_user == req.nas_user,
            Job.status.in_([JobStatus.RUNNING, JobStatus.PENDING]),
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Ya hay un job activo para {req.nas_user} (ID: {existing.id})",
        )

    job = Job(
        nas_user=req.nas_user,
        source_dir=req.source_dir,
        llm_url=req.llm_url,
        llm_model=req.llm_model,
        blur_threshold=req.blur_threshold,
        hash_threshold=req.hash_threshold,
        confidence_threshold=req.confidence_threshold,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Start pipeline in background
    from main import broadcast_progress
    runner = PipelineRunner(job.id, on_progress=broadcast_progress)
    _active_runners[job.id] = runner
    asyncio.create_task(_run_pipeline(runner, job.id))

    return job


async def _run_pipeline(runner: PipelineRunner, job_id: int):
    try:
        await runner.run()
    finally:
        _active_runners.pop(job_id, None)


@router.get("/", response_model=list[JobResponse])
async def list_jobs(
    nas_user: Optional[str] = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """List jobs, optionally filtered by user."""
    query = select(Job).order_by(desc(Job.created_at)).limit(limit)
    if nas_user:
        query = query.where(Job.nas_user == nas_user)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Get job details."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}/stats")
async def get_job_stats(job_id: int, db: AsyncSession = Depends(get_db)):
    """Get detailed stats for a job (for charts)."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result = await db.execute(select(Photo).where(Photo.job_id == job_id))
    photos = list(result.scalars().all())

    # By action
    by_action = {}
    for action in PhotoAction:
        count = sum(1 for p in photos if p.action == action)
        size = sum(p.size_bytes for p in photos if p.action == action)
        by_action[action.value] = {"count": count, "size_bytes": size}

    # By stage
    by_stage = {}
    for stage in range(5):
        count = sum(1 for p in photos if p.stage_decided == stage)
        by_stage[str(stage)] = count

    # By reason
    by_reason = {}
    for p in photos:
        r = p.reason.value if p.reason else "unknown"
        by_reason[r] = by_reason.get(r, 0) + 1

    return {
        "total": len(photos),
        "by_action": by_action,
        "by_stage": by_stage,
        "by_reason": by_reason,
    }


@router.post("/{job_id}/pause")
async def pause_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Pause a running job."""
    runner = _active_runners.get(job_id)
    if not runner:
        raise HTTPException(status_code=404, detail="No hay runner activo para este job")

    runner.pause()
    job = await db.get(Job, job_id)
    job.status = JobStatus.PAUSED
    await db.commit()
    return {"status": "paused"}


@router.post("/{job_id}/resume")
async def resume_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Resume a paused job."""
    runner = _active_runners.get(job_id)
    if runner:
        runner.resume()
        job = await db.get(Job, job_id)
        job.status = JobStatus.RUNNING
        await db.commit()
        return {"status": "resumed"}

    # Runner lost (e.g., server restarted) — create new one
    job = await db.get(Job, job_id)
    if not job or job.status not in (JobStatus.PAUSED, JobStatus.FAILED):
        raise HTTPException(status_code=400, detail="Job no se puede reanudar")

    from main import broadcast_progress
    runner = PipelineRunner(job.id, on_progress=broadcast_progress)
    _active_runners[job.id] = runner
    asyncio.create_task(_run_pipeline(runner, job.id))

    return {"status": "resumed"}


