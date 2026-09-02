"""
upload_youtube.py
=================
Uploads a video to YouTube using the YouTube Data API v3 with OAuth2
authentication and resumable (chunked) upload.

OAuth2 flow:
  - First run: opens a browser window to authorise your Google account.
    The token is saved to YOUTUBE_TOKEN_PATH (default: youtube_token.json).
  - Subsequent runs: token is loaded from disk and auto-refreshed if expired.

Quota cost:
  - videos.insert costs ~1,600 units per upload.
  - Default daily quota: 10,000 units (~6 uploads/day).
  - Quota increase request: https://console.cloud.google.com/iam-admin/quotas

Usage (standalone):
    python upload_youtube.py \
        --file output/youtube_horizontal.mp4 \
        --title "Relaxing ASMR" \
        --description "Soothing ASMR sounds" \
        --tags "asmr,relaxing,sleep" \
        --privacy private

Relevant docs:
  - videos.insert:   https://developers.google.com/youtube/v3/docs/videos/insert
  - OAuth2 setup:    https://developers.google.com/youtube/v3/quickstart/python
  - Scopes:          https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps
  - Quota:           https://developers.google.com/youtube/v3/getting-started#quota
"""

import argparse
import json
import logging
import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from config import cfg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OAuth2 scopes
# ---------------------------------------------------------------------------
_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Resumable upload chunk size: 5 MB (must be a multiple of 256 KB)
_CHUNK_SIZE = 5 * 1024 * 1024

