"""Backend: renders wallpapers on Windows via mpv.exe + the WorkerW trick.

Mirrors backends/mpvpaper.py's shape closely — same "spawn a renderer,
track its PID, always stop-then-start to avoid orphans" model — swapping
Linux's wlr-layer-shell surface for a window parented behind the desktop
icons (see _windows_wallpaper_host.py for how that window is acquired).
Static images skip mpv entirely and go through the plain Win32
SPI_SETDESKWALLPAPER call, which every Windows version supports natively.

Also attempts per-monitor wallpapers, same as the Linux mpvpaper backend:
_windows_wallpaper_host.py creates one child window per monitor (plus a
top-level one covering every monitor for the mirrored ALL case), and this
backend spawns one mpv.exe per target, each pointed at its own child hwnd.
The host process is shared across every target — spawning a second one
would re-run the undocumented WorkerW handshake and duplicate the whole
window stack — so it's only started once and reused, tracked separately
from each target's own mpv.exe. State is a dict keyed by target ("ALL", or
a monitor's device name), same pattern as the Linux backend.

NOTE: this backend has not been tested on real Windows (no Windows machine
was available during development) — it's built from documented Win32 APIs
and the same technique Wallpaper Engine/Lively Wallpaper use, but needs
real-Windows validation before being relied on. The per-monitor host-reuse
logic in particular is new and untested — a real ctypes mistake already
slipped through once in this same GUI (gui_qt/tray.py, fixed only after
the first real-hardware run), so treat this file with real skepticism
until it's actually been run on Windows. See the project plan for exactly
which parts need that validation.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import ClassVar

from livewall.backends.base import BackendApplyError, BackendUnavailableError, WallpaperBackend
from livewall.backends.registry import register
from livewall.config import CACHE_DIR

logger = logging.getLogger(__name__)

MPV_BIN = "mpv"
STATE_FILE = CACHE_DIR / "windows_mpv_state.json"
ALL_TARGET = "ALL"
_HOST_SCRIPT = Path(__file__).parent / "_windows_wallpaper_host.py"

# gifs loop fine under mpv's own loop-file handling, same as the Linux backend.
_LOOPING_EXTENSIONS = {".mp4", ".webm", ".mkv", ".gif"}
_STATIC_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}
# Real, confirmed-on-actual-Windows-11 bug, found via a genuine test session:
# these used to be joined into one space-separated string and passed as the
# value of a single "-o" flag (`["-o", "loop-file=inf no-audio ..."]`). "-o"
# is mpv's real short form of --o=<file>, the ENCODE-to-file option — mpv
# was silently going into encode mode targeting a file named after that
# whole garbage string, then fatal-erroring ("Encoding initialization
# failed") instead of ever rendering anything. This is very likely the
# actual reason wallpapers "applied successfully" (mpv did spawn, so
# _spawn_mpv() never raised) but nothing ever appeared on screen, moreso
# than the WorkerW-target-window question, which was the other live theory
# at the time this was found — a broken mpv invocation would explain "no
# visible change" regardless of whether the target window was correct.
_MPV_OPTS_LOOPING = ["--loop-file=inf", "--no-audio", "--load-scripts=no"]

_HOST_STARTUP_TIMEOUT_SECONDS = 5.0
_MPV_STARTUP_CHECK_SECONDS = 0.4
_STOP_GRACE_SECONDS = 1.0
_STOP_POLL_INTERVAL = 0.1
_IPC_TIMEOUT_SECONDS = 2.0

# SPI_SETDESKWALLPAPER / SPIF_UPDATEINIFILE|SPIF_SENDCHANGE — unchanged since
# Windows Vista, still the correct API on Windows 10/11.
_SPI_SETDESKWALLPAPER = 20
_SPIF_UPDATE_AND_SEND = 3


def _safe_target(target: str) -> str:
    """Filesystem-safe version of a target name, for deriving its IPC
    pipe/stderr-log paths — "ALL" is already plain, and monitor device
    names (e.g. "\\\\.\\DISPLAY1") contain backslashes that need
    stripping for use in a file/pipe name."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", target)


def _ipc_pipe(target: str) -> str:
    return rf"\\.\pipe\livewall-mpv-{_safe_target(target)}"


def _mpv_stderr_log(target: str) -> Path:
    return CACHE_DIR / f"windows_mpv_{_safe_target(target)}_stderr.log"


