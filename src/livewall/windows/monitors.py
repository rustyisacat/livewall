"""Monitor enumeration via EnumDisplayMonitors/GetMonitorInfoW — the
Windows counterpart to hypr.py's `hyprctl monitors -j` parsing, used by
backends/windows_mpv.py's list_monitor_targets().

Only returns device names (e.g. "\\\\.\\DISPLAY1") here — the actual
per-monitor rects/positioning math lives in
backends/_windows_wallpaper_host.py, the one place that genuinely needs
DPI-awareness set before reading geometry; a plain name enumeration isn't
affected by DPI virtualization the way rects are.

NOTE: not tested against real Windows (no Windows machine was available
during development) — needs real-Windows validation.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


def list_monitors() -> list[str]:
    """Every attached monitor's device name (e.g. "\\\\.\\DISPLAY1"),
    empty if enumeration fails for any reason — matches the project's
    existing "degrade gracefully, never crash" pattern for detection
    helpers (see hypr.py::list_monitors()).

    ctypes.WINFUNCTYPE (used for the enumeration callback) doesn't exist
    on non-Windows platforms at all, unlike ctypes.windll (which exists
    but fails at attribute-access time) — so this whole thing has to stay
    inside the function body to keep the module importable on Linux (this
    is what power_saver.py needs at import time regardless of platform).
    """
    user32 = ctypes.windll.user32
    names: list[str] = []

    monitorenumproc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(_RECT), wintypes.LPARAM
    )

    def _callback(hmonitor, _hdc, _rect_ptr, _lparam):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            names.append(info.szDevice)
        return True

    try:
        user32.EnumDisplayMonitors(None, None, monitorenumproc(_callback), 0)
    except OSError:
        return []
    return names