# Retry settings for transient errors (5xx / network failures)
_MAX_RETRIES = 5
_RETRY_INITIAL_DELAY_S = 5
_RETRY_BACKOFF_FACTOR = 2


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _load_or_create_credentials() -> Credentials:
    """
    Load cached OAuth2 credentials or run the browser-based auth flow.

    Token is persisted to YOUTUBE_TOKEN_PATH so future runs skip the browser.
    Handles token refresh automatically; falls back to browser flow only if
    the refresh_token is missing or the refresh itself fails.
    """
    token_path = Path(cfg.YOUTUBE_TOKEN_PATH)
    creds: Credentials | None = None

    if token_path.exists():
        logger.info("Loading cached YouTube token from %s", token_path)
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
            logger.info(
                "Token loaded. valid=%s expired=%s has_refresh_token=%s",
                creds.valid,
                creds.expired,
                bool(creds.refresh_token),
            )
        except Exception as exc:
            logger.warning("Failed to load token file (%s) — will re-authenticate.", exc)
            creds = None

    # ── Try to refresh if expired ─────────────────────────────────────────────
    if creds and not creds.valid:
        if creds.expired and creds.refresh_token:
            logger.info("Token is expired — attempting automatic refresh…")
            try:
                creds.refresh(Request())
                logger.info("Token refreshed successfully.")
                # Persist the refreshed token immediately
                token_path.write_text(creds.to_json(), encoding="utf-8")
                logger.info("Refreshed token saved to %s", token_path)
                return creds
            except Exception as exc:
                logger.warning(
                    "Token refresh failed (%s) — will run browser OAuth flow.", exc
                )
                creds = None  # force browser re-auth
        else:
            logger.warning(
                "Token is invalid and cannot be refreshed "
                "(expired=%s, has_refresh_token=%s). Running browser auth flow.",
                creds.expired,
                bool(creds.refresh_token),
            )
            creds = None

    # ── Full browser OAuth flow (first run or after refresh failure) ──────────
    if not creds or not creds.valid:
        secret_path = Path(cfg.YOUTUBE_CLIENT_SECRET_PATH)
        if not secret_path.exists():
            raise FileNotFoundError(
                f"YouTube OAuth2 client secret not found at: {secret_path}\n"
                "  → Download it from Google Cloud Console:\n"
                "    https://console.cloud.google.com/apis/credentials\n"
                "  → Set YOUTUBE_CLIENT_SECRET_PATH in your .env file."
            )
        logger.info(
            "Running YouTube OAuth2 browser flow. "
            "A browser window will open — please authorise the app and return here."
        )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(secret_path), _SCOPES
        )
        creds = flow.run_local_server(port=0)

        # Persist the new token
        token_path.write_text(creds.to_json(), encoding="utf-8")
        logger.info("New token saved to %s", token_path)

    return creds


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload_video(
    file_path: Path,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    category_id: str | None = None,
    privacy_status: str | None = None,
) -> str:
    """
    Upload *file_path* to YouTube and return the new video ID.

    Parameters
    ----------
    file_path      : Path to the video file (16:9 MP4 recommended).
    title          : YouTube video title (max 100 chars).
    description    : Video description (max 5,000 chars).
    tags           : List of tag strings.
    category_id    : YouTube category ID string. Default: cfg.YOUTUBE_CATEGORY_ID.
    privacy_status : "private" | "unlisted" | "public". Default: cfg.YOUTUBE_PRIVACY_STATUS.

    Returns
    -------
    The YouTube video ID (e.g. "dQw4w9WgXcQ").

    Raises
    ------
    RuntimeError on unrecoverable upload errors.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Video file not found: {file_path}")

    category_id    = category_id    or cfg.YOUTUBE_CATEGORY_ID
    privacy_status = privacy_status or cfg.YOUTUBE_PRIVACY_STATUS
    tags           = tags           or []

    creds   = _load_or_create_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    # ── Build request body ────────────────────────────────────────────────
    body: dict = {
        "snippet": {
            "title": title[:100],          # YouTube enforces 100-char limit
            "description": description,
            "tags": tags,
            "categoryId": category_id,
            # defaultLanguage / defaultAudioLanguage can be added here
        },
        "status": {
            "privacyStatus": privacy_status,
            # selfDeclaredMadeForKids: set False for general audience
            "selfDeclaredMadeForKids": False,
        },
    }

    # ── AI content disclosure ─────────────────────────────────────────────
    if cfg.AI_CONTENT_DISCLOSURE:
        # TODO: Verify the exact field name and location for AI-generated content
        # disclosure with YouTube Data API v3. The field changes over time.
        # As of 2024-2025, Meta and YouTube added "AI-generated" labels, but
        # the API-level field name for YouTube is under active development.
        # Check current field support at:
        #   https://developers.google.com/youtube/v3/docs/videos/insert
        # Possible future field:
        #   body["status"]["containsSyntheticMedia"] = True
        logger.warning(
            "AI_CONTENT_DISCLOSURE is enabled, but the YouTube Data API v3 "
            "field for AI disclosure is not yet stable. "
            "See: https://developers.google.com/youtube/v3/docs/videos/insert\n"
            "Proceeding without setting the disclosure field."
        )

    # ── Resumable upload ──────────────────────────────────────────────────
    media = MediaFileUpload(
        str(file_path),
        mimetype="video/*",
        chunksize=_CHUNK_SIZE,
        resumable=True,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    logger.info(
        "Starting YouTube upload: %s (%.1f MB) → title=%r privacy=%s",
        file_path,
        file_path.stat().st_size / 1024 / 1024,
        title,
        privacy_status,
    )

    # ── Chunked upload loop with retry ────────────────────────────────────
    response = None
    retry_delay = _RETRY_INITIAL_DELAY_S

    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                logger.info("Upload progress: %d%%", pct)
        except HttpError as exc:
            if exc.resp.status in (500, 502, 503, 504) and _MAX_RETRIES > 0:
                logger.warning(
                    "Transient HTTP %s — retrying in %ds…",
                    exc.resp.status, retry_delay,
                )
                time.sleep(retry_delay)
                retry_delay = min(
                    retry_delay * _RETRY_BACKOFF_FACTOR, 120
                )
            else:
                raise RuntimeError(
                    f"YouTube upload failed with HTTP {exc.resp.status}: {exc}"
                ) from exc

    video_id: str = response.get("id", "")
    logger.info(
        "YouTube upload complete! Video ID: %s | URL: https://youtu.be/%s",
        video_id, video_id,
    )
    return video_id


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Upload a video to YouTube via Data API v3."
    )
    parser.add_argument("--file",        required=True, type=Path, help="Path to the video file")
    parser.add_argument("--title",       required=True,             help="Video title")
    parser.add_argument("--description", default="",                help="Video description")
    parser.add_argument("--tags",        default="",                help="Comma-separated tags")
    parser.add_argument(
        "--privacy",
        default=cfg.YOUTUBE_PRIVACY_STATUS,
        choices=["private", "unlisted", "public"],
    )
    args = parser.parse_args()

    vid_id = upload_video(
        file_path=args.file,
        title=args.title,
        description=args.description,
        tags=[t.strip() for t in args.tags.split(",") if t.strip()],
        privacy_status=args.privacy,
    )
    print(f"\nYouTube video ID : {vid_id}")
    print(f"Watch URL        : https://youtu.be/{vid_id}")
