"""Backend: renders wallpapers on Windows via mpv.exe + the WorkerW trick.

Mirrors backends/mpvpaper.py's shape closely — same "spawn a renderer,
track its PID, always stop-then-start to avoid orphans" model — swapping
Linux's wlr-layer-shell surface for a window parented behind the desktop
icons (see _windows_wallpaper_host.py for how that window is acquired).
Static images skip mpv entirely and go through the plain Win32
SPI_SETDESKWALLPAPER call, which every Windows version supports natively.

NOTE: this backend has not been tested on real Windows (no Windows machine
was available during development) — it's built from documented Win32 APIs
and the same technique Wallpaper Engine/Lively Wallpaper use, but needs
real-Windows validation before being relied on. See the project plan for
exactly which parts need that validation.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import ClassVar

from livewall.backends.base import BackendApplyError, BackendUnavailableError, WallpaperBackend
from livewall.backends.registry import register
from livewall.config import CACHE_DIR

logger = logging.getLogger(__name__)

MPV_BIN = "mpv"
STATE_FILE = CACHE_DIR / "windows_mpv_state.json"
IPC_PIPE = r"\\.\pipe\livewall-mpv"
_HOST_SCRIPT = Path(__file__).parent / "_windows_wallpaper_host.py"

# gifs loop fine under mpv's own loop-file handling, same as the Linux backend.
_LOOPING_EXTENSIONS = {".mp4", ".webm", ".mkv", ".gif"}
_STATIC_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}
_MPV_OPTS_LOOPING = "loop-file=inf no-audio load-scripts=no"

_HOST_STARTUP_TIMEOUT_SECONDS = 5.0
_MPV_STARTUP_CHECK_SECONDS = 0.4
_STOP_GRACE_SECONDS = 1.0
_STOP_POLL_INTERVAL = 0.1
_IPC_TIMEOUT_SECONDS = 2.0

# SPI_SETDESKWALLPAPER / SPIF_UPDATEINIFILE|SPIF_SENDCHANGE — unchanged since
# Windows Vista, still the correct API on Windows 10/11.
_SPI_SETDESKWALLPAPER = 20
_SPIF_UPDATE_AND_SEND = 3


def _pid_alive(pid: int) -> bool:
    """Whether a PID is a live process, checked via OpenProcess +
    GetExitCodeProcess rather than /proc (which doesn't exist on Windows)."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _terminate(pid: int) -> None:
    # Windows has no SIGTERM/SIGKILL distinction the way POSIX does — Python
    # emulates os.kill(pid, SIGTERM) on Windows by calling TerminateProcess
    # directly, so there's no graceful-then-forceful escalation to do here,
    # unlike the Linux mpvpaper backend's SIGTERM-then-SIGKILL dance.
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


@register
class WindowsMpvBackend(WallpaperBackend):
    name: ClassVar[str] = "windows-mpv"

    supports_video = True
    supports_static_images = True
    supports_audio = True
    supports_multi_monitor = False  # v1 targets the primary monitor only
    supports_pause = True
    supports_resume = True
    supports_restart = False
    supports_thumbnail_refresh = False
    supports_boot_fix = False
    restores_on_login = False  # uses the existing restore-on-boot mechanism

    def is_available(self) -> bool:
        if sys.platform != "win32":
            return False
        return self._mpv_path() is not None

    def _mpv_path(self) -> str | None:
        found = shutil.which(MPV_BIN)
        if found:
            return found
        # When frozen via PyInstaller, a bundled mpv.exe ships next to the
        # packaged app (see the packaging spec) rather than relying on PATH.
        bundled_dir = getattr(sys, "_MEIPASS", None)
        if bundled_dir:
            bundled = Path(bundled_dir) / "mpv.exe"
            if bundled.exists():
                return str(bundled)
        return None

    def _read_state(self) -> dict | None:
        try:
            return json.loads(STATE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def _write_state(self, *, host_pid: int | None, mpv_pid: int | None, path: Path, static: bool) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({
            "host_pid": host_pid, "mpv_pid": mpv_pid, "path": str(path), "static": static,
        }))

    def _clear_state(self) -> None:
        STATE_FILE.unlink(missing_ok=True)

    def is_running(self) -> bool:
        state = self._read_state()
        if state is None or state.get("static"):
            return False
        return _pid_alive(state["mpv_pid"])

    def current_path(self) -> Path | None:
        state = self._read_state()
        if state is None:
            return None
        if not state.get("static") and not _pid_alive(state["mpv_pid"]):
            # Died, was killed externally, or a fresh boot — report nothing
            # currently rendering but leave the state file alone (needed by
            # last_applied_path() for restore-on-boot), same reasoning as
            # the Linux mpvpaper backend.
            return None
        return Path(state["path"])

    def last_applied_path(self) -> Path | None:
        state = self._read_state()
        return Path(state["path"]) if state is not None else None

    def stop(self) -> None:
        state = self._read_state()
        if state is None:
            return
        for key in ("host_pid", "mpv_pid"):
            pid = state.get(key)
            if pid is not None and _pid_alive(pid):
                _terminate(pid)
        if state.get("host_pid") or state.get("mpv_pid"):
            deadline = time.monotonic() + _STOP_GRACE_SECONDS
            while time.monotonic() < deadline and any(
                state.get(k) and _pid_alive(state[k]) for k in ("host_pid", "mpv_pid")
            ):
                time.sleep(_STOP_POLL_INTERVAL)
        self._clear_state()

    def set_wallpaper(self, path: Path, *, no_smart: bool = False) -> None:
        # no_smart (Material You recolour opt-out) is a caelestia-aw-only
        # concept — ignored here, same as on the Linux mpvpaper backend.
        mpv = self._mpv_path()
        if mpv is None:
            raise BackendUnavailableError("'mpv' is not on PATH")
        if not path.exists():
            raise FileNotFoundError(f"Wallpaper file missing: {path}")

        # Always stop whatever was previously tracked first — guarantees no
        # orphaned host/mpv processes across repeated switches.
        self.stop()

        if path.suffix.lower() not in _LOOPING_EXTENSIONS:
            self._set_static(path)
            return

        host_pid, hwnd = self._start_host()
        opts = f"{_MPV_OPTS_LOOPING} input-ipc-server={IPC_PIPE}"
        cmd = [mpv, f"--wid={hwnd}", "-o", opts, str(path)]
        logger.info("Applying via mpv: %s", " ".join(cmd))
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        except OSError as exc:
            _terminate(host_pid)
            raise BackendApplyError(f"Failed to launch mpv: {exc}") from exc

        time.sleep(_MPV_STARTUP_CHECK_SECONDS)
        if proc.poll() is not None:
            stderr = proc.stderr.read() if proc.stderr else ""
            _terminate(host_pid)
            raise BackendApplyError(stderr.strip() or f"mpv exited immediately (code {proc.returncode})")

        self._write_state(host_pid=host_pid, mpv_pid=proc.pid, path=path, static=False)

    def _start_host(self) -> tuple[int, int]:
        """Spawns the wallpaper host and reads back the HWND it acquired
        behind the desktop icons. A frozen (PyInstaller) build has no
        standalone _windows_wallpaper_host.py file to spawn once everything
        is bundled into one .exe, so it re-invokes itself with the hidden
        --wallpaper-host flag instead (see gui_qt/app.py); a dev/source run
        spawns the plain script directly."""
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--wallpaper-host"]
        else:
            cmd = [sys.executable, str(_HOST_SCRIPT)]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError as exc:
            raise BackendApplyError(f"Failed to launch the wallpaper host: {exc}") from exc

        deadline = time.monotonic() + _HOST_STARTUP_TIMEOUT_SECONDS
        line = ""
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                stderr = proc.stderr.read() if proc.stderr else ""
                raise BackendApplyError(stderr.strip() or "wallpaper host exited before reporting a window")
            line = proc.stdout.readline().strip() if proc.stdout else ""
            if line:
                break
        if not line or not line.isdigit():
            _terminate(proc.pid)
            raise BackendApplyError("wallpaper host did not report a window handle in time")
        return proc.pid, int(line)

    def _set_static(self, path: Path) -> None:
        image_path = path
        if path.suffix.lower() not in _STATIC_EXTENSIONS:
            raise BackendApplyError(f"Unsupported static image format: {path.suffix}")
        ok = ctypes.windll.user32.SystemParametersInfoW(
            _SPI_SETDESKWALLPAPER, 0, str(image_path), _SPIF_UPDATE_AND_SEND
        )
        if not ok:
            raise BackendApplyError("SystemParametersInfoW failed to set the wallpaper")
        self._write_state(host_pid=None, mpv_pid=None, path=path, static=True)

    def _mpv_ipc(self, command: list) -> dict | None:
        """Sends one JSON IPC command over mpv's named pipe and returns its
        reply, or None if nothing's running / the pipe isn't reachable —
        callers treat that as "nothing to do", not an error, same as the
        Linux backend's _mpv_ipc(). A Windows named pipe can be opened via
        the plain builtin open() in r+b mode — no pywin32 needed."""
        if not self.is_running():
            return None
        try:
            with open(IPC_PIPE, "r+b", buffering=0) as pipe:
                pipe.write((json.dumps({"command": command}) + "\n").encode())
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = pipe.read(4096)
                    if not chunk:
                        break
                    buf += chunk
        except OSError as exc:
            logger.warning("mpv IPC command %s failed: %s", command, exc)
            return None
        try:
            return json.loads(buf)
        except json.JSONDecodeError:
            return None

    def pause(self) -> None:
        self._mpv_ipc(["set_property", "pause", True])

    def resume(self) -> None:
        self._mpv_ipc(["set_property", "pause", False])

    def is_paused(self) -> bool | None:
        response = self._mpv_ipc(["get_property", "pause"])
        if response is None or "data" not in response:
            return None
        return bool(response["data"])

    def health_check(self) -> list[tuple[str, bool, str]]:
        checks: list[tuple[str, bool, str]] = []

        available = self.is_available()
        checks.append(("mpv CLI", available, "found" if available else "'mpv' not found on PATH or bundled"))

        running = self.is_running()
        checks.append(("wallpaper process running", running, "tracked process alive" if running else "not currently running (or a static image is set)"))

        current = self.current_path()
        if current is None:
            checks.append(("current wallpaper", True, "none applied yet"))
        elif not current.exists():
            checks.append(("current wallpaper", False, f"tracked file missing: {current}"))
        else:
            checks.append(("current wallpaper", True, str(current)))

        return checks
