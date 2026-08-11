"""Battery-percentage wallpaper pause for any backend that supports it.

Unlike ``battery.py`` (which patches caelestia-aw's own QML and is
inherently tied to that one backend), this drives a backend through its
plain ``pause()``/``resume()`` interface — so it works for mpvpaper today
and for any future backend that sets ``supports_pause``/``supports_resume``
without needing its own bespoke mechanism.

No renderer here has an internal battery-percentage timer of its own (mpv
doesn't know or care about system power), so this is meant to be invoked
periodically by a systemd timer (see ``systemd.py``) rather than reacting
to a signal — a short poll interval is simpler and more portable than
wiring UPower/DBus for something that only needs to change a couple of
times per battery cycle. Hysteresis state (was the saver already active)
persists on disk between runs, since each timer firing is a fresh process.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from livewall.backends.base import WallpaperBackend
from livewall.config import CACHE_DIR

logger = logging.getLogger(__name__)

STATE_FILE = CACHE_DIR / "power_saver_state.json"
POWER_SUPPLY_DIR = Path("/sys/class/power_supply")


def _read_battery_percent() -> int | None:
    """The first device under /sys/class/power_supply reporting type
    "Battery", or None on a desktop with no battery at all — callers treat
    that as "nothing to do," not an error, per the project's hardware-
    awareness requirement (same behavior on a laptop and a future desktop)."""
    if not POWER_SUPPLY_DIR.is_dir():
        return None
    for device in sorted(POWER_SUPPLY_DIR.iterdir()):
        try:
            if (device / "type").read_text().strip() != "Battery":
                continue
            return int((device / "capacity").read_text().strip())
        except (OSError, ValueError):
            continue
    return None


def _read_active() -> bool:
    try:
        return bool(json.loads(STATE_FILE.read_text()).get("active", False))
    except (OSError, json.JSONDecodeError):
        return False


def _write_active(active: bool) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"active": active}))


def is_available() -> bool:
    return _read_battery_percent() is not None


def check(backend: WallpaperBackend, low: int, high: int) -> str:
    """Runs one hysteresis check-and-act cycle. Returns a human-readable
    summary — same shape as ``CaelestiaAwBackend.ensure_playing()``, meant
    to be printed directly by the CLI and logged by the systemd service."""
    if not (backend.supports_pause and backend.supports_resume):
        return f"'{backend.name}' backend doesn't support pause/resume — nothing to do"

    percent = _read_battery_percent()
    if percent is None:
        return "no battery detected — nothing to do"

    was_active = _read_active()
    if not was_active and percent <= low:
        backend.pause()
        _write_active(True)
        return f"battery at {percent}% (<= {low}%) — paused"
    if was_active and percent >= high:
        backend.resume()
        _write_active(False)
        return f"battery at {percent}% (>= {high}%) — resumed"

    state = "paused" if was_active else "playing"
    return f"battery at {percent}% — no change ({state})"
