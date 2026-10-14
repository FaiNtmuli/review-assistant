"""运行参数。任何 OpenAI 兼容接口都能接：改 base_url 和 model 即可。"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


class Settings(BaseModel):
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    temperature: float = 0.0
    timeout: float = 180.0
    max_concurrency: int = 4
    max_rounds: int = 3
    max_comments_per_file: int = 8

    def for_state(self) -> dict[str, Any]:
        """进图的那份不带密钥：检查点会落盘，密钥不该跟着进去。"""
        data = self.model_dump()
        data.pop("api_key", None)
        return data

    @classmethod
    def from_state(cls, data: dict[str, Any] | None) -> "Settings":
        settings = cls(**(data or {}))
        if not settings.api_key:
            settings.api_key = resolve_api_key()
        return settings


def resolve_api_key() -> str:
    return (
        os.getenv("REVIEW_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or "not-needed"
    )


def load_settings(env_file: str | None = None, **overrides: Any) -> Settings:
    load_dotenv(env_file, override=False)
    resolved: dict[str, Any] = {
        "model": os.getenv("REVIEW_MODEL", DEFAULT_MODEL),
        "base_url": os.getenv("REVIEW_BASE_URL", DEFAULT_BASE_URL),
        "api_key": resolve_api_key(),
        "temperature": float(os.getenv("REVIEW_TEMPERATURE", "0")),
        "max_concurrency": int(os.getenv("REVIEW_MAX_CONCURRENCY", "4")),
        "max_rounds": int(os.getenv("REVIEW_MAX_ROUNDS", "3")),
    }
    for key, value in overrides.items():
        if value is not None:
            resolved[key] = value
    return Settings(**resolved)
