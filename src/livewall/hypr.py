"""Opt-in Hyprland integration: a global-hotkey binding for the quick picker.

Nothing here runs automatically. :func:`install` is only ever called from the
CLI after the user has seen the exact snippets and confirmed. Every file it
touches gets a one-time ``.livewall.bak`` copy first.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

HYPR_DIR = Path.home() / ".config" / "hypr"
VARIABLES_FILE = HYPR_DIR / "variables.lua"
KEYBINDS_FILE = HYPR_DIR / "hyprland" / "keybinds.lua"
RULES_FILE = HYPR_DIR / "hyprland" / "rules.lua"

PICKER_APP_ID = "livewall-picker"
PICKER_KEYBIND_DEFAULT = "SUPER + SHIFT + B"


def _livewall_bin() -> str:
    # Hyprland's own process environment doesn't source shell rc files, so it
    # generally won't have ~/.local/bin (where `uv tool install` puts things)
    # on PATH — a bare "livewall" in an exec_cmd silently fails there even
    # though it works fine from an interactive terminal. Use an absolute path.
    return shutil.which("livewall") or str(Path.home() / ".local" / "bin" / "livewall")


PICKER_EXEC_CMD = f"foot --app-id={PICKER_APP_ID} -e {_livewall_bin()} picker"
_OLD_PICKER_EXEC_CMD = f"foot --app-id={PICKER_APP_ID} -e livewall picker"

# Anchors matched against the live files to insert after/into, and to detect
# whether the patch has already been applied (idempotency).
_VARIABLES_ANCHOR = 'kbShowSidebar              = "SUPER + N",'
_VARIABLES_INSERT = f'    kbWallpaperPicker           = "{PICKER_KEYBIND_DEFAULT}",'

_RULES_ANCHOR = '"yad-icon-browser",                                   -- GTK icon browser'
_RULES_INSERT = f'    "{PICKER_APP_ID}",                                      -- LiveWall quick picker'

_KEYBINDS_APPEND = f'''

-- LiveWall quick picker
create_bind(vars.kbWallpaperPicker, hl.dsp.exec_cmd("{PICKER_EXEC_CMD}"), release)
'''


@dataclass
class InstallResult:
    changed: list[Path] = field(default_factory=list)
    already_installed: list[Path] = field(default_factory=list)
    missing_anchor: list[Path] = field(default_factory=list)


def snippets() -> dict[str, str]:
    """Human-readable preview of what :func:`install` would write, for display."""
    return {
        str(VARIABLES_FILE): _VARIABLES_INSERT.strip(),
        str(KEYBINDS_FILE): _KEYBINDS_APPEND.strip(),
        str(RULES_FILE): _RULES_INSERT.strip(),
    }


def is_installed() -> bool:
    try:
        return (
            "kbWallpaperPicker" in VARIABLES_FILE.read_text()
            and PICKER_APP_ID in KEYBINDS_FILE.read_text()
            and PICKER_APP_ID in RULES_FILE.read_text()
        )
    except OSError:
        return False


def needs_repair() -> bool:
    """True if an older install used a bare 'livewall' that Hyprland's PATH can't find."""
    try:
        text = KEYBINDS_FILE.read_text()
    except OSError:
        return False
    return _OLD_PICKER_EXEC_CMD in text and PICKER_EXEC_CMD not in text


def repair() -> bool:
    text = KEYBINDS_FILE.read_text()
    if _OLD_PICKER_EXEC_CMD not in text:
        return False
    _backup(KEYBINDS_FILE)
    KEYBINDS_FILE.write_text(text.replace(_OLD_PICKER_EXEC_CMD, PICKER_EXEC_CMD))
    return True


def _backup(path: Path) -> None:
    backup_path = path.with_suffix(path.suffix + ".livewall.bak")
    if not backup_path.exists():
        backup_path.write_text(path.read_text())
        logger.info("Backed up %s -> %s", path, backup_path)


def install() -> InstallResult:
    result = InstallResult()

    # variables.lua: add the kbWallpaperPicker keybind constant.
    text = VARIABLES_FILE.read_text()
    if "kbWallpaperPicker" in text:
        result.already_installed.append(VARIABLES_FILE)
    elif _VARIABLES_ANCHOR not in text:
        result.missing_anchor.append(VARIABLES_FILE)
    else:
        _backup(VARIABLES_FILE)
        text = text.replace(_VARIABLES_ANCHOR, _VARIABLES_ANCHOR + "\n" + _VARIABLES_INSERT, 1)
        VARIABLES_FILE.write_text(text)
        result.changed.append(VARIABLES_FILE)

    # keybinds.lua: append the create_bind call at the end of the file.
    text = KEYBINDS_FILE.read_text()
    if PICKER_APP_ID in text:
        result.already_installed.append(KEYBINDS_FILE)
    else:
        _backup(KEYBINDS_FILE)
        KEYBINDS_FILE.write_text(text.rstrip("\n") + "\n" + _KEYBINDS_APPEND)
        result.changed.append(KEYBINDS_FILE)

    # rules.lua: add the app-id to the existing float_60_70 class-matched rule.
    text = RULES_FILE.read_text()
    if PICKER_APP_ID in text:
        result.already_installed.append(RULES_FILE)
    elif _RULES_ANCHOR not in text:
        result.missing_anchor.append(RULES_FILE)
    else:
        _backup(RULES_FILE)
        text = text.replace(_RULES_ANCHOR, _RULES_INSERT + "\n    " + _RULES_ANCHOR, 1)
        RULES_FILE.write_text(text)
        result.changed.append(RULES_FILE)

    return result
