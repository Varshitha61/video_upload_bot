"""
generate_video.py
=================
Generates / fetches ASMR video clips using FREE sources:

  PRIMARY   → Pexels Video API (stock ASMR footage, free, 200 req/hr)
  OPTIONAL  → Hugging Face diffusers text-to-video (local GPU required)

Strategy (controlled by GENERATION_MODE in .env):
  "pexels"    → Search Pexels for videos matching the prompt keywords
  "huggingface" → Generate locally via HF diffusers (requires CUDA GPU)
  "auto"      → Try HF first if GPU available, fall back to Pexels

Pexels API docs:     https://www.pexels.com/api/documentation/#videos-search
HF diffusers docs:   https://huggingface.co/docs/diffusers/api/pipelines/text_to_video

Usage (standalone):
    python generate_video.py --prompt "ASMR wooden box tapping" --count 2
    python generate_video.py --prompt "ASMR rain sounds" --count 2 --mode pexels
    python generate_video.py --prompt "ASMR sand texture" --count 1 --mode huggingface
"""

import argparse
import logging
import random
import re
from pathlib import Path

import requests

from config import cfg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"

# HuggingFace model for text-to-video (runs LOCALLY — needs GPU + ~8GB VRAM)
# Other good free models to try:
#   "ali-vilab/text-to-video-ms-1.7b"
#   "cerspense/zeroscope_v2_576w"   ← lighter, faster, lower VRAM
# Docs: https://huggingface.co/models?pipeline_tag=text-to-video
HF_DEFAULT_MODEL = "cerspense/zeroscope_v2_576w"

# Preferred Pexels video quality (hd → sd → lowest available)
_PREFERRED_QUALITIES = ["hd", "sd", "uhd"]

# Max clips to download per Pexels search to avoid burning rate limit
_PEXELS_PER_PAGE = 10


# ---------------------------------------------------------------------------
# Pexels helpers
# ---------------------------------------------------------------------------

def _check_pexels_key() -> None:
    if not cfg.PEXELS_API_KEY:
        raise EnvironmentError(
            "PEXELS_API_KEY is not set in your .env file.\n"
            "  → Get a free key at: https://www.pexels.com/api/\n"
            "  → Add it to .env: PEXELS_API_KEY=your_key_here"
        )


def _prompt_to_pexels_query(prompt: str) -> str:
    """
    Extract the most relevant keywords from a prompt for Pexels search.
    Removes filler words and keeps the ASMR-relevant nouns/adjectives.
    """
    # Strip common non-searchable words
    stopwords = {
        "a", "an", "the", "and", "or", "of", "in", "on", "with", "for",
        "close-up", "close", "up", "shot", "4k", "hd", "cinematic",
        "soft", "gentle", "slow", "beautiful", "amazing"
    }
    words = re.sub(r"[^\w\s]", " ", prompt.lower()).split()
    keywords = [w for w in words if w not in stopwords and len(w) > 2]

    # Limit to 4 keywords for best Pexels results
    query = " ".join(keywords[:4]) if keywords else prompt
    logger.debug("Pexels query from prompt %r → %r", prompt, query)
    return query


def _pick_best_video_file(video_files: list[dict]) -> dict | None:
    """Pick the best quality video file from a Pexels video_files list."""
    for quality in _PREFERRED_QUALITIES:
        for vf in video_files:
            if vf.get("quality") == quality:
                return vf
    # Fall back to first available
    return video_files[0] if video_files else None


