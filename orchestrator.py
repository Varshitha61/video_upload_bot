"""
orchestrator.py
===============
End-to-end ASMR video automation pipeline.

Pipeline stages:
  1. Generate  → calls Veo 3 via Gemini API to produce N video clips
  2. Stitch    → concatenates clips; exports 16:9 (YouTube) + 9:16 (Instagram)
  3. Upload YT → uploads horizontal cut to YouTube (resumable, OAuth2)
  4. Upload IG → publishes vertical cut as an Instagram Reel (Graph API)

Each network stage is wrapped in try/except so one platform failing does NOT
block the other.

Usage:
    python orchestrator.py \
        --prompt "ASMR wooden box tapping" \
        --title "Relaxing ASMR" \
        --caption "Relaxing ASMR ✨ #asmr #sleep" \
        [--clips 2] \
        [--description "..."] \
        [--tags "asmr,relaxing"] \
        [--privacy private|unlisted|public] \
        [--video-url "https://..."] \
        [--skip-youtube] \
        [--skip-instagram]
"""

import argparse
import logging
import sys
import io
import random
from pathlib import Path

# Fix Windows terminal Unicode encoding issues
if hasattr(sys.stdout, 'buffer') and sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'buffer') and sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from config import cfg
from generate_video import generate_clips
from stitch_video import stitch
from upload_youtube import upload_video
from upload_telegram import upload_telegram
from upload_pinterest import upload_pin

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(cfg.OUTPUT_DIR) / "pipeline.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("orchestrator")

