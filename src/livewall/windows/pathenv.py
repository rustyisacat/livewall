"""Adds LiveWall's own install directory to the user's PATH, so `livewall`
(or `LiveWall`, case doesn't matter to Windows' own PATH lookup) works from
any newly-opened terminal without the user doing anything by hand — the
Windows counterpart to Linux's console-script entry point already being on
PATH the moment `uv tool install` finishes.

NOTE: not tested against real Windows (no Windows machine was available
during development) — the registry approach below is the standard,
documented way to edit a *user-level* PATH (HKCU\\Environment, not the
machine-wide one, which would need admin rights this app doesn't ask for),
but needs real-Windows validation.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_ENV_KEY = "Environment"
_PATH_VALUE = "Path"


def _install_dir() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None  # nothing to add for a dev/source run
    return Path(sys.executable).resolve().parent


def _current_path_entries() -> tuple[list[str], int]:
    """Returns (entries, registry value type) — the type matters: Path is
    normally REG_EXPAND_SZ (so %VARS% inside it still expand), and writing
    it back as plain REG_SZ would silently break that for every other
    entry already in there, not just ours."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ENV_KEY) as key:
            current, value_type = winreg.QueryValueEx(key, _PATH_VALUE)
    except (FileNotFoundError, OSError):
        return [], winreg.REG_EXPAND_SZ
    return [p for p in current.split(";") if p.strip()], value_type


def is_on_path() -> bool:
    install_dir = _install_dir()
    if install_dir is None:
        return True
    entries, _ = _current_path_entries()
    return str(install_dir).lower() in [p.strip().lower() for p in entries]


def add_to_path() -> None:
    install_dir = _install_dir()
    if install_dir is None:
        return

    entries, value_type = _current_path_entries()
    if str(install_dir).lower() in [p.strip().lower() for p in entries]:
        return  # already there

    import winreg

    entries.append(str(install_dir))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _ENV_KEY) as key:
        winreg.SetValueEx(key, _PATH_VALUE, 0, value_type, ";".join(entries))

    logger.info("Added %s to the user PATH", install_dir)
    _broadcast_environment_change()


def _broadcast_environment_change() -> None:
    # Lets already-open processes (e.g. Explorer) notice the change without
    # a full logoff. A brand-new terminal window re-reads the registry at
    # launch regardless, so this is a nice-to-have that helps it propagate
    # sooner, not something the feature depends on to work at all.
    import ctypes

    HWND_BROADCAST = 0xFFFF
    WM_SETTINGCHANGE = 0x001A
    SMTO_ABORTIFHUNG = 0x0002

    user32 = ctypes.windll.user32
    user32.SendMessageTimeoutW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_wchar_p,
        ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t),
    ]
    user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    result = ctypes.c_size_t()
    try:
        user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, ctypes.byref(result)
        )
    except OSError as exc:
        logger.warning("Broadcasting the PATH change failed (non-fatal): %s", exc)
