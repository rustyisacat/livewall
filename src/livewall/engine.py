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
import time
from pathlib import Path

from livewall.database import Wallpaper

logger = logging.getLogger(__name__)

CAELESTIA_BIN = "caelestia"
MPV_BIN = "mpv"
EXTRACT_THUMBS_TIMEOUT = 120
APPLY_TIMEOUT = 30

# Only these actually get continuous QtMultimedia decode — gif/static images
# have nothing to sample, so ensure_playing() can't verify them meaningfully.
_TRUE_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv"}
_DECODE_SAMPLE_SECONDS = 0.6

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


def apply_path(path: Path, no_smart: bool = False) -> None:
    """Apply any file (video or image) by path — not just a library entry."""
    if not is_available():
        raise CaelestiaNotAvailableError("'caelestia' is not on PATH")

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


def apply(wallpaper: Wallpaper, no_smart: bool = False) -> None:
    apply_path(wallpaper.file_path, no_smart=no_smart)


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


def restart_shell() -> None:
    """Restart the Caelestia shell.

    Works around a caelestia-aw bug where WallpaperPauser's QSettings backing
    store sometimes fails to initialize on startup ("Failed to initialize
    QSettings instance"), silently pinning pauseOnWindowOverlap/pauseOnBattery
    to their QML defaults regardless of what's saved in Nexus settings — which
    freezes video wallpapers. A clean restart forces the singleton to
    re-initialize from the actual saved config.
    """
    if not is_available():
        raise CaelestiaNotAvailableError("'caelestia' is not on PATH")
    subprocess.run([CAELESTIA_BIN, "shell", "-k"], capture_output=True, text=True, timeout=10)
    time.sleep(1)
    subprocess.run([CAELESTIA_BIN, "shell", "-d"], capture_output=True, text=True, timeout=10)


def _qs_pid() -> int | None:
    result = subprocess.run(["pgrep", "-x", "qs"], capture_output=True, text=True, timeout=5)
    pids = result.stdout.split()
    return int(pids[0]) if pids else None


def _cpu_ticks(pid: int) -> int:
    with open(f"/proc/{pid}/stat") as f:
        parts = f.read().split()
    return int(parts[13]) + int(parts[14])


def ensure_playing() -> str:
    """Restart the shell only if the current video wallpaper isn't actually decoding.

    A cheap, targeted alternative to always calling restart_shell(): most boots
    don't hit the QSettings init flakiness at all, so paying a full shell
    kill+restart every time is needless (and slow). This samples decode CPU
    briefly first and only restarts when that comes back genuinely idle.
    """
    current = current_path()
    if current is None:
        return "no wallpaper applied, nothing to check"
    if current.suffix.lower() not in _TRUE_VIDEO_EXTENSIONS:
        return f"current wallpaper ({current.suffix}) isn't a video, nothing to verify"

    pid = _qs_pid()
    if pid is None:
        subprocess.run([CAELESTIA_BIN, "shell", "-d"], capture_output=True, text=True, timeout=10)
        return "shell wasn't running — started it"

    try:
        before = _cpu_ticks(pid)
        time.sleep(_DECODE_SAMPLE_SECONDS)
        after = _cpu_ticks(pid)
    except (OSError, ValueError, IndexError):
        return "could not sample decode activity — leaving the shell alone"

    if after > before:
        return "already decoding — no action needed"

    restart_shell()
    return "was paused/frozen on startup — restarted the shell to fix it"


def preview(path: Path, blocking: bool = True) -> subprocess.Popen | None:
    """Open a wallpaper in a normal mpv window — unrelated to caelestia-aw."""
    if shutil.which(MPV_BIN) is None:
        raise CaelestiaNotAvailableError("mpv is not installed")
    cmd = [MPV_BIN, "--loop-file=inf", str(path)]
    if blocking:
        subprocess.run(cmd)
        return None
    return subprocess.Popen(cmd, start_new_session=True)