# ---------------------------------------------------------------------------
# Daily Automated Prompts
# ---------------------------------------------------------------------------
RANDOM_PROMPTS = [
    {
        "prompt": "ASMR kinetic sand slicing, soft pastel colors, close up macro shot, highly satisfying",
        "title": "Satisfying Kinetic Sand ASMR 🔪",
        "caption": "Relaxing kinetic sand slicing ✨ #asmr #satisfying #relax"
    },
    {
        "prompt": "ASMR wooden blocks falling and tapping, soft warm lighting, 4K",
        "title": "Relaxing Wooden ASMR 🪵",
        "caption": "Wood tapping and falling sounds ✨ #asmr #sleep #wood"
    },
    {
        "prompt": "ASMR rain drops hitting a glass window, cozy indoor lighting, highly detailed",
        "title": "Cozy Rain ASMR 🌧️",
        "caption": "Rain sounds for deep sleep ✨ #asmr #rain #sleep"
    },
    {
        "prompt": "ASMR soft whispering with a fuzzy microphone, dim studio lighting, relaxing",
        "title": "Deep Sleep Whispering ASMR 🎙️",
        "caption": "Soft whispers to help you sleep ✨ #asmr #whisper #relax"
    },
    {
        "prompt": "ASMR soap cutting into small cubes, bright natural lighting, satisfying macro",
        "title": "Satisfying Soap Cutting ASMR 🧼",
        "caption": "Crisp soap cutting sounds ✨ #asmr #soapcutting #satisfying"
    }
]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    prompt: str,
    title: str,
    caption: str,
    clips: int = 2,
    description: str = "",
    tags: list[str] | None = None,
    privacy: str | None = None,
    video_url: str | None = None,
    skip_youtube: bool = False,
    skip_telegram: bool = False,
    skip_pinterest: bool = False,
) -> dict:
    """
    Run the full generate → stitch → upload pipeline.

    Parameters
    ----------
    prompt        : Veo 3 text prompt for video generation.
    title         : YouTube video title.
    caption       : Instagram Reel caption.
    clips         : Number of Veo clips to generate and stitch together.
    description   : YouTube video description.
    tags          : YouTube tags list.
    privacy       : YouTube privacy status (private/unlisted/public).
    video_url     : Pre-hosted public URL for Instagram (if provided, skip
                    local file hosting concerns — must be publicly reachable).
    skip_youtube  : If True, skip the YouTube upload stage.
    skip_telegram : If True, skip the Telegram upload stage.

    Returns
    -------
    dict with keys: clips, youtube_path, instagram_path,
                    youtube_video_id (or None), instagram_media_id (or None),
                    pinterest_pin_id (or None), errors (list of stage error strings).
    """
    results: dict = {
        "clips": [],
        "youtube_path": None,
        "telegram_path": None,
        "youtube_video_id": None,
        "telegram_message_id": None,
        "pinterest_pin_id": None,
        "errors": [],
    }

    # ════════════════════════════════════════════════════════════════════════
    # Stage 1: Generate video clips
    # ════════════════════════════════════════════════════════════════════════
    logger.info("═══════════════════════════════════")
    logger.info("STAGE 1 — VIDEO GENERATION")
    logger.info("  prompt : %r", prompt)
    logger.info("  clips  : %d", clips)
    logger.info("═══════════════════════════════════")

    try:
        clip_paths = generate_clips(prompt=prompt, count=clips)
        results["clips"] = [str(p) for p in clip_paths]
        logger.info("Stage 1 complete. Generated %d clip(s).", len(clip_paths))
    except Exception as exc:
        msg = f"Stage 1 FAILED (video generation): {exc}"
        logger.error(msg, exc_info=True)
        results["errors"].append(msg)
        logger.error("Cannot continue without clips. Exiting pipeline.")
        return results

    # ════════════════════════════════════════════════════════════════════════
    # Stage 2: Stitch clips → platform cuts
    # ════════════════════════════════════════════════════════════════════════
    logger.info("═══════════════════════════════════")
    logger.info("STAGE 2 — STITCH & EXPORT")
    logger.info("═══════════════════════════════════")

    try:
        yt_path, ig_path = stitch(clip_paths)
        results["youtube_path"]   = str(yt_path)
        results["telegram_path"] = str(ig_path)
        logger.info("Stage 2 complete.")
        logger.info("  YouTube  → %s", yt_path)
        logger.info("  Telegram → %s", ig_path)
    except Exception as exc:
        msg = f"Stage 2 FAILED (stitching): {exc}"
        logger.error(msg, exc_info=True)
        results["errors"].append(msg)
        logger.error("Cannot upload without stitched files. Exiting pipeline.")
        return results

    # ════════════════════════════════════════════════════════════════════════
    # Stage 3: Upload to YouTube
    # ════════════════════════════════════════════════════════════════════════
    if skip_youtube:
        logger.info("Stage 3 (YouTube) — SKIPPED via --skip-youtube flag.")
    else:
        logger.info("═══════════════════════════════════")
        logger.info("STAGE 3 — UPLOAD TO YOUTUBE")
        logger.info("  title   : %r", title)
        logger.info("  privacy : %s", privacy or cfg.YOUTUBE_PRIVACY_STATUS)
        logger.info("═══════════════════════════════════")

        try:
            video_id = upload_video(
                file_path=yt_path,
                title=title,
                description=description,
                tags=tags or [],
                privacy_status=privacy,
            )
            results["youtube_video_id"] = video_id
            logger.info(
                "Stage 3 complete. YouTube video: https://youtu.be/%s", video_id
            )
        except Exception as exc:
            msg = f"Stage 3 FAILED (YouTube upload): {exc}"
            logger.error(msg, exc_info=True)
            results["errors"].append(msg)
            logger.warning("YouTube upload failed — continuing to Instagram stage.")

    # ════════════════════════════════════════════════════════════════════════
    # Stage 4: Upload to Telegram
    # ════════════════════════════════════════════════════════════════════════
    if skip_telegram:
        logger.info("Stage 4 (Telegram) — SKIPPED via --skip-telegram flag.")
    else:
        logger.info("═══════════════════════════════════")
        logger.info("STAGE 4 — UPLOAD TO TELEGRAM")
        logger.info("═══════════════════════════════════")

        try:
            res = upload_telegram(file_path=ig_path, caption=caption)
            results["telegram_message_id"] = res.get("result", {}).get("message_id")
            logger.info(
                "Stage 4 complete. Telegram message_id: %s", results["telegram_message_id"]
            )
        except Exception as exc:
            msg = f"Stage 4 FAILED (Telegram upload): {exc}"
            logger.error(msg, exc_info=True)
            results["errors"].append(msg)
            logger.warning(
                "Telegram upload failed. YouTube result (if any) is unaffected."
            )

    # ════════════════════════════════════════════════════════════════════════
    # Stage 5: Upload to Pinterest
    # ════════════════════════════════════════════════════════════════════════
    if skip_pinterest:
        logger.info("Stage 5 (Pinterest) — SKIPPED via --skip-pinterest flag.")
    else:
        logger.info("═══════════════════════════════════")
        logger.info("STAGE 5 — UPLOAD TO PINTEREST")
        logger.info("═══════════════════════════════════")

        try:
            pin_id = upload_pin(
                file_path=ig_path,  # Use the 9:16 vertical cut for Pinterest
                title=title,
                description=description or caption,
            )
            results["pinterest_pin_id"] = pin_id
            logger.info("Stage 5 complete. Pinterest Pin ID: %s", pin_id)
        except Exception as exc:
            msg = f"Stage 5 FAILED (Pinterest upload): {exc}"
            logger.error(msg, exc_info=True)
            results["errors"].append(msg)
            logger.warning("Pinterest upload failed.")

    # ════════════════════════════════════════════════════════════════════════
    # Summary
    # ════════════════════════════════════════════════════════════════════════
    logger.info("═══════════════════════════════════")
    logger.info("PIPELINE SUMMARY")
    logger.info("  Clips generated   : %d", len(results["clips"]))
    logger.info("  YouTube video ID  : %s", results["youtube_video_id"] or "N/A")
    logger.info("  Telegram msg ID   : %s", results["telegram_message_id"] or "N/A")
    logger.info("  Pinterest Pin ID  : %s", results["pinterest_pin_id"] or "N/A")
    if results["errors"]:
        logger.warning("  Errors (%d):", len(results["errors"]))
        for e in results["errors"]:
            logger.warning("    • %s", e)
    else:
        logger.info("  All stages completed successfully ✓")
    logger.info("═══════════════════════════════════")

    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "ASMR video automation pipeline: generate → stitch → upload.\n"
            "Generates ASMR video clips via Veo 3, stitches them, and "
            "uploads to YouTube and Telegram."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate and upload to both platforms:
  python orchestrator.py \\
      --prompt "ASMR wooden box tapping, close-up, soft lighting" \\
      --title "Relaxing ASMR 🪵" \\
      --caption "Relaxing ASMR ✨ #asmr #sleep #relax"

  # Generate + YouTube only, 3 clips:
  python orchestrator.py \\
      --prompt "ASMR rain sounds on a wooden window" \\
      --title "Rain ASMR 🌧️" \\
      --caption "" \\
      --clips 3 \\
      --skip-telegram

  # Use an existing video (skip generation):
  # Re-run just the upload stages by running upload_youtube.py / upload_telegram.py directly.
