"""Small stateless helpers: logging setup, hashing, and formatting."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from livewall.config import LOG_FILE, ensure_dirs

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
ANIMATED_EXTENSIONS = {".mp4", ".webm", ".gif"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | ANIMATED_EXTENSIONS

_HASH_CHUNK_SIZE = 1024 * 1024


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure the ``livewall`` logger to write to console and a log file."""
    ensure_dirs()
    logger = logging.getLogger("livewall")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(console)

    try:
        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
        )
        logger.addHandler(file_handler)
    except OSError:
        pass

    return logger


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def is_animated(path: Path) -> bool:
    return path.suffix.lower() in ANIMATED_EXTENSIONS


def sha256_file(path: Path) -> str:
    """Stream a file through sha256 without loading it fully into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def human_duration(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "--:--"
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def unique_name(desired: str, existing: set[str]) -> str:
    """Return ``desired`` unchanged, or suffixed with " (2)", " (3)", ... if taken."""
    if desired not in existing:
        return desired
    n = 2
    while f"{desired} ({n})" in existing:
        n += 1
    return f"{desired} ({n})"
