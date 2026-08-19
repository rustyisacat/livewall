"""Thin wrapper around the Win32 global-hotkey API (RegisterHotKey/
UnregisterHotKey) — this is the Windows counterpart to hypr.py's
Super+Shift+B keybind installer, but works fundamentally differently:

hypr.py patches Hyprland's *own* config file, so the compositor owns the
keybind permanently, independent of whether LiveWall is running.
RegisterHotKey only works for as long as the registering window/thread is
alive — there's no config file to "install" a keybind into on Windows. This
means the hotkey is only live while LiveWall's tray process (gui_qt/tray.py)
is running, which is why "run at login" (see startup.py's autostart
functions) matters on Windows in a way it doesn't on Linux.

The actual registration needs a real window handle to attach to and a
message loop to receive WM_HOTKEY on — gui_qt/tray.py is expected to call
register() with its main window's winId() and install a Qt native event
filter to catch WM_HOTKEY, since Qt's own event loop already pumps the
native Win32 message queue. This module deliberately only wraps the raw
Win32 calls, no Qt dependency here.

NOTE: not tested against real Windows (no Windows machine was available
during development) — needs real-Windows validation.
"""

from __future__ import annotations

import ctypes
import logging

logger = logging.getLogger(__name__)

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000  # only deliver one WM_HOTKEY per physical press

QUICK_PICKER_HOTKEY_ID = 1
# Mirrors the Linux default (Super+Shift+B): MOD_WIN | MOD_SHIFT + 'B'.
DEFAULT_MODIFIERS = MOD_WIN | MOD_SHIFT | MOD_NOREPEAT
DEFAULT_VK = ord("B")


def register(hwnd: int, hotkey_id: int = QUICK_PICKER_HOTKEY_ID,
             modifiers: int = DEFAULT_MODIFIERS, vk: int = DEFAULT_VK) -> bool:
    ok = bool(ctypes.windll.user32.RegisterHotKey(hwnd, hotkey_id, modifiers, vk))
    if not ok:
        logger.warning("RegisterHotKey failed (id=%s) — likely already claimed by another app", hotkey_id)
    return ok


def unregister(hwnd: int, hotkey_id: int = QUICK_PICKER_HOTKEY_ID) -> None:
    ctypes.windll.user32.UnregisterHotKey(hwnd, hotkey_id)
