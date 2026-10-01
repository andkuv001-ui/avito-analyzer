import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DEFAULT_BASE_URL = "https://routerai.ru/api/v1"
DEFAULT_MODEL = "openai/gpt-4o-mini"
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "180"))


def get_api_key() -> str | None:
    key = os.getenv("ROUTERAI_API_KEY", "").strip()
    return key or None


def get_base_url() -> str:
    return os.getenv("ROUTERAI_BASE_URL", "").strip() or DEFAULT_BASE_URL


def get_model(override: str | None = None) -> str:
    if override and override.strip():
        return override.strip()
    return os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODEL


def mask_key(key: str | None) -> str:
    if not key:
        return "не задан"
    if len(key) <= 8:
        return "задан"
    return f"задан ({key[:3]}…{key[-4:]})"
