"""
upload_telegram.py
===================
Uploads a video to Telegram via the Telegram Bot API.
"""

import argparse
import logging
from pathlib import Path
from typing import Any

import requests

from config import cfg

logger = logging.getLogger(__name__)


def _check_telegram_config() -> None:
    missing = []
    if not cfg.TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not cfg.TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise EnvironmentError(
            f"Missing Telegram credentials: {', '.join(missing)}\n"
            "  → Set them in your .env file.\n"
        )


def upload_telegram(file_path: str, caption: str) -> dict[str, Any]:
    """
    Uploads a video file to a Telegram chat.
    """
    _check_telegram_config()
    
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {file_path}")

    logger.info("Uploading video to Telegram…")
    
    url = f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendVideo"
    
    with open(path, "rb") as f:
        files = {"video": f}
        data = {
            "chat_id": cfg.TELEGRAM_CHAT_ID,
            "caption": caption
        }
        
        resp = requests.post(url, data=data, files=files, timeout=300)
    
    try:
        data_json = resp.json()
    except Exception:
        resp.raise_for_status()
        data_json = {}

    if not data_json.get("ok"):
        raise RuntimeError(f"Telegram API error: {data_json}")

    logger.info("Video successfully uploaded to Telegram.")
    return data_json


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Upload a video to Telegram.")
    parser.add_argument("--file-path", required=True)
    parser.add_argument("--caption", required=True)
    args = parser.parse_args()
    
    result = upload_telegram(args.file_path, args.caption)
    print(f"\nTelegram upload result: {result.get('ok')}")
