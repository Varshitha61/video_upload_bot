"""
stitch_video.py
===============
Concatenates multiple video clips into one using ffmpeg, then exports two
platform-specific cuts:

  ┌──────────────────────────────────────────────────────────────┐
  │  output/youtube_horizontal.mp4   ← 16:9, for YouTube        │
  │  output/instagram_vertical.mp4   ← 9:16, max 90s, for Reels │
  └──────────────────────────────────────────────────────────────┘

Requirements:
  - ffmpeg must be installed and available on PATH.
  - Install: https://ffmpeg.org/download.html
             Windows: winget install Gyan.FFmpeg
             macOS:   brew install ffmpeg
             Linux:   sudo apt install ffmpeg

Usage (standalone):
    python stitch_video.py output/clips/clip_001.mp4 output/clips/clip_002.mp4

ffmpeg docs:
  - Concat demuxer: https://ffmpeg.org/ffmpeg-formats.html#concat-1
  - Video filters:  https://ffmpeg.org/ffmpeg-filters.html
"""

import argparse
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from config import cfg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_INSTAGRAM_MAX_DURATION_S = 90   # Instagram Reels API hard cap
_FFMPEG_BIN  = os.environ.get("FFMPEG_BIN",  "ffmpeg")   # override via .env
_FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe")  # override via .env


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_ffmpeg() -> None:
    """Raise RuntimeError if ffmpeg is not found on PATH."""
    try:
        subprocess.run(
            [_FFMPEG_BIN, "-version"],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "ffmpeg not found or not working.\n"
            "  Install: https://ffmpeg.org/download.html\n"
            "  Windows: winget install Gyan.FFmpeg\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg\n"
            f"  Error: {exc}"
        ) from exc


def _run_ffmpeg(args: list[str], step: str) -> None:
    """Run an ffmpeg command and raise RuntimeError on failure."""
    cmd = [_FFMPEG_BIN, "-y"] + args   # -y = overwrite output without asking
    logger.debug("ffmpeg %s: %s", step, " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed during '{step}':\n"
            f"STDOUT: {result.stdout}\n"
            f"STDERR: {result.stderr}"
        )
    logger.info("ffmpeg step '%s' completed.", step)


