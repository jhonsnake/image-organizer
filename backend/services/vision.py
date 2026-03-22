"""
Vision classification service — Stage 4.
Uses OpenAI-compatible API (LM Studio, OpenAI, etc.)
Designed as an ABC so V2 can add Anthropic, Gemini, etc.
"""

import base64
import json
import logging
from abc import ABC, abstractmethod
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """You are an image classifier for a photo library cleanup tool.
Classify the image into EXACTLY ONE of these categories and respond with ONLY a JSON object:

- "photo": A legitimate personal photograph (people, places, events, nature, pets, food, etc.)
- "screenshot": A screen capture from a phone or computer
- "meme": An internet meme, sticker, viral image, or image with overlaid text meant to be funny/shared
- "invoice": A receipt, invoice, bill, ticket, boarding pass, or financial document
- "document": A photographed document that is NOT an invoice (ID, menu, handwritten note, letter, etc.)
- "accidental": An accidental photo (black, blurry pocket shot, floor, extremely dark/bright, finger over lens)

Respond ONLY with this JSON format, nothing else:
{"category": "photo|screenshot|meme|invoice|document|accidental", "confidence": 0.0-1.0}"""


class VisionProvider(ABC):
    """Abstract base for vision classification providers."""

    @abstractmethod
    async def classify(self, image_path: str, max_size: int = 512) -> Optional[dict]:
        """
        Classify an image. Returns {"category": str, "confidence": float} or None.
        """
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if this provider is reachable and has the model loaded."""
        ...

    @abstractmethod
    async def list_models(self) -> list[str]:
        """List available models from this provider."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @staticmethod
    def prepare_image_b64(image_path: str, max_size: int = 512) -> Optional[str]:
        """Resize and encode image as base64 JPEG."""
        try:
            with Image.open(image_path) as img:
                img.thumbnail((max_size, max_size))
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=80)
                return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as e:
            logger.warning(f"Failed to prepare image {image_path}: {e}")
            return None


class OpenAICompatibleProvider(VisionProvider):
    """
    Provider for any OpenAI-compatible API:
    LM Studio, vLLM, Ollama (with OpenAI compat), OpenAI itself, etc.
    """

    def __init__(self, base_url: str, model: str, api_key: str = "not-needed"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=60)

    @property
    def provider_name(self) -> str:
        return f"openai-compat ({self.base_url})"

    async def is_available(self) -> bool:
        try:
            resp = await self._client.get(
                f"{self.base_url}/models",
                headers=self._auth_headers(),
            )
            return resp.status_code == 200
        except Exception:
            return False

    def _auth_headers(self) -> dict:
        if self.api_key and self.api_key != "not-needed":
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    async def list_models(self) -> list[str]:
        try:
            resp = await self._client.get(
                f"{self.base_url}/models",
                headers=self._auth_headers(),
            )
            if resp.status_code == 200:
                data = resp.json()
                return [m["id"] for m in data.get("data", [])]
        except Exception:
            pass
        return []

    async def classify(self, image_path: str, max_size: int = 512) -> Optional[dict]:
        img_b64 = self.prepare_image_b64(image_path, max_size)
        if not img_b64:
            return None

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": CLASSIFICATION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_b64}"
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 100,
            "temperature": 0.1,
        }

        try:
            resp = await self._client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._auth_headers(),
            )
            if resp.status_code != 200:
                logger.warning(f"LLM returned {resp.status_code}: {resp.text[:200]}")
                return None

            content = resp.json()["choices"][0]["message"]["content"]
            return _parse_classification(content)

        except httpx.TimeoutException:
            logger.warning(f"Timeout classifying {Path(image_path).name}")
            return None
        except Exception as e:
            logger.warning(f"Vision classification failed: {e}")
            return None

    async def close(self):
        await self._client.aclose()


