"""Environment configuration for the daily Blogger post agent."""

from __future__ import annotations

import dataclasses
import os
import random
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
TOPICS_FILE = PROJECT_ROOT / "topics.json"
POSTED_LOG_FILE = PROJECT_ROOT / "posted_log.json"
EXTERNAL_SOURCES_FILE = PROJECT_ROOT / "external_sources.json"
IMAGES_DIR = PROJECT_ROOT / "images"
LOGS_DIR = PROJECT_ROOT / "logs"

LLM_BASE_URL_DEFAULT = "https://api.x.ai/v1"
PIXABAY_API_URL = "https://pixabay.com/api/"
BLOGGER_SCOPE = "https://www.googleapis.com/auth/blogger"
CDN_TEMPLATE = "https://cdn.jsdelivr.net/gh/{repo}@main/images/{filename}"

DEFAULT_NICHE = "Personal Finance & Money"
DEFAULT_LANGUAGE = "Hinglish"
MIN_WORDS = 800
MAX_WORDS = 1200

_TRUE_VALUES = {"true", "1", "yes", "y", "on"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Add it to your .env file (see .env.example)."
        )
    return value


@dataclass(frozen=True)
class Config:
    """Runtime configuration resolved once at startup."""

    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = LLM_BASE_URL_DEFAULT
    pixabay_api_key: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    blog_id: str = ""
    blog_niche: str = DEFAULT_NICHE
    blog_language: str = DEFAULT_LANGUAGE
    github_repository: str = ""
    site_url: str = ""
    site_name: str = ""
    draft_mode: bool = False
    tone: str = field(default_factory=lambda: random.choice(TONES))

    # Derived
    topic_count: int = 30
    min_words: int = MIN_WORDS
    max_words: int = MAX_WORDS

    @property
    def should_draft(self) -> bool:
        return self.draft_mode

    def with_draft(self, force: bool = True) -> "Config":
        """Return a copy with DRAFT_MODE forced on or off."""
        return dataclasses.replace(self, draft_mode=force)

    def require_publish_credentials(self) -> None:
        missing = [
            name
            for name in (
                "GOOGLE_CLIENT_ID",
                "GOOGLE_CLIENT_SECRET",
                "GOOGLE_REFRESH_TOKEN",
                "BLOG_ID",
            )
            if not os.getenv(name, "").strip()
        ]
        if missing:
            raise RuntimeError(
                "Cannot publish, missing credentials: " + ", ".join(missing)
            )


TONES = [
    "practical and conversational, like a friend explaining it over chai",
    "clear and calm, like a well-organised money diary",
    "encouraging and honest, no hype and no guilt-tripping",
    "simple and to-the-point, a reader who is short on time",
]


def load_config(*, require_all: bool = True) -> Config:
    """Load .env then build the Config.

    require_all=True enforces every variable needed for a live publish.
    --dry-run relaxes this to only the content-generation keys.
    """
    load_dotenv(PROJECT_ROOT / ".env")

    cfg = Config(
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "").strip(),
        llm_base_url=os.getenv("LLM_BASE_URL", "").strip() or LLM_BASE_URL_DEFAULT,
        pixabay_api_key=os.getenv("PIXABAY_API_KEY", "").strip(),
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
        google_refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN", "").strip(),
        blog_id=os.getenv("BLOG_ID", "").strip(),
        blog_niche=os.getenv("BLOG_NICHE", "").strip() or DEFAULT_NICHE,
        blog_language=os.getenv("BLOG_LANGUAGE", "").strip() or DEFAULT_LANGUAGE,
        github_repository=os.getenv("GITHUB_REPOSITORY", "").strip(),
        site_url=os.getenv("SITE_URL", "").strip(),
        site_name=os.getenv("SITE_NAME", "").strip(),
        draft_mode=_env_bool("DRAFT_MODE", False),
    )

    if require_all:
        _require("LLM_API_KEY")
        _require("LLM_MODEL")
        _require("PIXABAY_API_KEY")
        cfg.require_publish_credentials()
    else:
        _require("LLM_API_KEY")
        _require("LLM_MODEL")

    return cfg