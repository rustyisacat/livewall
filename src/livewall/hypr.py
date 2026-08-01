"""Hyprland integration: monitor discovery now, keybind/rule installer below."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def list_monitors() -> list[str]:
    """Return connected monitor names via ``hyprctl``, or ``["*"]`` if unavailable."""
    if shutil.which("hyprctl") is None:
        return ["*"]
    try:
        result = subprocess.run(
            ["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=5, check=True
        )
        monitors = json.loads(result.stdout)
        names = [m["name"] for m in monitors if "name" in m]
        return names or ["*"]
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, KeyError) as exc:
        logger.warning("Could not query hyprctl monitors: %s", exc)
        return ["*"]
