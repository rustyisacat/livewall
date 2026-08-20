"""Opt-in Windows Task Scheduler entries: the Windows counterpart to
systemd.py's timers. Same four automations, same function shape
(is_installed/install/uninstall per automation), driven by `schtasks.exe`
subprocess calls instead of `systemctl`.

Two real platform limitations worth knowing before touching this file:

- The classic `schtasks` CLI's finest recurring-trigger granularity is one
  minute (`/SC MINUTE /MO 1`) — there's no sub-minute equivalent of
  systemd's 20-second power-saver timer without dropping to the Task
  Scheduler COM API / raw XML task definitions. The Windows power-saver
  task instead runs every minute; battery percentage doesn't change fast
  enough for that to matter in practice.
- `/DELAY` on an ONLOGON trigger is HH:MM granularity, not systemd's
  second-level `ExecStartPre=sleep N` — restore-on-boot uses a 1-minute
  delay rather than the Linux side's 3 seconds. Login/network/graphics
  driver settling doesn't need second-level precision the way the
  caelestia-aw QSettings race the boot-fix service works around does.

NOTE: none of this has been run against a real `schtasks.exe` (no Windows
machine was available during development) — the command shapes below are
built from documented `schtasks` syntax, but need real-Windows validation.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from livewall.config import RANDOM_INTERVAL_SECONDS, RandomInterval

logger = logging.getLogger(__name__)

RANDOM_TASK_NAME = "LiveWall Random Rotation"
SYNC_TASK_NAME = "LiveWall Sync"
RESTORE_TASK_NAME = "LiveWall Restore On Login"
POWER_SAVER_TASK_NAME = "LiveWall Power Saver"

POWER_SAVER_INTERVAL_MINUTES = 1
RESTORE_DELAY = "0000:01"  # HH:MM after logon


def _livewall_bin() -> str:
    # A PyInstaller-frozen build's own executable *is* LiveWall — it accepts
    # the same hidden CLI-style subcommands (see gui_qt/app.py) so a
    # scheduled task can call `LiveWall.exe restore` etc. exactly like the
    # Linux side calls the `livewall` console script.
    if getattr(sys, "frozen", False):
        return sys.executable
    found = shutil.which("livewall")
    if found:
        return found
    raise FileNotFoundError("Could not locate the LiveWall executable to schedule")


def _query(task_name: str) -> bool:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name], capture_output=True, text=True, timeout=10,
    )
    return result.returncode == 0


def _delete(task_name: str) -> None:
    subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True, timeout=10)


def _create(args: list[str]) -> None:
    """Runs a `schtasks /Create ...` command, raising with the command's
    actual stdout/stderr on failure. Real bug, confirmed via a genuine test
    session: every install_*() below used to call subprocess.run(check=True)
    directly, which raises CalledProcessError — whose str() is just "Command
    [...] returned non-zero exit status 1", dropping the one thing that
    would actually explain *why* (schtasks.exe's own error text, on stderr).
    Every caller in this project catches a broad `except Exception` and logs
    str(exc), so fixing this here, once, fixes every call site's error
    message at once."""
    try:
        subprocess.run(args, check=True, capture_output=True, text=True, timeout=15)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(
            f"{' '.join(args)} failed (exit {exc.returncode})" + (f": {detail}" if detail else "")
        ) from exc


def is_installed() -> bool:
    return _query(RANDOM_TASK_NAME)


def install(interval: RandomInterval) -> None:
    if interval == "off":
        raise ValueError("random_interval is 'off' — set an interval first")
    minutes = max(1, RANDOM_INTERVAL_SECONDS[interval] // 60)
    _create([
        "schtasks", "/Create", "/TN", RANDOM_TASK_NAME,
        "/TR", f'"{_livewall_bin()}" random', "/SC", "MINUTE", "/MO", str(minutes), "/F",
    ])
    logger.info("Created scheduled task %r (every %sm)", RANDOM_TASK_NAME, minutes)


def uninstall() -> None:
    _delete(RANDOM_TASK_NAME)


def is_sync_installed() -> bool:
    return _query(SYNC_TASK_NAME)


def install_sync(hours: float) -> None:
    minutes = max(1, int(hours * 60))
    _create([
        "schtasks", "/Create", "/TN", SYNC_TASK_NAME,
        "/TR", f'"{_livewall_bin()}" sync', "/SC", "MINUTE", "/MO", str(minutes), "/F",
    ])
    logger.info("Created scheduled task %r (every %sm)", SYNC_TASK_NAME, minutes)


def uninstall_sync() -> None:
    _delete(SYNC_TASK_NAME)


def is_restore_installed() -> bool:
    return _query(RESTORE_TASK_NAME)


def install_restore_service() -> None:
    _create([
        "schtasks", "/Create", "/TN", RESTORE_TASK_NAME,
        "/TR", f'"{_livewall_bin()}" restore', "/SC", "ONLOGON", "/DELAY", RESTORE_DELAY, "/F",
    ])
    logger.info("Created scheduled task %r", RESTORE_TASK_NAME)


def uninstall_restore_service() -> None:
    _delete(RESTORE_TASK_NAME)


def is_power_saver_installed() -> bool:
    return _query(POWER_SAVER_TASK_NAME)


def install_power_saver() -> None:
    _create([
        "schtasks", "/Create", "/TN", POWER_SAVER_TASK_NAME,
        "/TR", f'"{_livewall_bin()}" power-check', "/SC", "MINUTE",
        "/MO", str(POWER_SAVER_INTERVAL_MINUTES), "/F",
    ])
    logger.info("Created scheduled task %r (every %sm)", POWER_SAVER_TASK_NAME, POWER_SAVER_INTERVAL_MINUTES)


def uninstall_power_saver() -> None:
    _delete(POWER_SAVER_TASK_NAME)
