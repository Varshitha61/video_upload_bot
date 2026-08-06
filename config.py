"""
config.py
=========
Loads all secrets and settings from a .env file (via python-dotenv).
No credentials are ever hardcoded here.

Usage:
    from config import cfg
    print(cfg.PEXELS_API_KEY)
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from the project root
# ---------------------------------------------------------------------------
_ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require(key: str) -> str:
    """Return the env var value, or exit with a helpful message if missing."""
    value = os.getenv(key)
    if not value:
        print(
            f"[config] ERROR: Required environment variable '{key}' is not set.\n"
            f"  → Copy .env.example to .env and fill in the value.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Config:

    # ── Generation mode ───────────────────────────────────────────────────
    # "pexels"      → fetch free stock ASMR footage from Pexels (default)
    # "huggingface" → generate locally via HF diffusers (needs CUDA GPU)
    # "auto"        → HF if GPU detected, else Pexels
    GENERATION_MODE: str = field(
        default_factory=lambda: _optional("GENERATION_MODE", "pexels")
    )

    # ── Pexels (primary free video source) ───────────────────────────────
    # Free API key from https://www.pexels.com/api/
    # Rate limits: 200 requests/hour, 20,000/month
    PEXELS_API_KEY: str = field(
        default_factory=lambda: _optional("PEXELS_API_KEY", "")
    )

    # ── HuggingFace (optional, local GPU only) ────────────────────────────
    # Free token from https://huggingface.co/settings/tokens
    # Only needed if GENERATION_MODE=huggingface
    HF_TOKEN: str = field(
        default_factory=lambda: _optional("HF_TOKEN", "")
    )

    # HF text-to-video model (local diffusers, requires GPU)
    # Lighter:  "cerspense/zeroscope_v2_576w"     (~4GB VRAM)
    # Better:   "ali-vilab/text-to-video-ms-1.7b" (~8GB VRAM)
    # List: https://huggingface.co/models?pipeline_tag=text-to-video
    HF_MODEL: str = field(
        default_factory=lambda: _optional(
            "HF_MODEL", "cerspense/zeroscope_v2_576w"
        )
    )

    # ── YouTube ───────────────────────────────────────────────────────────
    YOUTUBE_CLIENT_SECRET_PATH: str = field(
        default_factory=lambda: _optional(
            "YOUTUBE_CLIENT_SECRET_PATH", "client_secret.json"
        )
    )
    YOUTUBE_TOKEN_PATH: str = field(
        default_factory=lambda: _optional("YOUTUBE_TOKEN_PATH", "youtube_token.json")
    )
    # "22" = People & Blogs
    # Full list: https://developers.google.com/youtube/v3/docs/videoCategories/list
    YOUTUBE_CATEGORY_ID: str = field(
        default_factory=lambda: _optional("YOUTUBE_CATEGORY_ID", "22")
    )
    # Options: "private" | "unlisted" | "public"
    YOUTUBE_PRIVACY_STATUS: str = field(
        default_factory=lambda: _optional("YOUTUBE_PRIVACY_STATUS", "private")
    )

    # ── Telegram ─────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = field(default_factory=lambda: _optional("TELEGRAM_BOT_TOKEN", ""))
    TELEGRAM_CHAT_ID: str = field(default_factory=lambda: _optional("TELEGRAM_CHAT_ID", ""))

    # ── Pinterest ─────────────────────────────────────────────────────────
    PINTEREST_ACCESS_TOKEN: str = field(
        default_factory=lambda: _optional("PINTEREST_ACCESS_TOKEN", "")
    )
    PINTEREST_BOARD_ID: str = field(
        default_factory=lambda: _optional("PINTEREST_BOARD_ID", "")
    )

    # ── AI Content Disclosure ─────────────────────────────────────────────
    AI_CONTENT_DISCLOSURE: bool = field(
        default_factory=lambda: _optional(
            "AI_CONTENT_DISCLOSURE", "true"
        ).lower() == "true"
    )

    # ── Output directories ────────────────────────────────────────────────
    OUTPUT_DIR: str = field(
        default_factory=lambda: _optional("OUTPUT_DIR", "output")
    )
    CLIPS_DIR: str = field(
        default_factory=lambda: _optional("CLIPS_DIR", "output/clips")
    )


# Singleton — import this everywhere
cfg = Config()

# Ensure output directories exist at startup
Path(cfg.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
Path(cfg.CLIPS_DIR).mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    print("=== Config loaded ===")
    print(f"  GENERATION_MODE   : {cfg.GENERATION_MODE}")
    print(f"  PEXELS_API_KEY    : {'[SET]' if cfg.PEXELS_API_KEY else '[MISSING]'}")
    print(f"  HF_TOKEN          : {'[SET]' if cfg.HF_TOKEN else '[not set — optional]'}")
    print(f"  HF_MODEL          : {cfg.HF_MODEL}")
    print(f"  YOUTUBE_TOKEN_PATH: {cfg.YOUTUBE_TOKEN_PATH}")
    print(f"  AI_DISCLOSURE     : {cfg.AI_CONTENT_DISCLOSURE}")
    print(f"  OUTPUT_DIR        : {cfg.OUTPUT_DIR}")
    print(f"  CLIPS_DIR         : {cfg.CLIPS_DIR}")
    print(f"  TELEGRAM_BOT      : {'[SET]' if cfg.TELEGRAM_BOT_TOKEN else '[not set — optional]'}")
    print(f"  TELEGRAM_CHAT     : {'[SET]' if cfg.TELEGRAM_CHAT_ID else '[not set — optional]'}")
    print(f"  PIN_ACCESS_TOKEN  : {'[SET]' if cfg.PINTEREST_ACCESS_TOKEN else '[not set — optional]'}")
    print(f"  PIN_BOARD_ID      : {'[SET]' if cfg.PINTEREST_BOARD_ID else '[not set — optional]'}")
