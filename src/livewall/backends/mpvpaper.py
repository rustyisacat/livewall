"""Backend: renders wallpapers via mpvpaper (https://github.com/GhostNaN/mpvpaper).

mpvpaper has no state file or CLI query of its own — LiveWall tracks the PID
and applied path itself so ``current_path()``/``is_running()`` work, and so
switching wallpapers can cleanly stop the previous process before starting
the next one (no orphaned renderers across repeated switches).

Also the one backend that supports per-monitor wallpapers: mpvpaper accepts
a specific Wayland output name (e.g. "DP-2") as its positional <output>
argument instead of the special value "ALL", so per-monitor targeting is
just spawning one mpvpaper process per monitor instead of one pointed at
everything. The state file is keyed by "target" — "ALL" for the ordinary
mirrored case, or a real output name for a per-monitor one — with each
target tracking its own PID/path/IPC-socket/stderr-log independently so any
number of them can run concurrently.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import socket
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
ALL_TARGET = "ALL"

# mpv handles gifs fine via loop-file, so they get the same looping options as
# real video containers rather than a hardcoded per-extension special case.
_LOOPING_EXTENSIONS = {".mp4", ".webm", ".mkv", ".gif"}
# load-scripts=no: mpv auto-loads every script in /etc/mpv/scripts and
# ~/.config/mpv/scripts for every instance it spawns — on a system with
# mpv-mpris installed, that turns each wallpaper into its own MPRIS media
# session (shows up as "now playing" on media widgets/dashboards). This is a
# wallpaper renderer, not a media player, so scripting is disabled outright;
# --scripts-clr only clears an explicitly-passed --scripts list, it does NOT
# stop the directory auto-load scan that actually loads mpris.so, so it's
# not enough on its own. The user's real mpv usage elsewhere is a separate
# process and unaffected.
# panscan=1.0: fill the screen by cropping instead of mpv's default
# letterbox/pillarbox behavior when the source's aspect ratio doesn't
# exactly match the display's — a black bar is the alternative, which is
# never what a wallpaper should show. profile=gpu-hq: mpv's own built-in
# bundle of higher-quality scalers (ewa_lanczossharp-class upscaling
# instead of plain bilinear) — the difference is very visible on anything
# lower-resolution than the display it's stretched to, which is common
# for wallpaper-sized source video.
_MPV_OPTS_LOOPING = "loop-file=inf no-audio load-scripts=no panscan=1.0 profile=gpu-hq"
_MPV_OPTS_STATIC = "image-display-duration=inf no-audio load-scripts=no panscan=1.0 profile=gpu-hq"

_STARTUP_CHECK_SECONDS = 0.4
_STOP_GRACE_SECONDS = 1.0
_STOP_POLL_INTERVAL = 0.1
_IPC_TIMEOUT_SECONDS = 2.0


def _safe_target(target: str) -> str:
    """Filesystem-safe version of a target name, for deriving its IPC
    socket/stderr-log paths — output names are already plain (e.g.
    "eDP-1") but this guards against anything stranger regardless."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", target)


def _ipc_socket(target: str) -> Path:
    return CACHE_DIR / f"mpvpaper_{_safe_target(target)}.sock"


def _stderr_log(target: str) -> Path:
    return CACHE_DIR / f"mpvpaper_{_safe_target(target)}_stderr.log"


