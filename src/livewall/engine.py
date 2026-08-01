"""Backend: applies wallpapers by shelling out to caelestia-aw's own CLI.

caelestia-aw (https://github.com/AdiAmbassador/caelestia-aw) patches Caelestia's
Quickshell process to render mp4/webm/mkv/gif wallpapers natively. It owns
rendering, thumbnailing, Material You theming, and restore-on-login (its shell
watches the state file below and reapplies on change). This module is just a
thin, honest wrapper around its CLI and state file — LiveWall never renders a
wallpaper itself.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from livewall.database import Wallpaper

logger = logging.getLogger(__name__)

CAELESTIA_BIN = "caelestia"
MPV_BIN = "mpv"
EXTRACT_THUMBS_TIMEOUT = 120
APPLY_TIMEOUT = 30

_state_dir = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state"))
CURRENT_WALLPAPER_STATE = _state_dir / "caelestia" / "wallpaper" / "path.txt"


class CaelestiaNotAvailableError(RuntimeError):
    """Raised when the ``caelestia`` CLI isn't on PATH."""


class ApplyError(RuntimeError):
    """Raised when ``caelestia wallpaper -f`` fails."""


def is_available() -> bool:
    return shutil.which(CAELESTIA_BIN) is not None


def supports_animated() -> bool:
    """Whether the running ``caelestia`` CLI has the caelestia-aw patch applied."""
    if not is_available():
        return False
    try:
        result = subprocess.run(
            [CAELESTIA_BIN, "wallpaper", "--help"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return "--extract-thumbs" in result.stdout


def current_path() -> Path | None:
    """The wallpaper path Caelestia currently has applied, per its own state file."""
    try:
        text = CURRENT_WALLPAPER_STATE.read_text().strip()
    except OSError:
        return None
    return Path(text) if text else None


def apply(wallpaper: Wallpaper, no_smart: bool = False) -> None:
    if not is_available():
        raise CaelestiaNotAvailableError("'caelestia' is not on PATH")

    path = wallpaper.file_path
    if not path.exists():
        raise FileNotFoundError(f"Wallpaper file missing: {path}")

    cmd = [CAELESTIA_BIN, "wallpaper", "-f", str(path)]
    if no_smart:
        cmd.append("--no-smart")

    logger.info("Applying via caelestia-aw: %s", " ".join(cmd))
    try:
        subprocess.run(
            cmd, capture_output=True, text=True, timeout=APPLY_TIMEOUT, check=True
        )
    except subprocess.CalledProcessError as exc:
        raise ApplyError(exc.stderr.strip() or f"caelestia exited {exc.returncode}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ApplyError("caelestia wallpaper timed out") from exc


def refresh_thumbnails() -> None:
    """Ask caelestia-aw to (re)generate its own video thumbnail cache."""
    if not is_available():
        raise CaelestiaNotAvailableError("'caelestia' is not on PATH")
    try:
        subprocess.run(
            [CAELESTIA_BIN, "wallpaper", "--extract-thumbs"],
            capture_output=True, text=True, timeout=EXTRACT_THUMBS_TIMEOUT, check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ApplyError(exc.stderr.strip() or "thumbnail extraction failed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ApplyError("thumbnail extraction timed out") from exc


def preview(path: Path, blocking: bool = True) -> subprocess.Popen | None:
    """Open a wallpaper in a normal mpv window — unrelated to caelestia-aw."""
    if shutil.which(MPV_BIN) is None:
        raise CaelestiaNotAvailableError("mpv is not installed")
    cmd = [MPV_BIN, "--loop-file=inf", str(path)]
    if blocking:
        subprocess.run(cmd)
        return None
    return subprocess.Popen(cmd, start_new_session=True)
