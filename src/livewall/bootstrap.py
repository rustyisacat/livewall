"""One-time setup performed automatically the first time LiveWall ever
runs, rather than requiring the user to discover and opt into it by
hand via `livewall install ...`. Deliberately the one exception to this
project's usual "opt-in, confirms first" pattern for every other
automation (Hyprland keybind, random-rotation timer, boot-fix, desktop
entry, ...) — a wallpaper manager that never checks for its own updates
defeats half the point of shipping continuous fixes, so this one ships
enabled by default instead.

Guarded by Config.did_first_run_setup rather than "is it currently
installed", so an explicit `livewall uninstall update-checker` (or
disabling autostart in Windows) sticks afterward — this only ever fires
once, on the very first run, and never re-forces anything back on.
"""

from __future__ import annotations

import logging
import sys

from livewall.config import Config

logger = logging.getLogger(__name__)


def ensure_first_run_setup(config: Config) -> None:
    if config.did_first_run_setup:
        return
    config.did_first_run_setup = True

    if sys.platform == "win32":
        _enable_windows_autostart()
    else:
        _enable_linux_update_checker()

    config.save()


def _enable_linux_update_checker() -> None:
    try:
        from livewall import systemd

        if not systemd.is_update_checker_installed():
            systemd.install_update_checker()
            logger.info("First run: enabled the update checker (livewall-update.service)")
    except Exception as exc:
        logger.warning("First-run update-checker setup failed (non-fatal): %s", exc)


def _enable_windows_autostart() -> None:
    # windows/updater.py's self-update check already runs unconditionally
    # on every launch — no separate "install" step needed for that part.
    # What Linux's login-time systemd unit adds that a plain manual launch
    # doesn't is the "runs unattended, every login" cadence; autostart is
    # what gives Windows that same property (and is also required for the
    # quick-picker hotkey to work at all — see windows/startup.py).
    try:
        from livewall.windows import startup

        if not startup.is_autostart_installed():
            startup.install_autostart()
            logger.info("First run: enabled run-at-login")
    except Exception as exc:
        logger.warning("First-run autostart setup failed (non-fatal): %s", exc)
