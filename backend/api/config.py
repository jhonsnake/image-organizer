"""Configuration endpoints — save/load settings per NAS user."""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import AppConfig, get_db

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigSchema(BaseModel):
    nas_user: str
    source_dir: str
    llm_url: str = settings.default_llm_url
    llm_model: str = settings.default_model
    blur_threshold: float = settings.default_blur_threshold
    hash_threshold: int = settings.default_hash_threshold
    darkness_threshold: float = settings.default_darkness_threshold
    brightness_threshold: float = settings.default_brightness_threshold
    confidence_threshold: float = settings.default_confidence_threshold
    max_image_size: int = settings.default_max_image_size


class ConfigResponse(ConfigSchema):
    id: int

    class Config:
        from_attributes = True


@router.get("/users")
async def list_users():
    """List known NAS users and their Photos directories."""
    users = []
    for username in settings.nas_users:
        photos_dir = f"{settings.homes_mount}/{username}/Photos"
        exists = os.path.isdir(photos_dir)
        users.append({
            "username": username,
            "photos_dir": photos_dir,
            "available": exists,
        })
    return users


@router.get("/browse")
async def browse_directory(path: str):
    """Browse subdirectories within allowed paths (for folder selection)."""
    base = Path(settings.homes_mount)
    target = Path(path)

    # Security: must be under homes mount
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Path outside allowed directory")

    if not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    dirs = []
    for entry in sorted(target.iterdir()):
        if entry.is_dir() and not entry.name.startswith(".") and entry.name != "@eaDir":
            dirs.append({
                "name": entry.name,
                "path": str(entry),
            })

    return {"current": str(target), "directories": dirs}


@router.get("/{nas_user}", response_model=ConfigResponse | None)
async def get_config(nas_user: str, db: AsyncSession = Depends(get_db)):
    """Get saved configuration for a NAS user."""
    result = await db.execute(
        select(AppConfig).where(AppConfig.nas_user == nas_user)
    )
    config = result.scalar_one_or_none()
    if not config:
        return None
    return config


@router.put("/{nas_user}", response_model=ConfigResponse)
async def save_config(nas_user: str, data: ConfigSchema, db: AsyncSession = Depends(get_db)):
    """Save or update configuration for a NAS user."""
    result = await db.execute(
        select(AppConfig).where(AppConfig.nas_user == nas_user)
    )
    config = result.scalar_one_or_none()

    if config:
        for key, value in data.model_dump().items():
            setattr(config, key, value)
    else:
        config = AppConfig(**data.model_dump())
        db.add(config)

    await db.commit()
    await db.refresh(config)
    return config


@router.get("/llm/models")
async def list_llm_models(llm_url: str = settings.default_llm_url):
    """List available models from the LLM provider."""
    from services.vision import OpenAICompatibleProvider
    provider = OpenAICompatibleProvider(base_url=llm_url, model="")
    try:
        models = await provider.list_models()
        available = await provider.is_available()
        return {"available": available, "models": models, "url": llm_url}
    finally:
        await provider.close()
