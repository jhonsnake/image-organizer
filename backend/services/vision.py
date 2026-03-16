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
- "document": A photographed document (receipt, invoice, ID, ticket, menu, handwritten note, etc.)
- "accidental": An accidental photo (black, blurry pocket shot, floor, extremely dark/bright, finger over lens)

Respond ONLY with this JSON format, nothing else:
{"category": "photo|screenshot|meme|document|accidental", "confidence": 0.0-1.0}"""


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
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            resp = await self._client.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
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
                headers={"Authorization": f"Bearer {self.api_key}"},
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

        valid_categories = {"photo", "screenshot", "meme", "document", "accidental"}
        if category not in valid_categories:
            logger.warning(f"Unknown category: {category}")
            return None

        return {"category": category, "confidence": confidence}

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(f"Failed to parse: {e}")
        return None
