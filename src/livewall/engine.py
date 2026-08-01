"""mpvpaper-backed wallpaper engine: apply/stop/preview, one process per monitor."""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from livewall.config import CURRENT_STATE_FILE, Config, ensure_dirs
from livewall.database import Wallpaper

logger = logging.getLogger(__name__)

MPVPAPER_BIN = "mpvpaper"
MPV_BIN = "mpv"
TERMINATE_GRACE_SECONDS = 1.5


class MpvpaperNotFoundError(RuntimeError):
    """Raised when mpvpaper isn't on PATH."""


@dataclass
class EngineState:
    processes: dict[str, int]  # monitor -> pid
    current_wallpaper: str | None

    @classmethod
    def load(cls) -> "EngineState":
        if not CURRENT_STATE_FILE.exists():
            return cls(processes={}, current_wallpaper=None)
        try:
            raw = json.loads(CURRENT_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return cls(processes={}, current_wallpaper=None)
        return cls(
            processes={k: int(v) for k, v in raw.get("processes", {}).items()},
            current_wallpaper=raw.get("current_wallpaper"),
        )

    def save(self) -> None:
        ensure_dirs()
        CURRENT_STATE_FILE.write_text(
            json.dumps({"processes": self.processes, "current_wallpaper": self.current_wallpaper}, indent=2)
        )


def is_installed() -> bool:
    return shutil.which(MPVPAPER_BIN) is not None


def install_command() -> list[str]:
    """The command to install mpvpaper. Run only with the user's explicit go-ahead."""
    return ["sudo", "pacman", "-S", "mpvpaper"]


def install() -> bool:
    """Run the install command interactively (inherits the caller's TTY)."""
    logger.info("Installing mpvpaper via pacman")
    result = subprocess.run(install_command())
    return result.returncode == 0


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _terminate(pid: int) -> None:
    if not _is_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + TERMINATE_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _mpv_options(config: Config) -> str:
    opts = ["loop-file=inf"] if config.loop else []
    if config.mute:
        opts.append("no-audio")
    opts.append("hwdec=auto" if config.hwdec else "hwdec=no")

    if config.scaling == "stretch":
        opts.append("keepaspect=no")
    elif config.scaling == "fill":
        opts += ["keepaspect=yes", "panscan=1.0"]
    else:  # fit
        opts += ["keepaspect=yes", "panscan=0.0"]

    return " ".join(opts)


class WallpaperEngine:
    """Applies wallpapers via mpvpaper, tracking one process per monitor target."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.state = EngineState.load()

    def targets(self, monitor: str | None = None) -> list[str]:
        if monitor:
            return [monitor]
        return list(self.config.monitors) or ["*"]

    def is_running(self, monitor: str = "*") -> bool:
        pid = self.state.processes.get(monitor)
        return pid is not None and _is_alive(pid)

    def stop(self, monitor: str | None = None) -> None:
        """Stop tracked mpvpaper process(es). Stops every tracked monitor if None."""
        keys = [monitor] if monitor else list(self.state.processes.keys())
        for key in keys:
            pid = self.state.processes.pop(key, None)
            if pid is not None:
                logger.info("Stopping mpvpaper on %s (pid %d)", key, pid)
                _terminate(pid)
        self.state.save()

    def force_kill_all(self) -> None:
        """Fallback for orphaned mpvpaper processes we lost track of."""
        subprocess.run(["pkill", "-x", MPVPAPER_BIN], capture_output=True)
        self.state.processes.clear()
        self.state.save()

    def apply(self, wallpaper: Wallpaper, monitor: str | None = None) -> None:
        if not is_installed():
            raise MpvpaperNotFoundError("mpvpaper is not installed")

        path = wallpaper.file_path
        if not path.exists():
            raise FileNotFoundError(f"Wallpaper file missing: {path}")

        mpv_opts = _mpv_options(self.config)

        for target in self.targets(monitor):
            self.stop(target)
            cmd = [MPVPAPER_BIN, "-o", mpv_opts, target, str(path)]
            logger.info("Launching: %s", " ".join(cmd))
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.state.processes[target] = proc.pid

        self.state.current_wallpaper = wallpaper.name
        self.state.save()
        self.config.last_wallpaper = wallpaper.name
        self.config.save()

    def preview(self, path: Path, blocking: bool = True) -> subprocess.Popen | None:
        """Open a wallpaper in a normal mpv window for previewing."""
        if shutil.which(MPV_BIN) is None:
            raise MpvpaperNotFoundError("mpv is not installed")
        cmd = [MPV_BIN, "--loop-file=inf", str(path)]
        if blocking:
            subprocess.run(cmd)
            return None
        return subprocess.Popen(cmd, start_new_session=True)
