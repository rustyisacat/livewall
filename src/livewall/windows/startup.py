"""Opt-in Start Menu shortcut + "run at login" entry — the Windows
counterpart to desktop.py's .desktop file. Two genuinely separate concerns,
unlike Linux:

- The Start Menu shortcut is just a launcher entry (mirrors desktop.py).
- "Run at login" doesn't exist as a Linux concept for LiveWall at all: on
  Linux, Hyprland itself owns the Super+Shift+B keybind (hypr.py patches
  Hyprland's own config), so it works whether or not LiveWall is running.
  On Windows, RegisterHotKey (see hotkey.py) only works while the
  registering process is alive — so the quick-picker hotkey needs LiveWall's
  tray process running continuously, which means it needs to auto-start.

NOTE: shortcut creation goes through pywin32's win32com.client (WScript.Shell
COM object) — imported lazily inside the functions that need it so this
module still imports cleanly on non-Windows. None of this has been run
against a real Windows shell (no Windows machine was available during
development) and needs real-Windows validation.
"""

from __future__ import annotations

import logging
from pathlib import Path

# winreg is a Windows-only stdlib module (absent entirely on other
# platforms, not just non-functional) — imported lazily inside the
# functions that need it so this module still imports cleanly elsewhere.

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
ICON_SOURCE = DATA_DIR / "livewall.ico"

SHORTCUT_NAME = "LiveWall.lnk"
AUTOSTART_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "LiveWall"


def _start_menu_dir() -> Path:
    import winshell  # type: ignore[import-not-found]

    return Path(winshell.start_menu()) / "Programs"


def _shortcut_path() -> Path:
    return _start_menu_dir() / SHORTCUT_NAME


def _livewall_exe() -> Path:
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    raise FileNotFoundError(
        "Can't locate a packaged LiveWall.exe to shortcut — this only "
        "applies to a PyInstaller-built install"
    )


def is_shortcut_installed() -> bool:
    return _shortcut_path().exists()


def install_shortcut() -> None:
    import win32com.client  # type: ignore[import-not-found]

    target = _livewall_exe()
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(_shortcut_path()))
    shortcut.TargetPath = str(target)
    shortcut.WorkingDirectory = str(target.parent)
    if ICON_SOURCE.exists():
        shortcut.IconLocation = str(ICON_SOURCE)
    shortcut.Description = "LiveWall — animated wallpaper library manager"
    shortcut.Save()
    logger.info("Wrote %s", _shortcut_path())


def uninstall_shortcut() -> None:
    _shortcut_path().unlink(missing_ok=True)


def is_autostart_installed() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REGISTRY_KEY) as key:
            winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
            return True
    except (FileNotFoundError, OSError):
        return False


def install_autostart() -> None:
    import winreg

    target = _livewall_exe()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REGISTRY_KEY) as key:
        winreg.SetValueEx(key, AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, f'"{target}" --tray')
    logger.info("Registered %s to run at login", target)


def uninstall_autostart() -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REGISTRY_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
    except (FileNotFoundError, OSError):
        pass
