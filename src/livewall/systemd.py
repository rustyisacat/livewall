"""Opt-in systemd user timers: scheduled random rotation, and periodic sync.

caelestia-aw already restores the last wallpaper on login by itself, so the
automation LiveWall needs to add is (1) periodic random rotation and (2)
periodically re-scanning its wallpapers directory for new files — both plain
oneshot service + timer pairs.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from livewall.config import RANDOM_INTERVAL_SECONDS, RandomInterval

logger = logging.getLogger(__name__)

SERVICE_NAME = "livewall-random.service"
TIMER_NAME = "livewall-random.timer"
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE_FILE = UNIT_DIR / SERVICE_NAME
TIMER_FILE = UNIT_DIR / TIMER_NAME


def _livewall_bin() -> str:
    # Prefer the stable `uv tool install` location over whatever's first on the
    # current PATH — `uv run` prepends the project's own .venv/bin, which would
    # bake a dev-environment-only path into a unit file meant to run standalone.
    stable = Path.home() / ".local" / "bin" / "livewall"
    if stable.exists():
        return str(stable)
    return shutil.which("livewall") or str(stable)


def render_service() -> str:
    return (
        "[Unit]\n"
        "Description=Apply a random LiveWall wallpaper\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={_livewall_bin()} random\n"
    )


def render_timer(interval: RandomInterval) -> str:
    seconds = RANDOM_INTERVAL_SECONDS[interval]
    return (
        "[Unit]\n"
        "Description=Periodically apply a random LiveWall wallpaper\n\n"
        "[Timer]\n"
        f"OnBootSec={seconds}\n"
        f"OnUnitActiveSec={seconds}\n"
        "Persistent=false\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def is_installed() -> bool:
    return SERVICE_FILE.exists() and TIMER_FILE.exists()


def install(interval: RandomInterval) -> None:
    if interval == "off":
        raise ValueError("random_interval is 'off' — set an interval first")

    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    SERVICE_FILE.write_text(render_service())
    TIMER_FILE.write_text(render_timer(interval))
    logger.info("Wrote %s and %s", SERVICE_FILE, TIMER_FILE)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", TIMER_NAME], check=True)


def uninstall() -> None:
    subprocess.run(["systemctl", "--user", "disable", "--now", TIMER_NAME], capture_output=True)
    SERVICE_FILE.unlink(missing_ok=True)
    TIMER_FILE.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)


SYNC_SERVICE_NAME = "livewall-sync.service"
SYNC_TIMER_NAME = "livewall-sync.timer"
SYNC_SERVICE_FILE = UNIT_DIR / SYNC_SERVICE_NAME
SYNC_TIMER_FILE = UNIT_DIR / SYNC_TIMER_NAME


def render_sync_service() -> str:
    return (
        "[Unit]\n"
        "Description=Sync new LiveWall wallpapers from caelestia-aw's wallpapers directory\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={_livewall_bin()} sync\n"
    )


def render_sync_timer(hours: float) -> str:
    seconds = int(hours * 3600)
    return (
        "[Unit]\n"
        "Description=Periodically sync new LiveWall wallpapers\n\n"
        "[Timer]\n"
        f"OnBootSec={seconds}\n"
        f"OnUnitActiveSec={seconds}\n"
        "Persistent=false\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def is_sync_installed() -> bool:
    return SYNC_SERVICE_FILE.exists() and SYNC_TIMER_FILE.exists()


def install_sync(hours: float) -> None:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    SYNC_SERVICE_FILE.write_text(render_sync_service())
    SYNC_TIMER_FILE.write_text(render_sync_timer(hours))
    logger.info("Wrote %s and %s", SYNC_SERVICE_FILE, SYNC_TIMER_FILE)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", SYNC_TIMER_NAME], check=True)


def uninstall_sync() -> None:
    subprocess.run(["systemctl", "--user", "disable", "--now", SYNC_TIMER_NAME], capture_output=True)
    SYNC_SERVICE_FILE.unlink(missing_ok=True)
    SYNC_TIMER_FILE.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)


BOOT_FIX_SERVICE_NAME = "livewall-boot-fix.service"
BOOT_FIX_SERVICE_FILE = UNIT_DIR / BOOT_FIX_SERVICE_NAME
BOOT_FIX_DELAY_SECONDS = 5


def render_boot_fix_service() -> str:
    return (
        "[Unit]\n"
        "Description=Work around caelestia-aw's flaky pause-state init shortly after login\n"
        "After=graphical-session.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        # caelestia shell -d forks a detached daemon without escaping this service's
        # cgroup — the default KillMode=control-group would kill that freshly-spawned
        # process the moment this oneshot's ExecStart exits. KillMode=none leaves it running.
        "KillMode=none\n"
        f"ExecStartPre=/usr/bin/sleep {BOOT_FIX_DELAY_SECONDS}\n"
        # ensure-playing only restarts the shell if the wallpaper genuinely isn't
        # decoding — most boots don't hit the bug at all, so this skips the costly
        # kill+restart in the common case instead of paying it unconditionally.
        f"ExecStart={_livewall_bin()} ensure-playing\n\n"
        "[Install]\n"
        "WantedBy=graphical-session.target\n"
    )


def is_boot_fix_installed() -> bool:
    return BOOT_FIX_SERVICE_FILE.exists()


def install_boot_fix() -> None:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    BOOT_FIX_SERVICE_FILE.write_text(render_boot_fix_service())
    logger.info("Wrote %s", BOOT_FIX_SERVICE_FILE)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", BOOT_FIX_SERVICE_NAME], check=True)


def uninstall_boot_fix() -> None:
    subprocess.run(["systemctl", "--user", "disable", BOOT_FIX_SERVICE_NAME], capture_output=True)
    BOOT_FIX_SERVICE_FILE.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)


RESTORE_SERVICE_NAME = "livewall-restore.service"
RESTORE_SERVICE_FILE = UNIT_DIR / RESTORE_SERVICE_NAME
RESTORE_DELAY_SECONDS = 3


def render_restore_service() -> str:
    return (
        "[Unit]\n"
        "Description=Re-apply the last LiveWall wallpaper on login, for backends "
        "that don't restore it themselves\n"
        "After=graphical-session.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        # Mirrors boot-fix: a backend's set_wallpaper() can fork a detached
        # renderer (e.g. mpvpaper) that doesn't escape this service's cgroup —
        # the default KillMode=control-group would kill it the moment this
        # oneshot's ExecStart exits. KillMode=none leaves it running.
        "KillMode=none\n"
        f"ExecStartPre=/usr/bin/sleep {RESTORE_DELAY_SECONDS}\n"
        f"ExecStart={_livewall_bin()} restore\n\n"
        "[Install]\n"
        "WantedBy=graphical-session.target\n"
    )


def is_restore_installed() -> bool:
    return RESTORE_SERVICE_FILE.exists()


def install_restore_service() -> None:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    RESTORE_SERVICE_FILE.write_text(render_restore_service())
    logger.info("Wrote %s", RESTORE_SERVICE_FILE)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", RESTORE_SERVICE_NAME], check=True)


def uninstall_restore_service() -> None:
    subprocess.run(["systemctl", "--user", "disable", RESTORE_SERVICE_NAME], capture_output=True)
    RESTORE_SERVICE_FILE.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)


POWER_SAVER_SERVICE_NAME = "livewall-power-saver.service"
POWER_SAVER_TIMER_NAME = "livewall-power-saver.timer"
POWER_SAVER_SERVICE_FILE = UNIT_DIR / POWER_SAVER_SERVICE_NAME
POWER_SAVER_TIMER_FILE = UNIT_DIR / POWER_SAVER_TIMER_NAME
POWER_SAVER_INTERVAL_SECONDS = 20


def render_power_saver_service() -> str:
    return (
        "[Unit]\n"
        "Description=Check battery percentage and pause/resume the live wallpaper\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={_livewall_bin()} power-check\n"
    )


def render_power_saver_timer() -> str:
    return (
        "[Unit]\n"
        "Description=Periodically check battery percentage for the live wallpaper\n\n"
        "[Timer]\n"
        f"OnBootSec={POWER_SAVER_INTERVAL_SECONDS}\n"
        f"OnUnitActiveSec={POWER_SAVER_INTERVAL_SECONDS}\n"
        "Persistent=false\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def is_power_saver_installed() -> bool:
    return POWER_SAVER_SERVICE_FILE.exists() and POWER_SAVER_TIMER_FILE.exists()


def install_power_saver() -> None:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    POWER_SAVER_SERVICE_FILE.write_text(render_power_saver_service())
    POWER_SAVER_TIMER_FILE.write_text(render_power_saver_timer())
    logger.info("Wrote %s and %s", POWER_SAVER_SERVICE_FILE, POWER_SAVER_TIMER_FILE)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", POWER_SAVER_TIMER_NAME], check=True)


def uninstall_power_saver() -> None:
    subprocess.run(["systemctl", "--user", "disable", "--now", POWER_SAVER_TIMER_NAME], capture_output=True)
    POWER_SAVER_SERVICE_FILE.unlink(missing_ok=True)
    POWER_SAVER_TIMER_FILE.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)