def _download_file(url: str, dest: Path) -> Path:
    """Stream-download a file from *url* to *dest*."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading → %s", dest.name)

    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)

    logger.info("Saved: %s (%.1f MB)", dest, dest.stat().st_size / 1024 / 1024)
    return dest


def fetch_from_pexels(
    prompt: str,
    count: int,
    output_dir: Path,
) -> list[Path]:
    """
    Search Pexels for videos matching *prompt* and download *count* of them.

    Each call picks a random page of results so you get different videos on
    every run, even for the same prompt.

    Pexels rate limits:
      - 200 requests / hour
      - 20,000 requests / month
    Docs: https://www.pexels.com/api/documentation/#videos-search

    Returns list of downloaded file paths.
    """
    _check_pexels_key()

    query = _prompt_to_pexels_query(prompt)

    # Randomise the page so each run fetches a different set of videos
    random_page = random.randint(1, 5)
    logger.info("Pexels search: query=%r, count=%d, page=%d", query, count, random_page)

    headers = {"Authorization": cfg.PEXELS_API_KEY}
    params = {
        "query": query,
        "per_page": min(_PEXELS_PER_PAGE, 80),   # Pexels max per_page = 80
        "page": random_page,
        "orientation": "landscape",               # 16:9 for horizontal source
        "size": "medium",                         # small | medium | large
    }

    resp = requests.get(PEXELS_VIDEO_SEARCH_URL, headers=headers, params=params, timeout=15)

    if resp.status_code == 401:
        raise EnvironmentError(
            "Pexels API returned 401 Unauthorized.\n"
            "  → Regenerate your key at: https://www.pexels.com/api/\n"
            "  → Update PEXELS_API_KEY in your .env"
        )

    # If the random page is empty (beyond total pages), fall back to page 1
    if resp.status_code == 200:
        data = resp.json()
        videos = data.get("videos", [])
        if not videos and random_page > 1:
            logger.info("Page %d returned no results — falling back to page 1.", random_page)
            params["page"] = 1
            resp = requests.get(PEXELS_VIDEO_SEARCH_URL, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            videos = data.get("videos", [])
    else:
        resp.raise_for_status()
        data = resp.json()
        videos = data.get("videos", [])

    if not videos:
        raise RuntimeError(
            f"Pexels returned no results for query: {query!r}\n"
            "  Try a simpler prompt (e.g. 'rain', 'sand', 'wood texture')."
        )

    # Shuffle so repeated runs with the same page also vary clip order
    random.shuffle(videos)
    logger.info("Pexels found %d video(s). Downloading %d…", len(videos), count)

    paths: list[Path] = []
    for i, video in enumerate(videos[:count], start=1):
        vf = _pick_best_video_file(video.get("video_files", []))
        if not vf:
            logger.warning("Video %d has no downloadable files — skipping.", i)
            continue

        video_url = vf["link"]
        # Use the Pexels video ID in the filename to avoid overwriting previous clips
        pexels_id = video.get("id", i)
        dest = output_dir / f"clip_{pexels_id}.mp4"

        try:
            path = _download_file(video_url, dest)
            paths.append(path)
        except Exception as exc:
            logger.warning("Failed to download clip %d: %s", i, exc)

    if not paths:
        raise RuntimeError(
            "All Pexels downloads failed. Check your internet connection."
        )

    logger.info("Pexels: downloaded %d clip(s).", len(paths))
    return paths


# ---------------------------------------------------------------------------
# Hugging Face (local diffusers) helpers
# ---------------------------------------------------------------------------

def _check_gpu() -> bool:
    """Return True if a CUDA GPU is available."""
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def generate_from_huggingface(
    prompt: str,
    count: int,
    output_dir: Path,
    model_id: str | None = None,
) -> list[Path]:
    """
    Generate video clips locally using Hugging Face diffusers.

    Requirements:
      - NVIDIA GPU with ≥8GB VRAM (CUDA)
      - pip install torch diffusers accelerate transformers

    Recommended free models (lighter first):
      "cerspense/zeroscope_v2_576w"        ← ~4GB VRAM, fast
      "ali-vilab/text-to-video-ms-1.7b"    ← ~8GB VRAM, better quality
    Model list: https://huggingface.co/models?pipeline_tag=text-to-video

    NOTE: First run downloads model weights (~3–7GB). Subsequent runs use cache.
    """
    try:
        import torch
        from diffusers import DiffusionPipeline, DPMSolverMultistepScheduler
        from diffusers.utils import export_to_video
    except ImportError as exc:
        raise ImportError(
            "Hugging Face diffusers is not installed.\n"
            "  Run: pip install torch diffusers accelerate transformers\n"
            "  If you don't have a GPU, use --mode pexels instead."
        ) from exc

    if not _check_gpu():
        raise RuntimeError(
            "No CUDA GPU detected. Hugging Face text-to-video requires a GPU.\n"
            "  → Use --mode pexels for the free no-GPU alternative."
        )

    model_id = model_id or cfg.HF_MODEL or HF_DEFAULT_MODEL
    logger.info("Loading HF model: %s (first run downloads ~3-7GB)…", model_id)

    pipe = DiffusionPipeline.from_pretrained(
        model_id,
        torch_dtype=torch.float16,
    )
    # Memory optimisations for lower VRAM
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
    pipe.enable_model_cpu_offload()
    pipe.enable_vae_slicing()

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for i in range(1, count + 1):
        logger.info("Generating clip %d/%d via HuggingFace…", i, count)
        result = pipe(
            prompt,
            num_inference_steps=40,
            num_frames=24,          # ~1 second at 24fps; increase for longer clips
        )
        frames = result.frames[0]

        dest = output_dir / f"clip_{i:03d}.mp4"
        export_to_video(frames, str(dest), fps=8)
        logger.info("HF clip saved: %s", dest)
        paths.append(dest)

    return paths


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_clips(
    prompt: str,
    count: int = 2,
    mode: str | None = None,
) -> list[Path]:
    """
    Generate or fetch *count* video clips using the configured mode.

    Parameters
    ----------
    prompt : Text description (used as Pexels search keywords or HF prompt).
    count  : Number of clips to produce.
    mode   : "pexels" | "huggingface" | "auto"
             Defaults to GENERATION_MODE in .env (default: "pexels").

    Returns list of Paths to downloaded/generated MP4 files.
    """
    mode = (mode or cfg.GENERATION_MODE).lower()
    output_dir = Path(cfg.CLIPS_DIR)

    if mode == "pexels":
        return fetch_from_pexels(prompt, count, output_dir)

    elif mode == "huggingface":
        return generate_from_huggingface(prompt, count, output_dir)

    elif mode == "auto":
        if _check_gpu():
            logger.info("Auto mode: GPU detected → using HuggingFace")
            try:
                return generate_from_huggingface(prompt, count, output_dir)
            except Exception as exc:
                logger.warning("HF generation failed (%s) — falling back to Pexels.", exc)
        else:
            logger.info("Auto mode: No GPU → using Pexels")
        return fetch_from_pexels(prompt, count, output_dir)

    else:
        raise ValueError(
            f"Unknown GENERATION_MODE: {mode!r}\n"
            "  Valid options: 'pexels' | 'huggingface' | 'auto'"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import io

    # Fix Windows terminal Unicode encoding issues
    if hasattr(sys.stdout, 'buffer') and sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'buffer') and sys.stderr.encoding != 'utf-8':
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Fetch/generate ASMR video clips via Pexels or HuggingFace."
    )
    parser.add_argument(
        "--prompt",
        required=True,
        help='ASMR scene description (e.g. "ASMR wooden box tapping")',
    )
    parser.add_argument(
        "--count",
        type=int,
        default=2,
        help="Number of clips to fetch/generate (default: 2)",
    )
    parser.add_argument(
        "--mode",
        choices=["pexels", "huggingface", "auto"],
        default=None,
        help="Generation mode (overrides GENERATION_MODE in .env)",
    )
    args = parser.parse_args()

    paths = generate_clips(
        prompt=args.prompt,
        count=args.count,
        mode=args.mode,
    )
    print(f"\n✅ {len(paths)} clip(s) ready:")
    for p in paths:
        print(f"   {p}")
