"""ffprobe-based metadata probing and ffmpeg-based thumbnail generation.

Deliberately avoids Pillow: ffmpeg alone can resize both video frames and
still images, so it's the only external tool needed for this module.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from livewall.config import THUMBNAIL_DIR, ensure_dirs
from livewall.utils import is_animated

logger = logging.getLogger(__name__)

THUMBNAIL_SIZE = 320
PROBE_TIMEOUT = 15
THUMBNAIL_TIMEOUT = 20


class ToolNotFoundError(RuntimeError):
    """Raised when ffmpeg/ffprobe is not on PATH."""


def _find(binary: str) -> str:
    path = shutil.which(binary)
    if path is None:
        raise ToolNotFoundError(f"'{binary}' not found on PATH")
    return path


@dataclass
class Metadata:
    width: int | None
    height: int | None
    duration: float | None
    animated: bool
    aspect_ratio: str | None

    @property
    def resolution(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return "unknown"


def _aspect_ratio(width: int, height: int) -> str:
    divisor = math.gcd(width, height) or 1
    return f"{width // divisor}:{height // divisor}"


def probe(path: Path) -> Metadata:
    """Inspect a wallpaper file with ffprobe and return its technical metadata."""
    try:
        ffprobe = _find("ffprobe")
    except ToolNotFoundError:
        logger.warning("ffprobe not available; returning empty metadata for %s", path)
        return Metadata(None, None, None, is_animated(path), None)

    cmd = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT, check=True
        )
        data = json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
        logger.warning("ffprobe failed for %s: %s", path, exc)
        return Metadata(None, None, None, is_animated(path), None)

    video_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    stream = video_streams[0] if video_streams else {}

    width = stream.get("width")
    height = stream.get("height")

    duration_raw = stream.get("duration") or data.get("format", {}).get("duration")
    duration = float(duration_raw) if duration_raw is not None else None

    nb_frames_raw = stream.get("nb_frames")
    nb_frames = int(nb_frames_raw) if nb_frames_raw and nb_frames_raw.isdigit() else None

    animated = is_animated(path)
    if path.suffix.lower() == ".gif" and nb_frames is not None:
        animated = nb_frames > 1

    aspect_ratio = _aspect_ratio(width, height) if width and height else None

    return Metadata(width, height, duration, animated, aspect_ratio)


def thumbnail_path(cache_key: str) -> Path:
    return THUMBNAIL_DIR / f"{cache_key}.png"


def generate(path: Path, cache_key: str, metadata: Metadata | None = None) -> Path | None:
    """Generate (or reuse) a cached thumbnail PNG for ``path``. Returns None on failure."""
    ensure_dirs()
    out_path = thumbnail_path(cache_key)
    if out_path.exists():
        return out_path

    try:
        ffmpeg = _find("ffmpeg")
    except ToolNotFoundError:
        logger.warning("ffmpeg not available; skipping thumbnail for %s", path)
        return None

    scale_filter = f"scale={THUMBNAIL_SIZE}:-1:flags=lanczos"

    if metadata and metadata.animated:
        duration = metadata.duration or 0
        seek = min(1.0, duration * 0.1) if duration else 0.0
        vf = f"thumbnail,{scale_filter}" if seek == 0.0 else scale_filter
        cmd = [ffmpeg, "-y", "-v", "error"]
        if seek > 0:
            cmd += ["-ss", f"{seek:.2f}"]
        cmd += ["-i", str(path), "-frames:v", "1", "-vf", vf, str(out_path)]
    else:
        cmd = [
            ffmpeg, "-y", "-v", "error",
            "-i", str(path),
            "-frames:v", "1",
            "-vf", scale_filter,
            str(out_path),
        ]

    try:
        subprocess.run(cmd, capture_output=True, timeout=THUMBNAIL_TIMEOUT, check=True)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Thumbnail generation failed for %s: %s", path, exc)
        out_path.unlink(missing_ok=True)
        return None

    if not out_path.exists():
        return None
    return out_path


def remove_thumbnail(cache_key: str) -> None:
    thumbnail_path(cache_key).unlink(missing_ok=True)