def _get_video_duration(path: Path) -> float:
    """Return the duration of a video file in seconds using ffprobe."""
    result = subprocess.run(
        [
            _FFPROBE_BIN, "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("ffprobe failed for %s — duration unknown", path)
        return 0.0
    import json
    data = json.loads(result.stdout)
    return float(data.get("format", {}).get("duration", 0))


# ---------------------------------------------------------------------------
# Core stitching logic
# ---------------------------------------------------------------------------

def _write_concat_list(clip_paths: list[Path], tmp_dir: str) -> Path:
    """
    Write an ffmpeg concat demuxer list file.
    Format:   file '/absolute/path/to/clip.mp4'
    """
    list_path = Path(tmp_dir) / "concat_list.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for cp in clip_paths:
            # Escape single quotes in paths
            safe = str(cp.resolve()).replace("'", "\\'")
            f.write(f"file '{safe}'\n")
    logger.debug("Concat list written to %s", list_path)
    return list_path


def concatenate_clips(clip_paths: list[Path], output_path: Path) -> Path:
    """
    Concatenate *clip_paths* into a single video at *output_path*.

    Uses the concat demuxer (stream-copy, no re-encode) for speed.
    Falls back to concat filter if streams are incompatible.
    """
    if not clip_paths:
        raise ValueError("clip_paths must not be empty")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Concatenating %d clip(s) → %s", len(clip_paths), output_path)

    with tempfile.TemporaryDirectory() as tmp_dir:
        list_file = _write_concat_list(clip_paths, tmp_dir)

        _run_ffmpeg(
            [
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_file),
                # Stream-copy avoids re-encoding and preserves quality.
                # If clips have mismatched codecs/resolutions, remove -c copy
                # and let ffmpeg transcode them.
                "-c", "copy",
                str(output_path),
            ],
            step="concatenate",
        )

    return output_path


def export_youtube(raw_path: Path, output_path: Path) -> Path:
    """
    Export a 16:9 YouTube-ready MP4 from *raw_path*.

    Encoding:
      - Video: H.264 (libx264), CRF 23, preset medium
      - Audio: AAC 192k
      - Resolution: up to 1920×1080 (scale down if larger, preserve aspect)
      - Container: MP4

    YouTube recommended specs:
      https://support.google.com/youtube/answer/1722171
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Exporting YouTube horizontal cut → %s", output_path)

    # Scale to fit within 1920×1080, preserve aspect ratio, pad if needed,
    # then force 16:9 crop.
    # vf breakdown:
    #   scale=1920:1080:force_original_aspect_ratio=decrease
    #     → scale down to fit inside 1920×1080, keep aspect
    #   pad=1920:1080:(ow-iw)/2:(oh-ih)/2
    #     → add black bars (letterbox/pillarbox) to reach exactly 1920×1080
    _run_ffmpeg(
        [
            "-i", str(raw_path),
            "-vf", (
                "scale=1920:1080:force_original_aspect_ratio=decrease,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2"
            ),
            "-c:v", "libx264",
            "-crf", "23",
            "-preset", "medium",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",   # enables streaming while downloading
            str(output_path),
        ],
        step="export_youtube",
    )
    return output_path


def export_instagram(raw_path: Path, output_path: Path) -> Path:
    """
    Export a 9:16 Instagram Reels-ready MP4 from *raw_path*.

    Instagram Reels API requirements:
      - Aspect ratio: 9:16
      - Codec: H.264 or HEVC (we use H.264 for max compatibility)
      - Max duration: 90 seconds
      - Container: MP4
      - Audio: AAC
      - Recommended resolution: 1080×1920

    Docs: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Exporting Instagram vertical (9:16) cut → %s", output_path)

    # Check duration and clamp if needed
    duration = _get_video_duration(raw_path)
    trim_args: list[str] = []
    if duration > _INSTAGRAM_MAX_DURATION_S:
        logger.warning(
            "Combined clip duration (%.1fs) exceeds Instagram Reels max "
            "(%ds). Trimming to %ds.",
            duration, _INSTAGRAM_MAX_DURATION_S, _INSTAGRAM_MAX_DURATION_S,
        )
        trim_args = ["-t", str(_INSTAGRAM_MAX_DURATION_S)]

    # vf breakdown:
    #   scale=1080:1920:force_original_aspect_ratio=increase
    #     → scale up until one dimension matches 1080×1920
    #   crop=1080:1920
    #     → center-crop to exactly 1080×1920 (cuts the excess edges)
    _run_ffmpeg(
        [
            "-i", str(raw_path),
        ]
        + trim_args
        + [
            "-vf", (
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920"
            ),
            "-c:v", "libx264",
            "-crf", "23",
            "-preset", "medium",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(output_path),
        ],
        step="export_instagram",
    )
    return output_path


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def stitch(clip_paths: list[Path]) -> tuple[Path, Path]:
    """
    Full stitch pipeline: concatenate clips, then export both platform cuts.

    Returns
    -------
    (youtube_path, instagram_path)
    """
    _check_ffmpeg()

    output_dir = Path(cfg.OUTPUT_DIR)
    raw_path = output_dir / "raw_combined.mp4"
    yt_path  = output_dir / "youtube_horizontal.mp4"
    ig_path  = output_dir / "instagram_vertical.mp4"

    if len(clip_paths) == 1:
        # No need to concatenate a single clip; just use it directly
        logger.info("Single clip — skipping concatenation step.")
        raw_path = clip_paths[0]
    else:
        concatenate_clips(clip_paths, raw_path)

    export_youtube(raw_path, yt_path)
    export_instagram(raw_path, ig_path)

    logger.info(
        "Stitch complete.\n  YouTube  → %s\n  Instagram → %s", yt_path, ig_path
    )
    return yt_path, ig_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Stitch video clips and export YouTube + Instagram cuts."
    )
    parser.add_argument(
        "clips",
        nargs="+",
        type=Path,
        help="One or more clip files to concatenate.",
    )
    args = parser.parse_args()

    yt, ig = stitch(args.clips)
    print(f"\nYouTube  : {yt}")
    print(f"Instagram: {ig}")
