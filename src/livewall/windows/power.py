"""Battery percentage reading via GetSystemPowerStatus — the Windows
counterpart to power_saver.py's /sys/class/power_supply read. This is the
only Windows-specific piece power_saver.py needs; its hysteresis/driving
logic is already backend- and OS-agnostic.

NOTE: not tested against real Windows (no Windows machine was available
during development) — needs real-Windows validation.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", wintypes.BYTE),
        ("BatteryFlag", wintypes.BYTE),
        ("BatteryLifePercent", wintypes.BYTE),
        ("Reserved1", wintypes.BYTE),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


_BATTERY_FLAG_NO_BATTERY = 128
_BATTERY_PERCENT_UNKNOWN = 255


def read_battery_percent() -> int | None:
    """The current battery percentage, or None on a desktop with no battery
    at all (or if Windows itself doesn't know) — callers treat that as
    "nothing to do," not an error, matching the Linux side's behavior."""
    status = _SYSTEM_POWER_STATUS()
    ok = ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))
    if not ok:
        return None
    if status.BatteryFlag == _BATTERY_FLAG_NO_BATTERY:
        return None
    if status.BatteryLifePercent == _BATTERY_PERCENT_UNKNOWN:
        return None
    return int(status.BatteryLifePercent)
