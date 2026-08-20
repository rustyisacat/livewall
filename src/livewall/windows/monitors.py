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

    # LPARAM is pointer-sized (64-bit on x64 Windows) — ctypes.wintypes.LPARAM
    # is a legacy c_long (32-bit) alias, too narrow for real use. Not passed
    # a meaningful value here (dwData is always 0), but declared correctly
    # anyway since a mismatched callback signature is undefined behavior
    # regardless of whether the value in question happens to be small. See
    # backends/_windows_wallpaper_host.py's module docstring for the real
    # crash this exact class of mistake caused elsewhere in this codebase.
    lparam_t = ctypes.c_ssize_t
    monitorenumproc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(_RECT), lparam_t
    )

    def _callback(hmonitor, _hdc, _rect_ptr, _lparam):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            names.append(info.szDevice)
        return True

    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFOEXW)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, monitorenumproc, lparam_t]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL

    try:
        user32.EnumDisplayMonitors(None, None, monitorenumproc(_callback), 0)
    except OSError:
        return []
    return names