HOST_STDERR_LOG = CACHE_DIR / "windows_wallpaper_host_stderr.log"


def _pid_alive(pid: int) -> bool:
    """Whether a PID is a live process, checked via OpenProcess +
    GetExitCodeProcess rather than /proc (which doesn't exist on Windows)."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    # HANDLE is pointer-width; without an explicit restype ctypes defaults
    # to a 32-bit int and would truncate the returned handle on x64 — see
    # _windows_wallpaper_host.py's module docstring for the real crash this
    # exact class of missing-argtypes mistake caused elsewhere.
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
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
    # The host window now covers every monitor's bounding rect, not just
    # the primary — a real fix, not just a relabeling, since the old
    # primary-only sizing meant a secondary Windows monitor likely showed
    # nothing under ALL mode at all.
    supports_multi_monitor = True
    supports_pause = True
    supports_resume = True
    supports_restart = False
    supports_thumbnail_refresh = False
    supports_boot_fix = False
    restores_on_login = False  # uses the existing restore-on-boot mechanism
    supports_per_monitor = True  # animated wallpapers only — see set_wallpaper_for_monitor()

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

    # ---- state -------------------------------------------------------

    def _read_state(self) -> dict:
        try:
            raw = json.loads(STATE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        if "targets" not in raw:
            # Pre-per-monitor flat format ({"host_pid":, "mpv_pid":,
            # "path":, "static":}) — read as an implicit ALL entry. The
            # old host_pid (if any) can't be reused without a hwnd map, so
            # it's just abandoned here rather than tracked — a harmless,
            # one-time leftover process until the next full stop()/reboot,
            # not worth the complexity of hunting it down and killing it
            # from a read path.
            if "path" not in raw:
                return {}
            return {
                "host_pid": None,
                "host_hwnds": {},
                "targets": {
                    ALL_TARGET: {
                        "pid": raw.get("mpv_pid"),
                        "path": raw["path"],
                        "static": bool(raw.get("static")),
                    }
                },
            }
        return raw

    def _write_state(self, state: dict) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if state.get("targets"):
            STATE_FILE.write_text(json.dumps(state))
        else:
            STATE_FILE.unlink(missing_ok=True)

    def _live_targets(self) -> dict[str, dict]:
        live: dict[str, dict] = {}
        for target, entry in self._read_state().get("targets", {}).items():
            if entry.get("static"):
                live[target] = entry  # a static "render" has no process to check
            elif entry.get("pid") is not None and _pid_alive(entry["pid"]):
                live[target] = entry
        return live

    # ---- status --------------------------------------------------------

    def is_running(self) -> bool:
        return bool(self._live_targets())

    def current_path(self) -> Path | None:
        entry = self._live_targets().get(ALL_TARGET)
        return Path(entry["path"]) if entry else None

    def current_path_for_monitor(self, monitor: str) -> Path | None:
        entry = self._live_targets().get(monitor)
        return Path(entry["path"]) if entry else None

    def last_applied_path(self) -> Path | None:
        entry = self._read_state().get("targets", {}).get(ALL_TARGET)
        return Path(entry["path"]) if entry else None

    def last_applied_paths_by_monitor(self) -> dict[str, Path]:
        return {
            t: Path(e["path"])
            for t, e in self._read_state().get("targets", {}).items()
            if t != ALL_TARGET
        }

    def list_monitor_targets(self) -> list[str]:
        if sys.platform != "win32":
            return []
        from livewall.windows import monitors

        return monitors.list_monitors()

    # ---- stop ------------------------------------------------------------

    def _stop_target(self, target: str, state: dict) -> dict:
        targets = state.get("targets", {})
        entry = targets.pop(target, None)
        state["targets"] = targets
        if entry is not None and not entry.get("static") and entry.get("pid") is not None:
            pid = entry["pid"]
            if _pid_alive(pid):
                _terminate(pid)
                deadline = time.monotonic() + _STOP_GRACE_SECONDS
                while time.monotonic() < deadline and _pid_alive(pid):
                    time.sleep(_STOP_POLL_INTERVAL)

        if not targets:
            # Nothing left needs the host's windows — kill it too, rather
            # than leaving it holding a WorkerW-parented window stack for
            # no reason.
            host_pid = state.get("host_pid")
            if host_pid is not None and _pid_alive(host_pid):
                _terminate(host_pid)
            state["host_pid"] = None
            state["host_hwnds"] = {}
        return state

    def stop(self) -> None:
        """Stops every tracked target — ALL and any per-monitor renders,
        plus the shared host once nothing needs it — matching the
        pre-per-monitor meaning of "stop everything"."""
        state = self._read_state()
        for target in list(state.get("targets", {})):
            state = self._stop_target(target, state)
        self._write_state(state)

    # ---- host lifecycle -------------------------------------------------

    def _get_or_start_host(self, state: dict) -> tuple[int, dict[str, int]]:
        """Reuses an already-running host's window map if one exists —
        spawning a second host would re-run the WorkerW handshake and
        duplicate the whole window stack — otherwise spawns a fresh one."""
        host_pid = state.get("host_pid")
        host_hwnds = state.get("host_hwnds") or {}
        if host_pid is not None and host_hwnds and _pid_alive(host_pid):
            return host_pid, {str(k): int(v) for k, v in host_hwnds.items()}
        return self._spawn_host()

    def _spawn_host(self) -> tuple[int, dict[str, int]]:
        """Spawns the wallpaper host and reads back the hwnd it acquired
        for every target (ALL, plus one per monitor). A frozen (PyInstaller)
        build has no standalone _windows_wallpaper_host.py file to spawn
        once everything is bundled into one .exe, so it re-invokes itself
        with the hidden --wallpaper-host flag instead (see gui_qt/app.py);
        a dev/source run spawns the plain script directly."""
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--wallpaper-host"]
        else:
            cmd = [sys.executable, str(_HOST_SCRIPT)]
        # stdout stays a PIPE — the host writes a short, fixed burst of
        # lines to it (one per target, plus a DONE sentinel) and never
        # again after that (it just pumps window messages), so there's no
        # long-running-write-to-an-unread-pipe risk there. Stderr is at
        # risk of exactly that, though: the host runs for as long as any
        # wallpaper is applied, far outliving this call, so any stderr
        # write after this function returns would hit a PIPE whose reader
        # is long gone — same fix as everywhere else in this project: a
        # real file, which has no "reader" to disappear.
        HOST_STDERR_LOG.parent.mkdir(parents=True, exist_ok=True)
        try:
            stderr_file = open(HOST_STDERR_LOG, "w")
        except OSError as exc:
            raise BackendApplyError(f"Failed to open {HOST_STDERR_LOG}: {exc}") from exc
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr_file, text=True)
        except OSError as exc:
            raise BackendApplyError(f"Failed to launch the wallpaper host: {exc}") from exc
        finally:
            stderr_file.close()

        deadline = time.monotonic() + _HOST_STARTUP_TIMEOUT_SECONDS
        hwnds: dict[str, int] = {}
        saw_done = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                stderr = HOST_STDERR_LOG.read_text(errors="replace") if HOST_STDERR_LOG.exists() else ""
                raise BackendApplyError(stderr.strip() or "wallpaper host exited before reporting its windows")
            line = proc.stdout.readline().strip() if proc.stdout else ""
            if not line:
                continue
            if line == "DONE":
                saw_done = True
                break
            target, _, hwnd_str = line.partition(" ")
            if hwnd_str.isdigit():
                hwnds[target] = int(hwnd_str)

        if not saw_done or ALL_TARGET not in hwnds:
            _terminate(proc.pid)
            raise BackendApplyError("wallpaper host did not finish reporting its windows in time")

        # The host can "succeed" (report a window, never crash) while having
        # fallen back to a strategy that real-Windows-11 testing found
        # doesn't actually render anything visible — see
        # _windows_wallpaper_host.py's module docstring. Its own stderr log
        # already carries a DEBUG line saying which strategy fired; surface
        # the risky one here too so it's visible without having to go dig up
        # that log file after the fact.
        if HOST_STDERR_LOG.exists():
            host_log = HOST_STDERR_LOG.read_text(errors="replace")
            if "falling back to Progman itself" in host_log:
                logger.warning(
                    "The wallpaper host couldn't find a proper WorkerW window and fell back to "
                    "Progman directly — this has been confirmed NOT to render on at least one real "
                    "Windows 11 build. If the wallpaper doesn't appear, this is very likely why."
                )
        return proc.pid, hwnds

    # ---- apply -----------------------------------------------------------

    def _spawn_mpv(self, target: str, hwnd: int, path: Path) -> int:
        mpv = self._mpv_path()
        cmd = [
            mpv, f"--wid={hwnd}", *_MPV_OPTS_LOOPING,
            f"--input-ipc-server={_ipc_pipe(target)}", str(path),
        ]
        logger.info("Applying via mpv (%s): %r", target, cmd)

        stderr_log = _mpv_stderr_log(target)
        stderr_log.parent.mkdir(parents=True, exist_ok=True)
        try:
            stderr_file = open(stderr_log, "w")
        except OSError as exc:
            raise BackendApplyError(f"Failed to open {stderr_log}: {exc}") from exc
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=stderr_file)
        except OSError as exc:
            raise BackendApplyError(f"Failed to launch mpv: {exc}") from exc
        finally:
            stderr_file.close()

        time.sleep(_MPV_STARTUP_CHECK_SECONDS)
        if proc.poll() is not None:
            stderr = stderr_log.read_text(errors="replace") if stderr_log.exists() else ""
            raise BackendApplyError(stderr.strip() or f"mpv exited immediately (code {proc.returncode})")
        return proc.pid

    def _apply_animated_target(self, target: str, path: Path, state: dict) -> dict:
        """Ensures a host is running, spawns mpv pointed at `target`'s
        hwnd, and folds the result into `state` — still needs to be
        written by the caller, mirroring mpvpaper.py's spawn-then
        -caller-writes-state split."""
        host_pid, hwnds = self._get_or_start_host(state)
        hwnd = hwnds.get(target)
        if hwnd is None:
            raise BackendApplyError(f"the wallpaper host didn't report a window for '{target}'")
        mpv_pid = self._spawn_mpv(target, hwnd, path)
        state["host_pid"] = host_pid
        state["host_hwnds"] = hwnds
        state.setdefault("targets", {})[target] = {"pid": mpv_pid, "path": str(path), "static": False}
        return state

    def set_wallpaper(self, path: Path, *, no_smart: bool = False) -> None:
        # no_smart (Material You recolour opt-out) is a caelestia-aw-only
        # concept — ignored here, same as on the Linux mpvpaper backend.
        mpv = self._mpv_path()
        if mpv is None:
            raise BackendUnavailableError("'mpv' is not on PATH")
        if not path.exists():
            raise FileNotFoundError(f"Wallpaper file missing: {path}")

        # Always stop every tracked target first — guarantees no orphaned
        # host/mpv processes across repeated switches, and that going back
        # to a single mirrored wallpaper actually replaces whatever
        # per-monitor assignments existed.
        state = self._read_state()
        for target in list(state.get("targets", {})):
            state = self._stop_target(target, state)
        self._write_state(state)

        if path.suffix.lower() not in _LOOPING_EXTENSIONS:
            self._set_static(path)
            return

        state = self._read_state()
        state = self._apply_animated_target(ALL_TARGET, path, state)
        self._write_state(state)

    def set_wallpaper_for_monitor(self, monitor: str, path: Path, *, no_smart: bool = False) -> None:
        mpv = self._mpv_path()
        if mpv is None:
            raise BackendUnavailableError("'mpv' is not on PATH")
        if not path.exists():
            raise FileNotFoundError(f"Wallpaper file missing: {path}")
        if path.suffix.lower() not in _LOOPING_EXTENSIONS:
            # A real per-monitor static wallpaper needs the modern
            # IDesktopWallpaper COM interface, not SPI_SETDESKWALLPAPER
            # (which only ever sets one wallpaper across the whole
            # desktop) — a bigger, riskier addition not worth attempting
            # blind on top of everything else here that's already
            # unverified.
            raise BackendApplyError(
                "per-monitor static images aren't supported yet — only animated wallpapers can be assigned to a single monitor"
            )

        state = self._read_state()
        targets = state.get("targets", {})

        if ALL_TARGET in targets:
            # Switching from mirrored to per-monitor: naively tearing ALL
            # down would blank every monitor just to satisfy a change to
            # one of them. Preserve what the others were showing by
            # re-launching ALL's own path explicitly on each of them first
            # (skipped if ALL was a static image — nothing animated to
            # re-launch, and per-monitor static isn't supported anyway).
            all_entry = targets[ALL_TARGET]
            others = [m for m in self.list_monitor_targets() if m != monitor]
            state = self._stop_target(ALL_TARGET, state)
            if not all_entry.get("static"):
                for other in others:
                    try:
                        state = self._apply_animated_target(other, Path(all_entry["path"]), state)
                    except BackendApplyError as exc:
                        logger.warning("Could not preserve the previous wallpaper on %s: %s", other, exc)
                        continue
        else:
            state = self._stop_target(monitor, state)

        state = self._apply_animated_target(monitor, path, state)
        self._write_state(state)

    def _set_static(self, path: Path) -> None:
        if path.suffix.lower() not in _STATIC_EXTENSIONS:
            raise BackendApplyError(f"Unsupported static image format: {path.suffix}")
        user32 = ctypes.windll.user32
        user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPCWSTR, wintypes.UINT]
        user32.SystemParametersInfoW.restype = wintypes.BOOL
        ok = user32.SystemParametersInfoW(_SPI_SETDESKWALLPAPER, 0, str(path), _SPIF_UPDATE_AND_SEND)
        if not ok:
            raise BackendApplyError("SystemParametersInfoW failed to set the wallpaper")
        state = self._read_state()
        state.setdefault("targets", {})[ALL_TARGET] = {"pid": None, "path": str(path), "static": True}
        self._write_state(state)

    # ---- mpv IPC (pause/resume) -------------------------------------------

    def _mpv_ipc(self, target: str, command: list) -> dict | None:
        """Sends one JSON IPC command over `target`'s mpv named pipe and
        returns its reply, or None if that target isn't running / its pipe
        isn't reachable — callers treat that as "nothing to do", not an
        error, same as the Linux backend's _mpv_ipc(). A Windows named pipe
        can be opened via the plain builtin open() in r+b mode — no
        pywin32 needed."""
        if target not in self._live_targets():
            return None
        try:
            with open(_ipc_pipe(target), "r+b", buffering=0) as pipe:
                pipe.write((json.dumps({"command": command}) + "\n").encode())
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = pipe.read(4096)
                    if not chunk:
                        break
                    buf += chunk
        except OSError as exc:
            logger.warning("mpv IPC command %s (%s) failed: %s", command, target, exc)
            return None
        try:
            return json.loads(buf)
        except json.JSONDecodeError:
            return None

    def pause(self) -> None:
        # Battery saver means "stop rendering to save power" — every live
        # animated target gets paused, not just ALL, so a per-monitor
        # setup doesn't keep half its wallpapers burning battery. Static
        # targets have no process to pause.
        for target, entry in self._live_targets().items():
            if not entry.get("static"):
                self._mpv_ipc(target, ["set_property", "pause", True])

    def resume(self) -> None:
        for target, entry in self._live_targets().items():
            if not entry.get("static"):
                self._mpv_ipc(target, ["set_property", "pause", False])

    def is_paused(self) -> bool | None:
        """True/False if known, None if there's nothing (animated) running
        to ask. In per-monitor mode this reports one representative
        target's state (ALL if present, otherwise whichever live target
        sorts first) rather than every target individually — a v1
        simplification, since pause()/resume() above always act on all of
        them together anyway."""
        live = {t: e for t, e in self._live_targets().items() if not e.get("static")}
        if not live:
            return None
        target = ALL_TARGET if ALL_TARGET in live else sorted(live)[0]
        response = self._mpv_ipc(target, ["get_property", "pause"])
        if response is None or "data" not in response:
            return None
        return bool(response["data"])

    def health_check(self) -> list[tuple[str, bool, str]]:
        checks: list[tuple[str, bool, str]] = []

        available = self.is_available()
        checks.append(("mpv CLI", available, "found" if available else "'mpv' not found on PATH or bundled"))

        live = self._live_targets()
        checks.append((
            "wallpaper process running", bool(live),
            f"tracked target(s): {', '.join(sorted(live))}" if live else "not currently running",
        ))

        if not live:
            checks.append(("current wallpaper", True, "none applied yet"))
        else:
            for target in sorted(live):
                path = Path(live[target]["path"])
                label = "current wallpaper" if target == ALL_TARGET else f"current wallpaper ({target})"
                if not path.exists():
                    checks.append((label, False, f"tracked file missing: {path}"))
                else:
                    checks.append((label, True, str(path)))

        return checks
