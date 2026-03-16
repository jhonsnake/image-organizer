"""V2: Vision provider management — configure, detect, and test cloud/local providers."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import VisionProviderConfig, get_db
from services.vision import create_provider, detect_available_providers, PROVIDER_TYPES

router = APIRouter(prefix="/api/providers", tags=["providers"])


class ProviderSchema(BaseModel):
    name: str
    provider_type: str  # openai-compatible, anthropic, gemini
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    priority: int = 10
    enabled: bool = True


class ProviderResponse(ProviderSchema):
    id: int
    available: Optional[bool] = None
    models: Optional[list[str]] = None

    class Config:
        from_attributes = True


@router.get("/types")
async def list_provider_types():
    """List supported provider types."""
    return [
        {
            "type": "openai-compatible",
            "label": "OpenAI Compatible (LM Studio, vLLM, Ollama, OpenAI)",
            "requires_url": True,
            "requires_key": False,
        },
        {
            "type": "anthropic",
            "label": "Anthropic Claude",
            "requires_url": False,
            "requires_key": True,
        },
        {
            "type": "gemini",
            "label": "Google Gemini",
            "requires_url": False,
            "requires_key": True,
        },
    ]


@router.get("/", response_model=list[ProviderResponse])
async def list_providers(db: AsyncSession = Depends(get_db)):
    """List all configured providers."""
    result = await db.execute(
        select(VisionProviderConfig).order_by(VisionProviderConfig.priority)
    )
    providers = list(result.scalars().all())

    # Mask API keys in response
    responses = []
    for p in providers:
        resp = ProviderResponse.model_validate(p)
        if resp.api_key and len(resp.api_key) > 8:
            resp.api_key = resp.api_key[:4] + "..." + resp.api_key[-4:]
        responses.append(resp)

    return responses


@router.post("/", response_model=ProviderResponse)
async def create_provider_config(
    data: ProviderSchema, db: AsyncSession = Depends(get_db),
):
    """Add a new provider configuration."""
    if data.provider_type not in PROVIDER_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown type: {data.provider_type}")

    config = VisionProviderConfig(**data.model_dump())
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


@router.put("/{provider_id}", response_model=ProviderResponse)
async def update_provider_config(
    provider_id: int, data: ProviderSchema, db: AsyncSession = Depends(get_db),
):
    """Update a provider configuration."""
    config = await db.get(VisionProviderConfig, provider_id)
    if not config:
        raise HTTPException(status_code=404, detail="Provider not found")

    for key, value in data.model_dump().items():
        # Don't overwrite key if masked value sent back
        if key == "api_key" and "..." in value:
            continue
        setattr(config, key, value)

    await db.commit()
    await db.refresh(config)
    return config


@router.delete("/{provider_id}")
async def delete_provider_config(
    provider_id: int, db: AsyncSession = Depends(get_db),
):
    """Delete a provider configuration."""
    config = await db.get(VisionProviderConfig, provider_id)
    if not config:
        raise HTTPException(status_code=404, detail="Provider not found")

    await db.delete(config)
    await db.commit()
    return {"deleted": True}


class ReorderItem(BaseModel):
    id: int
    priority: int


@router.put("/reorder")
async def reorder_providers(
    items: list[ReorderItem], db: AsyncSession = Depends(get_db),
):
    """Batch-update provider priorities."""
    for item in items:
        await db.execute(
            update(VisionProviderConfig)
            .where(VisionProviderConfig.id == item.id)
            .values(priority=item.priority)
        )
    await db.commit()
    return {"updated": len(items)}


@router.patch("/{provider_id}/toggle")
async def toggle_provider(
    provider_id: int, db: AsyncSession = Depends(get_db),
):
    """Toggle a provider enabled/disabled."""
    config = await db.get(VisionProviderConfig, provider_id)
    if not config:
        raise HTTPException(status_code=404, detail="Provider not found")
    config.enabled = not config.enabled
    await db.commit()
    await db.refresh(config)
    return {"id": config.id, "enabled": config.enabled}


@router.get("/{provider_id}/models")
async def get_provider_models(
    provider_id: int, db: AsyncSession = Depends(get_db),
):
    """List models available on a specific provider."""
    config = await db.get(VisionProviderConfig, provider_id)
    if not config:
        raise HTTPException(status_code=404, detail="Provider not found")

    provider = create_provider(
        provider_type=config.provider_type,
        base_url=config.base_url,
        model=config.model,
        api_key=config.api_key,
    )
    try:
        models = await provider.list_models()
        return {"models": models}
    finally:
        await provider.close()


@router.post("/detect")
async def detect_providers(db: AsyncSession = Depends(get_db)):
    """Check which configured providers are currently available."""
    result = await db.execute(
        select(VisionProviderConfig)
        .where(VisionProviderConfig.enabled == True)
        .order_by(VisionProviderConfig.priority)
    )
    providers = list(result.scalars().all())

    configs = [
        {
            "id": p.id,
            "type": p.provider_type,
            "base_url": p.base_url,
            "model": p.model,
            "api_key": p.api_key,
            "name": p.name,
            "priority": p.priority,
        }
        for p in providers
    ]

    results = await detect_available_providers(configs)

    return {
        "providers": results,
        "recommended": next((r for r in results if r["available"]), None),
    }


@router.post("/{provider_id}/test")
async def test_provider(provider_id: int, db: AsyncSession = Depends(get_db)):
    """Test a specific provider by sending a simple request."""
    config = await db.get(VisionProviderConfig, provider_id)
    if not config:
        raise HTTPException(status_code=404, detail="Provider not found")

    provider = create_provider(
        provider_type=config.provider_type,
        base_url=config.base_url,
        model=config.model,
        api_key=config.api_key,
    )

    try:
        available = await provider.is_available()
        models = await provider.list_models() if available else []
        return {
            "available": available,
            "models": models,
            "provider_name": provider.provider_name,
        }
    finally:
        await provider.close()
