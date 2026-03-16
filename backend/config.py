from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # Database
    database_url: str = "sqlite+aiosqlite:///./data/photo_cleaner.db"

    # Server
    host: str = "0.0.0.0"
    port: int = 8090

    # Default LLM endpoint (LM Studio OpenAI-compatible)
    default_llm_url: str = "http://100.127.43.94:1234/v1"
    default_model: str = "qwen3-vl-8b-instruct"

    # NAS paths (Docker volume mounts)
    homes_mount: str = "/data/homes"
    cleanup_suffix: str = "_cleanup"

    # Known NAS users
    nas_users: list[str] = ["jhonsnake", "Kelly Cristancho"]

    # Pipeline defaults
    default_blur_threshold: float = 50.0
    default_hash_threshold: int = 8
    default_darkness_threshold: float = 15.0
    default_brightness_threshold: float = 245.0
    default_confidence_threshold: float = 0.7
    default_max_image_size: int = 512

    # Thumbnails
    thumbnail_size: int = 300
    thumbnail_dir: str = "./data/thumbnails"

    class Config:
        env_file = ".env"
        env_prefix = "CLEANER_"


settings = Settings()
