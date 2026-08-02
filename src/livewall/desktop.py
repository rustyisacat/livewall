"""Opt-in .desktop entry so ``livewall gui`` shows up as a real app.

The GUI is a Textual (terminal) app, so "installing" it as a desktop app
means the standard thing TUI apps do: a .desktop file whose Exec= launches a
terminal running it, discovered via the normal XDG applications directory
that Caelestia's launcher (like virtually every Quickshell/freedesktop-based
launcher) scans automatically.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
ICON_SOURCE = DATA_DIR / "livewall.svg"

APPLICATIONS_DIR = Path.home() / ".local" / "share" / "applications"
ICONS_DIR = Path.home() / ".local" / "share" / "icons"
DESKTOP_FILE = APPLICATIONS_DIR / "livewall.desktop"
ICON_DEST = ICONS_DIR / "livewall.svg"

TERMINAL_BIN = "foot"


def _livewall_bin() -> str:
    stable = Path.home() / ".local" / "bin" / "livewall"
    if stable.exists():
        return str(stable)
    return shutil.which("livewall") or str(stable)


def render_desktop_file() -> str:
    exec_cmd = f"{TERMINAL_BIN} --app-id=livewall-gui -e {_livewall_bin()} gui"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=LiveWall\n"
        "Comment=Animated wallpaper library manager for Hyprland + Caelestia\n"
        f"Exec={exec_cmd}\n"
        f"Icon={ICON_DEST}\n"
        "Terminal=false\n"
        "Categories=Graphics;\n"
        "StartupNotify=true\n"
    )


def is_installed() -> bool:
    return DESKTOP_FILE.exists()


def install() -> None:
    if not ICON_SOURCE.exists():
        raise FileNotFoundError(f"Icon not found: {ICON_SOURCE}")
    if shutil.which(TERMINAL_BIN) is None:
        raise FileNotFoundError(f"'{TERMINAL_BIN}' is not on PATH")

    APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)

    ICON_DEST.write_text(ICON_SOURCE.read_text())
    DESKTOP_FILE.write_text(render_desktop_file())
    logger.info("Wrote %s and %s", DESKTOP_FILE, ICON_DEST)


def uninstall() -> None:
    DESKTOP_FILE.unlink(missing_ok=True)
    ICON_DEST.unlink(missing_ok=True)