@register
class MpvpaperBackend(WallpaperBackend):
    name: ClassVar[str] = "mpvpaper"

    supports_video = True
    supports_static_images = True
    supports_audio = True
    supports_multi_monitor = True  # same clip on every output via mpvpaper's "ALL" target
    supports_pause = True
    supports_resume = True
    supports_restart = False
    supports_thumbnail_refresh = False
    supports_boot_fix = False
    supports_per_monitor = True  # a genuinely different wallpaper per output

    def is_available(self) -> bool:
        return shutil.which(MPVPAPER_BIN) is not None

    # ---- state -------------------------------------------------------

    def _read_state(self) -> dict[str, dict]:
        try:
            raw = json.loads(STATE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        if "pid" in raw:
            # Pre-per-monitor flat format ({"pid":, "path":}) — read as an
            # implicit ALL entry, no rewrite needed.
            return {ALL_TARGET: {"pid": raw["pid"], "path": raw["path"]}}
        return raw

    def _write_state(self, state: dict[str, dict]) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if state:
            STATE_FILE.write_text(json.dumps(state))
        else:
            STATE_FILE.unlink(missing_ok=True)

    def _pid_alive(self, pid: int) -> bool:
        try:
            return Path(f"/proc/{pid}/comm").read_text().strip() == "mpvpaper"
        except OSError:
            return False

    def _live_targets(self) -> dict[str, dict]:
        """Every tracked target whose PID is actually still alive."""
        return {t: entry for t, entry in self._read_state().items() if self._pid_alive(entry["pid"])}

    # ---- status --------------------------------------------------------

    def is_running(self) -> bool:
        return bool(self._live_targets())

    def current_path(self) -> Path | None:
        """The ALL/mirrored target's current path specifically — None if
        nothing's live under ALL, even if per-monitor targets are active.
        Use current_path_for_monitor() for those."""
        entry = self._live_targets().get(ALL_TARGET)
        return Path(entry["path"]) if entry else None

    def current_path_for_monitor(self, monitor: str) -> Path | None:
        entry = self._live_targets().get(monitor)
        return Path(entry["path"]) if entry else None

    def last_applied_path(self) -> Path | None:
        # Deliberately skips the liveness check current_path() does — right
        # after a reboot the tracked PID is legitimately gone, but the path
        # itself is still the thing to restore, not something to self-heal away.
        entry = self._read_state().get(ALL_TARGET)
        return Path(entry["path"]) if entry else None

    def last_applied_paths_by_monitor(self) -> dict[str, Path]:
        return {t: Path(entry["path"]) for t, entry in self._read_state().items() if t != ALL_TARGET}

    def list_monitor_targets(self) -> list[str]:
        from livewall import hypr

        return hypr.list_monitors()

    # ---- stop ------------------------------------------------------------

    def _stop_target(self, target: str, state: dict[str, dict]) -> dict[str, dict]:
        """Kills `target`'s tracked process (if alive) and drops its state
        entry + its IPC socket. Returns the updated dict — caller still
        owns writing it back, so multiple targets can be stopped as one
        atomic state update."""
        entry = state.pop(target, None)
        if entry is None:
            return state
        pid = entry["pid"]
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
        _ipc_socket(target).unlink(missing_ok=True)
        return state

    def stop(self) -> None:
        """Stops every tracked target — ALL and any per-monitor renders —
        matching the pre-per-monitor meaning of "stop everything"."""
        state = self._read_state()
        for target in list(state):
            state = self._stop_target(target, state)
        self._write_state(state)

    # ---- apply -----------------------------------------------------------

    def _spawn(self, target: str, path: Path) -> int:
        """Spawns mpvpaper pointed at `target` (an output name, or "ALL"),
        returns its pid. Raises BackendApplyError if it fails or dies
        immediately."""
        opts = _MPV_OPTS_LOOPING if path.suffix.lower() in _LOOPING_EXTENSIONS else _MPV_OPTS_STATIC
        # A JSON IPC socket, forwarded straight through to the underlying mpv
        # process (mpvpaper just passes -o options along) — this is what
        # pause()/resume() below talk to. mpv unlinks and rebinds a stale
        # socket file on its own; _stop_target() also removes it on our side
        # so a dead process never leaves a stale path a new one could race.
        opts = f"{opts} input-ipc-server={_ipc_socket(target)}"
        cmd = [MPVPAPER_BIN, "--layer", "background", "-o", opts, target, str(path)]
        logger.info("Applying via mpvpaper (%s): %s", target, " ".join(cmd))

        # stderr goes to a real file, not subprocess.PIPE: mpvpaper is meant
        # to keep running long after this (short-lived) CLI/systemd-service
        # process exits, but a PIPE's read end closes when the parent that
        # opened it exits. mpvpaper does write to stderr periodically during
        # normal playback, and once that read end is gone, its next write
        # raises SIGPIPE and kills the whole renderer a few seconds later —
        # looking exactly like a successful apply followed by the wallpaper
        # silently vanishing (this is what was actually happening on every
        # boot and every apply before this fix). A plain file has no
        # "reader" to disappear, so this can't happen with one.
        stderr_log = _stderr_log(target)
        stderr_log.parent.mkdir(parents=True, exist_ok=True)
        try:
            stderr_file = open(stderr_log, "w")
        except OSError as exc:
            raise BackendApplyError(f"Failed to open {stderr_log}: {exc}") from exc
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=stderr_file,
                start_new_session=True,
            )
        except OSError as exc:
            raise BackendApplyError(f"Failed to launch mpvpaper: {exc}") from exc
        finally:
            # The child has its own duplicated fd to the same file, so this
            # doesn't affect its ability to keep writing.
            stderr_file.close()

        time.sleep(_STARTUP_CHECK_SECONDS)
        if proc.poll() is not None:
            stderr = stderr_log.read_text(errors="replace") if stderr_log.exists() else ""
            raise BackendApplyError(
                stderr.strip() or f"mpvpaper exited immediately (code {proc.returncode})"
            )
        return proc.pid

    def set_wallpaper(self, path: Path, *, no_smart: bool = False) -> None:
        # no_smart (Material You recolour opt-out) is a caelestia-aw-only
        # concept — there's no equivalent for mpvpaper, so it's just ignored.
        if not self.is_available():
            raise BackendUnavailableError("'mpvpaper' is not on PATH")
        if not path.exists():
            raise FileNotFoundError(f"Wallpaper file missing: {path}")

        # Always stop everything (ALL and any per-monitor renders) before
        # spawning — guarantees no orphaned mpvpaper across repeated
        # switches, and that going back to a single mirrored wallpaper
        # actually replaces whatever per-monitor assignments existed.
        self.stop()

        pid = self._spawn(ALL_TARGET, path)
        self._write_state({ALL_TARGET: {"pid": pid, "path": str(path)}})

    def set_wallpaper_for_monitor(self, monitor: str, path: Path, *, no_smart: bool = False) -> None:
        if not self.is_available():
            raise BackendUnavailableError("'mpvpaper' is not on PATH")
        if not path.exists():
            raise FileNotFoundError(f"Wallpaper file missing: {path}")

        state = self._read_state()

        if ALL_TARGET in state:
            # Switching from mirrored to per-monitor: naively tearing ALL
            # down would blank every monitor just to satisfy a change to
            # one of them. Preserve what the others were showing by
            # re-launching ALL's own path explicitly on each of them first.
            all_path = state[ALL_TARGET]["path"]
            others = [m for m in self.list_monitor_targets() if m != monitor]
            state = self._stop_target(ALL_TARGET, state)
            for other in others:
                try:
                    other_pid = self._spawn(other, Path(all_path))
                except BackendApplyError as exc:
                    logger.warning("Could not preserve the previous wallpaper on %s: %s", other, exc)
                    continue
                state[other] = {"pid": other_pid, "path": all_path}
        else:
            state = self._stop_target(monitor, state)

        pid = self._spawn(monitor, path)
        state[monitor] = {"pid": pid, "path": str(path)}
        self._write_state(state)

    # ---- mpv IPC (pause/resume) -------------------------------------------

    def _mpv_ipc(self, target: str, command: list) -> dict | None:
        """Sends one JSON IPC command to `target`'s mpv process and returns
        its reply, or None if that target isn't running / its socket isn't
        reachable — callers treat that as "nothing to do" rather than an
        error, since pause/resume against an already-stopped wallpaper is a
        no-op, not a failure."""
        if target not in self._live_targets():
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(_IPC_TIMEOUT_SECONDS)
                sock.connect(str(_ipc_socket(target)))
                sock.sendall((json.dumps({"command": command}) + "\n").encode())
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = sock.recv(4096)
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
        # target gets paused, not just ALL, so a per-monitor setup doesn't
        # keep half its wallpapers burning battery.
        for target in self._live_targets():
            self._mpv_ipc(target, ["set_property", "pause", True])

    def resume(self) -> None:
        for target in self._live_targets():
            self._mpv_ipc(target, ["set_property", "pause", False])

    def is_paused(self) -> bool | None:
        """True/False if known, None if there's nothing running to ask —
        used by power_saver.py to avoid sending a redundant pause/resume
        and by `livewall status`/`doctor` for reporting. In per-monitor
        mode this reports one representative target's state (ALL if
        present, otherwise whichever live target sorts first) rather than
        every target individually — a v1 simplification, since pause()/
        resume() above always act on all of them together anyway."""
        live = self._live_targets()
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
        checks.append(("mpvpaper CLI", available, "found on PATH" if available else "'mpvpaper' not found on PATH"))

        live = self._live_targets()
        checks.append((
            "mpvpaper process running", bool(live),
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
