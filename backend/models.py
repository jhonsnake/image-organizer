import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Enum, Text,
    ForeignKey, create_engine, Index,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship

from config import settings


class Base(DeclarativeBase):
    pass


# ── Enums ──

class PhotoAction(str, enum.Enum):
    KEEP = "keep"
    TRASH = "trash"
    REVIEW = "review"
    DOCUMENTS = "documents"


class PhotoReason(str, enum.Enum):
    # Metadata stage
    SCREENSHOT_FILENAME = "screenshot_filename"
    SCREENSHOT_DIMS_NO_EXIF = "screenshot_dims_no_exif"
    MESSAGING_IMAGE = "messaging_image"
    TINY_IMAGE = "tiny_image"
    SMALL_FILE = "small_file"
    # Hash stage
    DUPLICATE = "duplicate"
    # Quality stage
    BLURRY = "blurry"
    TOO_DARK = "too_dark"
    OVEREXPOSED = "overexposed"
    # Vision stage
    VISION_SCREENSHOT = "vision_screenshot"
    VISION_MEME = "vision_meme"
    VISION_DOCUMENT = "vision_document"
    VISION_ACCIDENTAL = "vision_accidental"
    VISION_AMBIGUOUS = "vision_ambiguous"
    VISION_PHOTO = "vision_photo"
    # Fallback
    UNCLASSIFIED = "unclassified"
    LEGITIMATE = "legitimate"
    # Manual
    MANUAL_KEEP = "manual_keep"
    MANUAL_TRASH = "manual_trash"
    MANUAL_DOCUMENTS = "manual_documents"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class PipelineStage(str, enum.Enum):
    SCANNING = "scanning"
    METADATA = "metadata"
    DEDUP = "dedup"
    QUALITY = "quality"
    VISION = "vision"
    EXECUTING = "executing"
    DONE = "done"


# ── Models ──

class AppConfig(Base):
    """Persistent app configuration (one row per user)."""
    __tablename__ = "app_config"

    id = Column(Integer, primary_key=True)
    nas_user = Column(String(255), unique=True, nullable=False)
    source_dir = Column(String(1024), nullable=False)
    llm_url = Column(String(512), nullable=False)
    llm_model = Column(String(255), nullable=False)
    blur_threshold = Column(Float, default=50.0)
    hash_threshold = Column(Integer, default=8)
    darkness_threshold = Column(Float, default=15.0)
    brightness_threshold = Column(Float, default=245.0)
    confidence_threshold = Column(Float, default=0.7)
    max_image_size = Column(Integer, default=512)
    active_provider_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Job(Base):
    """A pipeline execution run."""
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    nas_user = Column(String(255), nullable=False)
    source_dir = Column(String(1024), nullable=False)
    status = Column(Enum(JobStatus), default=JobStatus.PENDING)
    current_stage = Column(Enum(PipelineStage), default=PipelineStage.SCANNING)
    total_files = Column(Integer, default=0)
    processed_files = Column(Integer, default=0)
    # Stats
    kept_count = Column(Integer, default=0)
    trash_count = Column(Integer, default=0)
    review_count = Column(Integer, default=0)
    documents_count = Column(Integer, default=0)
    space_saved_bytes = Column(Integer, default=0)
    # Config snapshot
    llm_url = Column(String(512))
    llm_model = Column(String(255))
    provider_id = Column(Integer, nullable=True)  # Snapshot of provider used
    blur_threshold = Column(Float)
    hash_threshold = Column(Integer)
    confidence_threshold = Column(Float)
    # Stage progress (persisted for resume)
    stage_progress = Column(Integer, default=0)
    stage_total = Column(Integer, default=0)
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    photos = relationship("Photo", back_populates="job", cascade="all, delete-orphan")


class Photo(Base):
    """Individual photo record within a job."""
    __tablename__ = "photos"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    path = Column(String(2048), nullable=False)
    filename = Column(String(512), nullable=False)
    extension = Column(String(16))
    size_bytes = Column(Integer, default=0)
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    # Classification
    action = Column(Enum(PhotoAction), default=PhotoAction.KEEP)
    reason = Column(Enum(PhotoReason), default=PhotoReason.UNCLASSIFIED)
    confidence = Column(Float, default=0.0)
    stage_decided = Column(Integer, default=0)  # 1=metadata, 2=hash, 3=quality, 4=vision
    # Metadata
    has_camera_exif = Column(Boolean, default=False)
    camera_make = Column(String(255), nullable=True)
    date_taken = Column(String(64), nullable=True)
    # Quality
    blur_score = Column(Float, default=0.0)
    brightness = Column(Float, default=128.0)
    # Hash
    phash = Column(String(64), nullable=True)
    duplicate_group = Column(String(64), nullable=True)
    # Vision
    vision_label = Column(String(64), nullable=True)
    vision_confidence = Column(Float, default=0.0)
    # State
    moved = Column(Boolean, default=False)
    thumbnail_path = Column(String(1024), nullable=True)

    job = relationship("Job", back_populates="photos")

    __table_args__ = (
        Index("ix_photos_job_action", "job_id", "action"),
        Index("ix_photos_job_stage", "job_id", "stage_decided"),
    )


class VisionProviderConfig(Base):
    """V2: Configured vision providers (local + cloud)."""
    __tablename__ = "vision_providers"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)  # Display name
    provider_type = Column(String(64), nullable=False)  # openai-compatible, anthropic, gemini
    base_url = Column(String(512), default="")
    model = Column(String(255), default="")
    api_key = Column(String(512), default="")  # Stored encrypted in production
    priority = Column(Integer, default=10)  # Lower = preferred (fallback order)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WatcherEvent(Base):
    """V2: Log of auto-detected file events from the watcher."""
    __tablename__ = "watcher_events"

    id = Column(Integer, primary_key=True)
    filepath = Column(String(2048), nullable=False)
    filename = Column(String(512), nullable=False)
    nas_user = Column(String(255), nullable=False)
    action = Column(Enum(PhotoAction), nullable=True)
    reason = Column(String(255), nullable=True)
    confidence = Column(Float, default=0.0)
    provider_used = Column(String(255), nullable=True)
    processed = Column(Boolean, default=False)
    moved = Column(Boolean, default=False)
    detected_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)


# ── Database setup ──

engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"timeout": 30},
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        # Enable WAL mode for better concurrent read/write
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
