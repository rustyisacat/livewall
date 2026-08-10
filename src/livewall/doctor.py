"""One-shot health check across every moving piece LiveWall touches.

There are enough interconnected pieces now (caelestia-aw itself, a QML patch,
three opt-in systemd units, a Hyprland keybind, a desktop entry) that "is
everything actually working" isn't obvious from any single command. This
runs the same kinds of checks used throughout development — process state,
file existence, live hyprctl/systemctl queries — in one pass.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass

from livewall import battery, desktop, engine, hypr, systemd
from livewall.library import Library


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _systemd_active(unit: str) -> str:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", unit], capture_output=True, text=True, timeout=5
    )
    return result.stdout.strip() or "unknown"


def _caelestia_shell_running() -> bool:
    result = subprocess.run(["pgrep", "-x", "qs"], capture_output=True, timeout=5)
    return result.returncode == 0


def _picker_keybind_live() -> bool:
    try:
        result = subprocess.run(
            ["hyprctl", "binds", "-j"], capture_output=True, text=True, timeout=5, check=True
        )
        binds = json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return False
    return any(b.get("key", "").upper() == "B" and b.get("modmask") == 65 for b in binds)


def run() -> list[Check]:
    checks: list[Check] = []

    # --- caelestia-aw backend ---
    if not engine.is_available():
        checks.append(Check("caelestia CLI", False, "'caelestia' not found on PATH"))
    elif not engine.supports_animated():
        checks.append(Check("caelestia CLI", False, "found, but the caelestia-aw patch isn't applied (--extract-thumbs missing)"))
    else:
        checks.append(Check("caelestia CLI", True, "caelestia-aw patch detected"))

    checks.append(Check("caelestia shell running", _caelestia_shell_running(), "pgrep -x qs"))

    current = engine.current_path()
    if current is None:
        checks.append(Check("current wallpaper", False, "state file unreadable/empty"))
    elif not current.exists():
        checks.append(Check("current wallpaper", False, f"state file points to a missing file: {current}"))
    else:
        checks.append(Check("current wallpaper", True, str(current)))

    # --- external tools ---
    for tool in ("ffmpeg", "ffprobe", "mpv"):
        checks.append(Check(f"'{tool}' available", shutil.which(tool) is not None, "on PATH" if shutil.which(tool) else "not found"))

    # --- library integrity ---
    lib = Library()
    wallpapers = lib.all()
    missing = [w for w in wallpapers if not w.file_path.exists()]
    if missing:
        names = ", ".join(w.name for w in missing[:5])
        more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
        checks.append(Check("library integrity", False, f"{len(missing)} entries point to missing files: {names}{more}"))
    else:
        checks.append(Check("library integrity", True, f"{len(wallpapers)} wallpapers, all files present"))

    # --- Hyprland keybind ---
    if not hypr.is_installed():
        checks.append(Check("Hyprland picker keybind", False, "not installed — run 'livewall install hyprland'"))
    elif hypr.needs_repair():
        checks.append(Check("Hyprland picker keybind", False, "installed with a stale bare-command exec — run 'livewall install hyprland' to repair"))
    elif not _picker_keybind_live():
        checks.append(Check("Hyprland picker keybind", False, "files patched, but not registered live — try 'hyprctl reload'"))
    else:
        checks.append(Check("Hyprland picker keybind", True, "Super+Shift+B registered"))

    # --- desktop entry ---
    checks.append(Check(
        "Desktop entry", desktop.is_installed(),
        "installed" if desktop.is_installed() else "not installed (optional) — 'livewall install desktop-entry'",
    ))

    # --- systemd units (all optional) ---
    for label, installed, unit in (
        ("Random-rotation timer", systemd.is_installed(), systemd.TIMER_NAME),
        ("Sync timer", systemd.is_sync_installed(), systemd.SYNC_TIMER_NAME),
        ("Boot-fix service", systemd.is_boot_fix_installed(), systemd.BOOT_FIX_SERVICE_NAME),
    ):
        if not installed:
            checks.append(Check(label, True, "not installed (optional)"))
            continue
        state = _systemd_active(unit)
        ok = state in ("active", "inactive")  # oneshot services/timers idle between runs as "inactive"
        checks.append(Check(label, ok, f"installed, systemctl state: {state}"))

    # --- battery saver patch ---
    if not battery.is_available():
        checks.append(Check("Battery saver patch", True, "not applicable — WallpaperPauser.qml not found"))
    elif not battery.is_patched():
        checks.append(Check("Battery saver patch", True, "not installed (optional)"))
    else:
        checks.append(Check("Battery saver patch", True, "applied"))

    return checks


def format_report(checks: list[Check]) -> str:
    lines = []
    for check in checks:
        mark = "✓" if check.ok else "✗"
        lines.append(f"{mark} {check.name}: {check.detail}")
    failures = [c for c in checks if not c.ok]
    lines.append("")
    if failures:
        lines.append(f"{len(failures)} issue(s) found.")
    else:
        lines.append("Everything looks healthy.")
    return "\n".join(lines)
