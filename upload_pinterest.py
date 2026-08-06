"""
upload_pinterest.py
===================
Uploads a video as a Video Pin to Pinterest using the Pinterest API v5.

Publishing workflow:
  1. POST /v5/media        → Registers video upload, returns media_id & upload_url
  2. POST {upload_url}     → Uploads the actual video file via multipart/form-data
  3. GET  /v5/media/{id}   → Polls status until 'succeeded'
  4. POST /v5/pins         → Creates the Pin using the media_id

Relevant docs:
  - https://developers.pinterest.com/docs/api/v5/
"""

import argparse
import logging
import time
from typing import Any
import os

import requests

from config import cfg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_BASE_URL = "https://api.pinterest.com/v5"

# Polling settings for media processing status
_POLL_INITIAL_DELAY_S = 10
_POLL_BACKOFF_FACTOR = 1.5
_POLL_MAX_DELAY_S = 30
_POLL_MAX_ATTEMPTS = 20

_STATUS_SUCCEEDED = "succeeded"
_STATUS_FAILED    = "failed"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _check_pin_config() -> None:
    """Raise if required Pinterest credentials are not set."""
    missing = []
    if not cfg.PINTEREST_ACCESS_TOKEN:
        missing.append("PINTEREST_ACCESS_TOKEN")
    if not cfg.PINTEREST_BOARD_ID:
        missing.append("PINTEREST_BOARD_ID")
    if missing:
        raise EnvironmentError(
            f"Missing Pinterest credentials: {', '.join(missing)}\n"
            "  → Set them in your .env file."
        )


def _pin_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.PINTEREST_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Core steps
# ---------------------------------------------------------------------------
def _register_media_upload() -> tuple[str, str, dict[str, str]]:
    """Step 1: Register video upload."""
    logger.info("Registering Pinterest media upload...")
    
    url = f"{_BASE_URL}/media"
    payload = {"media_type": "video"}
    
    resp = requests.post(url, headers=_pin_headers(), json=payload, timeout=30)
    
    try:
        data = resp.json()
    except Exception:
        resp.raise_for_status()
        data = {}

    if "code" in data and "message" in data:
        raise RuntimeError(f"Pinterest API Error: {data['message']}")
        
    resp.raise_for_status()
    
    media_id = data.get("media_id", "")
    upload_url = data.get("upload_url", "")
    upload_parameters = data.get("upload_parameters", {})
    
    if not media_id or not upload_url:
        raise RuntimeError(f"Failed to register media upload. Response: {data}")
        
    logger.info("Upload registered. media_id=%s", media_id)
    return media_id, upload_url, upload_parameters


def _upload_video_file(file_path: str, upload_url: str, upload_parameters: dict[str, str]) -> None:
    """Step 2: Upload the video file."""
    logger.info("Uploading video file to Pinterest's AWS bucket...")
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Video file not found: {file_path}")
        
    # Pinterest requires sending the parameters as form data along with the file.
    # The file MUST be the last field in the form data.
    with open(file_path, "rb") as f:
        files = {"file": f}
        # Requests automatically sets multipart/form-data when using 'files'
        resp = requests.post(upload_url, data=upload_parameters, files=files, timeout=300)
        
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"Video file upload failed (HTTP {resp.status_code}): {resp.text}")
        
    logger.info("Video file uploaded successfully.")


def _poll_media_status(media_id: str) -> None:
    """Step 3: Poll until processing is complete."""
    logger.info("Polling media status for %s...", media_id)
    
    url = f"{_BASE_URL}/media/{media_id}"
    headers = {"Authorization": f"Bearer {cfg.PINTEREST_ACCESS_TOKEN}"}
    
    delay = _POLL_INITIAL_DELAY_S
    for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
        logger.info("  Attempt %d/%d — waiting %ds...", attempt, _POLL_MAX_ATTEMPTS, int(delay))
        time.sleep(delay)
        
        resp = requests.get(url, headers=headers, timeout=30)
        try:
            data = resp.json()
        except Exception:
            resp.raise_for_status()
            data = {}
            
        status = data.get("status", "")
        logger.info("  status=%s", status)
        
        if status == _STATUS_SUCCEEDED:
            logger.info("Media processing complete.")
            return
            
        if status == _STATUS_FAILED:
            raise RuntimeError(f"Pinterest media processing failed. Response: {data}")
            
        delay = min(delay * _POLL_BACKOFF_FACTOR, _POLL_MAX_DELAY_S)
        
    raise RuntimeError(f"Media {media_id} did not finish processing in time.")


def _create_pin(media_id: str, title: str, description: str, link: str = "") -> str:
    """Step 4: Create the Pin."""
    logger.info("Creating Pin...")
    
    url = f"{_BASE_URL}/pins"
    payload: dict[str, Any] = {
        "board_id": cfg.PINTEREST_BOARD_ID,
        "title": title[:100],  # Pinterest title max 100 chars
        "description": description[:500],  # Pinterest description max 500 chars
        "media_source": {
            "source_type": "video_id",
            "media_id": media_id,
        }
    }
    
    if link:
        payload["link"] = link
        
    resp = requests.post(url, headers=_pin_headers(), json=payload, timeout=30)
    
    try:
        data = resp.json()
    except Exception:
        resp.raise_for_status()
        data = {}
        
    if "code" in data and "message" in data:
        raise RuntimeError(f"Pinterest API Error: {data['message']}")
        
    resp.raise_for_status()
    
    pin_id = data.get("id", "")
    if not pin_id:
        raise RuntimeError(f"Failed to create Pin. Response: {data}")
        
    logger.info("Pin created! pin_id=%s", pin_id)
    return pin_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def upload_pin(
    file_path: str,
    title: str,
    description: str,
    link: str = "",
) -> str:
    """
    Upload a local video file as a Pinterest Video Pin.
    
    Returns the generated Pin ID.
    """
    _check_pin_config()
    
    # 1. Register media
    media_id, upload_url, upload_parameters = _register_media_upload()
    
    # 2. Upload video
    _upload_video_file(file_path, upload_url, upload_parameters)
    
    # 3. Poll status
    _poll_media_status(media_id)
    
    # 4. Create pin
    pin_id = _create_pin(media_id, title=title, description=description, link=link)
    
    return pin_id


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    
    parser = argparse.ArgumentParser(description="Upload a video Pin to Pinterest via API v5.")
    parser.add_argument("--file", required=True, help="Local path to the video file.")
    parser.add_argument("--title", required=True, help="Pin title (max 100 chars).")
    parser.add_argument("--description", required=True, help="Pin description (max 500 chars).")
    parser.add_argument("--link", default="", help="Optional destination link for the Pin.")
    args = parser.parse_args()
    
    pid = upload_pin(
        file_path=args.file,
        title=args.title,
        description=args.description,
        link=args.link,
    )
    print(f"\nPinterest Pin ID: {pid}")
