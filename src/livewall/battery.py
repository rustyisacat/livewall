"""Battery-percentage wallpaper saver — independent of AC/charging status.

caelestia-aw's own WallpaperPauser only pauses based on whether you're on AC
power at all, with no percentage threshold, and exposes no pause/resume IPC
for wallpapers (only `list/set/get` — confirmed via `caelestia shell -s`).
So there's no real "pause" primitive to call into.

What we *can* do, and what actually matters for battery: switch to a static
frame of the current wallpaper when the battery drops low (video decode is
the expensive part, a static image is nearly free), and switch back once it
recovers. Hysteresis (a low and a high threshold) avoids flapping right at
one cutoff.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from livewall import engine
from livewall.config import CACHE_DIR, ensure_dirs
from livewall.utils import sha256_file

logger = logging.getLogger(__name__)

STATE_FILE = CACHE_DIR / "battery_saver_state.json"
FRAME_CACHE_DIR = CACHE_DIR / "battery_frames"
FRAME_EXTRACT_TIMEOUT = 15


def battery_percent() -> int | None:
    """The first battery's charge percentage, or None if there isn't one."""
    for capacity_file in sorted(Path("/sys/class/power_supply").glob("BAT*/capacity")):
        try:
            return int(capacity_file.read_text().strip())
        except (OSError, ValueError):
            continue
    return None


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"active": False, "saved_path": None}


def _save_state(state: dict) -> None:
    ensure_dirs()
    STATE_FILE.write_text(json.dumps(state))


def _static_frame_for(video_path: Path) -> Path | None:
    """A full-resolution single frame from video_path, cached by content hash.

    caelestia-aw's own wallpaper thumbnail (~/.local/state/caelestia/wallpaper/
    thumbnail.jpg) is only 128x72 — built for Material You colour extraction,
    not for display. Applying it as the actual background renders as
    effectively black once the shell's mask/blur effects hit that few pixels.
    Extracting our own frame at the source's native resolution avoids that.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None

    FRAME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = sha256_file(video_path)
    out_path = FRAME_CACHE_DIR / f"{key}.jpg"
    if out_path.exists():
        return out_path

    cmd = [ffmpeg, "-y", "-v", "error", "-i", str(video_path), "-frames:v", "1", str(out_path)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=FRAME_EXTRACT_TIMEOUT, check=True)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Frame extraction failed for %s: %s", video_path, exc)
        out_path.unlink(missing_ok=True)
        return None

    return out_path if out_path.exists() else None


def check(low: int, high: int) -> str:
    """Run one check against the current battery level. Returns a status string."""
    percent = battery_percent()
    if percent is None:
        return "no battery detected — nothing to do"

    state = _load_state()

    if not state.get("active") and percent <= low:
        current = engine.current_path()
        if current is None:
            return f"battery at {percent}% (<= {low}%), but no wallpaper is currently applied"
        if not current.exists():
            return f"battery at {percent}% (<= {low}%), but '{current}' no longer exists"

        frame = _static_frame_for(current)
        if frame is None:
            return f"battery at {percent}% (<= {low}%), but couldn't extract a static frame"

        engine.apply_path(frame, no_smart=True)
        _save_state({"active": True, "saved_path": str(current)})
        return f"battery at {percent}% <= {low}% — switched to a static frame (saved '{current.name}')"

    if state.get("active") and percent >= high:
        saved_path = state.get("saved_path")
        if saved_path and Path(saved_path).exists():
            engine.apply_path(Path(saved_path))
            _save_state({"active": False, "saved_path": None})
            return f"battery at {percent}% >= {high}% — restored '{Path(saved_path).name}'"
        _save_state({"active": False, "saved_path": None})
        return f"battery at {percent}% >= {high}%, but the saved wallpaper is gone — cleared saver state"

    status = "active (static fallback)" if state.get("active") else "inactive"
    return f"battery at {percent}% — no change ({status})"
