"""Backend: renders wallpapers via mpvpaper (https://github.com/GhostNaN/mpvpaper).

mpvpaper has no state file or CLI query of its own — LiveWall tracks the PID
and applied path itself so ``current_path()``/``is_running()`` work, and so
switching wallpapers can cleanly stop the previous process before starting
the next one (no orphaned renderers across repeated switches).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import ClassVar

from livewall.backends.base import BackendApplyError, BackendUnavailableError, WallpaperBackend
from livewall.backends.registry import register
from livewall.config import CACHE_DIR

logger = logging.getLogger(__name__)

MPVPAPER_BIN = "mpvpaper"
STATE_FILE = CACHE_DIR / "mpvpaper_state.json"

# mpv handles gifs fine via loop-file, so they get the same looping options as
# real video containers rather than a hardcoded per-extension special case.
_LOOPING_EXTENSIONS = {".mp4", ".webm", ".mkv", ".gif"}
_MPV_OPTS_LOOPING = "loop-file=inf no-audio"
_MPV_OPTS_STATIC = "image-display-duration=inf no-audio"

_STARTUP_CHECK_SECONDS = 0.4
_STOP_GRACE_SECONDS = 1.0
_STOP_POLL_INTERVAL = 0.1


@register
class MpvpaperBackend(WallpaperBackend):
    name: ClassVar[str] = "mpvpaper"

    supports_video = True
    supports_static_images = True
    supports_audio = True
    supports_multi_monitor = True  # same clip on every output via mpvpaper's "ALL" target
    supports_pause = False
    supports_resume = False
    supports_restart = False
    supports_thumbnail_refresh = False
    supports_boot_fix = False

    def is_available(self) -> bool:
        return shutil.which(MPVPAPER_BIN) is not None

    def _read_state(self) -> dict | None:
        try:
            return json.loads(STATE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def _write_state(self, pid: int, path: Path) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"pid": pid, "path": str(path)}))

    def _clear_state(self) -> None:
        STATE_FILE.unlink(missing_ok=True)

    def _pid_alive(self, pid: int) -> bool:
        try:
            return Path(f"/proc/{pid}/comm").read_text().strip() == "mpvpaper"
        except OSError:
            return False

    def is_running(self) -> bool:
        state = self._read_state()
        return state is not None and self._pid_alive(state["pid"])

    def current_path(self) -> Path | None:
        state = self._read_state()
        if state is None:
            return None
        if not self._pid_alive(state["pid"]):
            # Process died or was killed externally — self-heal the stale entry.
            self._clear_state()
            return None
        return Path(state["path"])

    def stop(self) -> None:
        state = self._read_state()
        if state is None:
            return
        pid = state["pid"]
        if self._pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            else:
                deadline = time.monotonic() + _STOP_GRACE_SECONDS
                while time.monotonic() < deadline and self._pid_alive(pid):
                    time.sleep(_STOP_POLL_INTERVAL)
                if self._pid_alive(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
        self._clear_state()

    def set_wallpaper(self, path: Path, *, no_smart: bool = False) -> None:
        # no_smart (Material You recolour opt-out) is a caelestia-aw-only
        # concept — there's no equivalent for mpvpaper, so it's just ignored.
        if not self.is_available():
            raise BackendUnavailableError("'mpvpaper' is not on PATH")
        if not path.exists():
            raise FileNotFoundError(f"Wallpaper file missing: {path}")

        # Always stop whatever was previously tracked before spawning a new
        # process — guarantees no orphaned mpvpaper across repeated switches.
        self.stop()

        opts = _MPV_OPTS_LOOPING if path.suffix.lower() in _LOOPING_EXTENSIONS else _MPV_OPTS_STATIC
        cmd = [MPVPAPER_BIN, "--layer", "background", "-o", opts, "ALL", str(path)]
        logger.info("Applying via mpvpaper: %s", " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, start_new_session=True,
            )
        except OSError as exc:
            raise BackendApplyError(f"Failed to launch mpvpaper: {exc}") from exc

        time.sleep(_STARTUP_CHECK_SECONDS)
        if proc.poll() is not None:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise BackendApplyError(
                stderr.strip() or f"mpvpaper exited immediately (code {proc.returncode})"
            )

        self._write_state(proc.pid, path)

    def health_check(self) -> list[tuple[str, bool, str]]:
        checks: list[tuple[str, bool, str]] = []

        available = self.is_available()
        checks.append(("mpvpaper CLI", available, "found on PATH" if available else "'mpvpaper' not found on PATH"))

        running = self.is_running()
        checks.append(("mpvpaper process running", running, "tracked process alive" if running else "not currently running"))

        current = self.current_path()
        if current is None:
            checks.append(("current wallpaper", True, "none applied yet"))
        elif not current.exists():
            checks.append(("current wallpaper", False, f"tracked file missing: {current}"))
        else:
            checks.append(("current wallpaper", True, str(current)))

        return checks