""",
    )

    parser.add_argument(
        "--prompt",
        default=None,
        help=(
            'Veo 3 text prompt for the ASMR video. '
            'Example: "ASMR wooden box tapping, soft candlelight, 4K"'
        ),
    )
    parser.add_argument(
        "--title",
        default=None,
        help="YouTube video title (max 100 characters).",
    )
    parser.add_argument(
        "--caption",
        default=None,
        help='Telegram caption (also used as Pinterest description).',
    )
    parser.add_argument(
        "--clips",
        type=int,
        default=2,
        help="Number of Veo 3 clips to generate and stitch (default: 2).",
    )
    parser.add_argument(
        "--description",
        default="",
        help="YouTube video description (max 5,000 characters).",
    )
    parser.add_argument(
        "--tags",
        default="asmr,relaxing,sleep,satisfying",
        help='Comma-separated YouTube tags (default: "asmr,relaxing,sleep,satisfying").',
    )
    parser.add_argument(
        "--privacy",
        default=None,
        choices=["private", "unlisted", "public"],
        help=(
            "YouTube privacy status. "
            "Defaults to YOUTUBE_PRIVACY_STATUS from .env (default: private)."
        ),
    )
    parser.add_argument(
        "--skip-youtube",
        action="store_true",
        help="Skip the YouTube upload stage.",
    )
    parser.add_argument(
        "--skip-telegram",
        action="store_true",
        help="Skip the Telegram upload stage.",
    )
    parser.add_argument(
        "--skip-pinterest",
        action="store_true",
        help="Skip the Pinterest upload stage.",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    # Pick a random predefined prompt if not fully provided
    if not args.prompt or not args.title or not args.caption:
        selected = random.choice(RANDOM_PROMPTS)
        args.prompt = args.prompt or selected["prompt"]
        args.title = args.title or selected["title"]
        args.caption = args.caption or selected["caption"]
        logger.info(f"Using random prompt: {args.title}")

    tag_list = [t.strip() for t in args.tags.split(",") if t.strip()]

    results = run_pipeline(
        prompt=args.prompt,
        title=args.title,
        caption=args.caption,
        clips=args.clips,
        description=args.description,
        tags=tag_list,
        privacy=args.privacy,
        skip_youtube=args.skip_youtube,
        skip_telegram=args.skip_telegram,
        skip_pinterest=args.skip_pinterest,
    )

    # Exit with non-zero code if any stage had errors (useful for CI/scripts)
    if results["errors"]:
        sys.exit(1)
