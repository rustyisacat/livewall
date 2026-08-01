"""Battery-percentage wallpaper pause via a small caelestia-aw shell patch.

caelestia-aw's own WallpaperPauser only pauses based on whether you're on AC
power at all, never battery *percentage*, and exposes no external IPC to
call its real pause()/resume() (which freezes/resumes the exact current
video frame — `caelestia shell -s` confirms `target wallpaper` only has
list/set/get). Applying a static image instead of pausing doesn't work
either: static images are broken on this install, confirmed by applying
caelestia-aw's own bundled default wallpaper asset and seeing the same
black-screen result, independent of anything LiveWall generates.

The only way left to get genuine percentage-based, AC-independent pausing
that freezes the live frame is to patch WallpaperPauser.qml itself: add a
battery-percentage check with its own hysteresis, using
UPower.displayDevice.percentage directly, alongside its existing rules, and
wire percentageChanged so it reacts immediately — no external polling timer
needed at all once this is in place.

This is the one piece of LiveWall that reaches into caelestia-aw's own
installed files rather than just calling its CLI. The file is owned by the
user (not root), so no sudo is required — but a caelestia-shell package
update will overwrite it, so this needs re-applying after that happens.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

WALLPAPER_PAUSER_FILE = Path("/etc/xdg/quickshell/caelestia/services/WallpaperPauser.qml")

_PROPS_ANCHOR = 'property string hwDecoder: "none"'

_RECALC_OLD = (
    'let newPaused = false;\n'
    '        let reason = "None";\n'
    '\n'
    '        // Rule #1 — Battery\n'
    '        if (pauseOnBattery && UPower.onBattery) {'
)

_CONNECTIONS_ANCHOR = (
    'Connections {\n'
    '        target: UPower\n'
    '        function onOnBatteryChanged() {\n'
    '            recalcTimer.restart();\n'
    '        }\n'
    '    }'
)

_MARKER = "LiveWall battery-percentage saver"


def _props_insert(low: float, high: float) -> str:
    return (
        f'{_PROPS_ANCHOR}\n'
        f'    property real batterySaverLowPercent: {low}\n'
        f'    property real batterySaverHighPercent: {high}\n'
        f'    property bool _batterySaverActive: false'
    )


_RECALC_NEW = (
    'let newPaused = false;\n'
    '        let reason = "None";\n'
    '\n'
    f'        // {_MARKER} — independent of AC status, own hysteresis\n'
    '        if (batterySaverLowPercent < 100) {\n'
    '            const battPct = UPower.displayDevice ? UPower.displayDevice.percentage * 100 : 100;\n'
    '            if (!_batterySaverActive && battPct <= batterySaverLowPercent) {\n'
    '                _batterySaverActive = true;\n'
    '            } else if (_batterySaverActive && battPct >= batterySaverHighPercent) {\n'
    '                _batterySaverActive = false;\n'
    '            }\n'
    '        }\n'
    '\n'
    '        if (_batterySaverActive) {\n'
    '            newPaused = true;\n'
    f'            reason = "{_MARKER}";\n'
    '        } else if (pauseOnBattery && UPower.onBattery) {'
)

_CONNECTIONS_INSERT = (
    _CONNECTIONS_ANCHOR
    + '\n\n    Connections {\n'
    '        target: UPower.displayDevice\n'
    '        function onPercentageChanged() {\n'
    '            recalcTimer.restart();\n'
    '        }\n'
    '    }'
)


def is_available() -> bool:
    return WALLPAPER_PAUSER_FILE.exists()


def is_patched() -> bool:
    try:
        return _MARKER in WALLPAPER_PAUSER_FILE.read_text()
    except OSError:
        return False


def _backup_path() -> Path:
    return WALLPAPER_PAUSER_FILE.with_suffix(".qml.livewall.bak")


def patch(low: float, high: float) -> None:
    if not WALLPAPER_PAUSER_FILE.exists():
        raise FileNotFoundError(f"{WALLPAPER_PAUSER_FILE} not found")

    backup = _backup_path()
    if is_patched():
        # Re-patching with new thresholds: start from the pristine backup, not the already-patched file.
        if not backup.exists():
            raise RuntimeError("Already patched but no backup found — refusing to guess the original")
        original = backup.read_text()
    else:
        original = WALLPAPER_PAUSER_FILE.read_text()
        if not backup.exists():
            backup.write_text(original)

    if _PROPS_ANCHOR not in original or _RECALC_OLD not in original or _CONNECTIONS_ANCHOR not in original:
        raise RuntimeError(
            "WallpaperPauser.qml doesn't match the structure this patch expects — not touching it"
        )

    patched = original.replace(_PROPS_ANCHOR, _props_insert(low, high), 1)
    patched = patched.replace(_RECALC_OLD, _RECALC_NEW, 1)
    patched = patched.replace(_CONNECTIONS_ANCHOR, _CONNECTIONS_INSERT, 1)

    WALLPAPER_PAUSER_FILE.write_text(patched)
    logger.info("Patched %s (low=%s, high=%s)", WALLPAPER_PAUSER_FILE, low, high)


def unpatch() -> bool:
    backup = _backup_path()
    if not backup.exists():
        return False
    WALLPAPER_PAUSER_FILE.write_text(backup.read_text())
    logger.info("Restored %s from backup", WALLPAPER_PAUSER_FILE)
    return True
