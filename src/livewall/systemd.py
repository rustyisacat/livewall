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
    return shutil.which("livewall") or str(Path.home() / ".local" / "bin" / "livewall")


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


BATTERY_SERVICE_NAME = "livewall-battery-saver.service"
BATTERY_TIMER_NAME = "livewall-battery-saver.timer"
BATTERY_SERVICE_FILE = UNIT_DIR / BATTERY_SERVICE_NAME
BATTERY_TIMER_FILE = UNIT_DIR / BATTERY_TIMER_NAME


def render_battery_service() -> str:
    return (
        "[Unit]\n"
        "Description=Check battery level for LiveWall's wallpaper battery saver\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={_livewall_bin()} battery-check\n"
    )


def render_battery_timer(seconds: int = 60) -> str:
    return (
        "[Unit]\n"
        "Description=Periodically check battery level for LiveWall's battery saver\n\n"
        "[Timer]\n"
        f"OnBootSec={seconds}\n"
        f"OnUnitActiveSec={seconds}\n"
        "Persistent=false\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def is_battery_saver_installed() -> bool:
    return BATTERY_SERVICE_FILE.exists() and BATTERY_TIMER_FILE.exists()


def install_battery_saver(check_seconds: int = 60) -> None:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    BATTERY_SERVICE_FILE.write_text(render_battery_service())
    BATTERY_TIMER_FILE.write_text(render_battery_timer(check_seconds))
    logger.info("Wrote %s and %s", BATTERY_SERVICE_FILE, BATTERY_TIMER_FILE)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", BATTERY_TIMER_NAME], check=True)


def uninstall_battery_saver() -> None:
    subprocess.run(["systemctl", "--user", "disable", "--now", BATTERY_TIMER_NAME], capture_output=True)
    BATTERY_SERVICE_FILE.unlink(missing_ok=True)
    BATTERY_TIMER_FILE.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