class AnthropicProvider(VisionProvider):
    """Provider for Anthropic Claude API (claude-sonnet, claude-haiku, etc.)."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key
        self.model = model
        self._client = httpx.AsyncClient(
            timeout=60,
            base_url="https://api.anthropic.com",
        )

    @property
    def provider_name(self) -> str:
        return "anthropic"

    async def is_available(self) -> bool:
        if not self.api_key or self.api_key == "not-needed":
            return False
        try:
            # Light check — just verify auth with a minimal request
            resp = await self._client.post(
                "/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            return resp.status_code in (200, 429)  # 429 = rate limited but valid key
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        return [
            "claude-sonnet-4-20250514",
            "claude-haiku-4-20250414",
        ]

    async def classify(self, image_path: str, max_size: int = 512) -> Optional[dict]:
        img_b64 = self.prepare_image_b64(image_path, max_size)
        if not img_b64:
            return None

        try:
            resp = await self._client.post(
                "/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 100,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/jpeg",
                                        "data": img_b64,
                                    },
                                },
                                {"type": "text", "text": CLASSIFICATION_PROMPT},
                            ],
                        }
                    ],
                },
            )
            if resp.status_code != 200:
                logger.warning(f"Anthropic returned {resp.status_code}: {resp.text[:200]}")
                return None

            content = resp.json()["content"][0]["text"]
            return _parse_classification(content)

        except Exception as e:
            logger.warning(f"Anthropic classification failed: {e}")
            return None

    async def close(self):
        await self._client.aclose()


class GeminiProvider(VisionProvider):
    """Provider for Google Gemini API."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model = model
        self._client = httpx.AsyncClient(timeout=60)

    @property
    def provider_name(self) -> str:
        return "gemini"

    async def is_available(self) -> bool:
        if not self.api_key or self.api_key == "not-needed":
            return False
        try:
            resp = await self._client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={self.api_key}",
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            resp = await self._client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={self.api_key}",
            )
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                return [
                    m["name"].replace("models/", "")
                    for m in models
                    if "vision" in m.get("name", "").lower()
                    or "gemini" in m.get("name", "").lower()
                ]
        except Exception:
            pass
        return ["gemini-2.0-flash", "gemini-2.5-flash"]

    async def classify(self, image_path: str, max_size: int = 512) -> Optional[dict]:
        img_b64 = self.prepare_image_b64(image_path, max_size)
        if not img_b64:
            return None

        try:
            resp = await self._client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}",
                json={
                    "contents": [
                        {
                            "parts": [
                                {
                                    "inline_data": {
                                        "mime_type": "image/jpeg",
                                        "data": img_b64,
                                    }
                                },
                                {"text": CLASSIFICATION_PROMPT},
                            ]
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0.1,
                        "maxOutputTokens": 100,
                    },
                },
            )
            if resp.status_code != 200:
                logger.warning(f"Gemini returned {resp.status_code}: {resp.text[:200]}")
                return None

            content = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            return _parse_classification(content)

        except Exception as e:
            logger.warning(f"Gemini classification failed: {e}")
            return None

    async def close(self):
        await self._client.aclose()


# ── Provider Registry ──

PROVIDER_TYPES = {
    "openai-compatible": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}


def create_provider(
    provider_type: str,
    base_url: str = "",
    model: str = "",
    api_key: str = "not-needed",
) -> VisionProvider:
    """Factory to create a provider by type."""
    if provider_type == "openai-compatible":
        return OpenAICompatibleProvider(base_url=base_url, model=model, api_key=api_key)
    elif provider_type == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model or "claude-sonnet-4-20250514")
    elif provider_type == "gemini":
        return GeminiProvider(api_key=api_key, model=model or "gemini-2.0-flash")
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")


async def detect_available_providers(configs: list[dict]) -> list[dict]:
    """
    Check which providers are available from a list of configurations.
    Each config: {"type": str, "base_url": str, "model": str, "api_key": str}
    Returns list with 'available' field added.
    """
    results = []
    for cfg in configs:
        provider = create_provider(
            provider_type=cfg["type"],
            base_url=cfg.get("base_url", ""),
            model=cfg.get("model", ""),
            api_key=cfg.get("api_key", "not-needed"),
        )
        try:
            available = await provider.is_available()
            models = await provider.list_models() if available else []
            results.append({
                **cfg,
                "available": available,
                "models": models,
                "provider_name": provider.provider_name,
            })
        except Exception:
            results.append({**cfg, "available": False, "models": [], "provider_name": cfg["type"]})
        finally:
            await provider.close()
    return results


def _parse_classification(text: str) -> Optional[dict]:
    """Parse model response into {"category": str, "confidence": float}."""
    text = text.strip()

    # Handle thinking tags
    if "</think>" in text:
        text = text.split("</think>")[-1].strip()

    # Handle markdown code blocks
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    # Find JSON
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        logger.warning(f"No JSON found in response: {text[:100]}")
        return None

    try:
        data = json.loads(text[start:end])
        category = data.get("category", "").lower()
        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        valid_categories = {"photo", "screenshot", "meme", "document", "invoice", "accidental"}
        if category not in valid_categories:
            logger.warning(f"Unknown category: {category}")
            return None

        return {"category": category, "confidence": confidence}

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(f"Failed to parse: {e}")
        return None
