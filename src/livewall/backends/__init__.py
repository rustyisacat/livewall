"""Wallpaper renderer backends.

Call sites should only ever import from this package, never from the
individual backend modules — that's what keeps ``cli.py``/``gui.py``/
``picker.py``/``doctor.py`` free of any per-backend conditionals.
"""

from __future__ import annotations

from livewall.backends.base import (
    BackendApplyError,
    BackendError,
    BackendUnavailableError,
    WallpaperBackend,
)
from livewall.backends.registry import available_backend_names, get_backend

# Imported for their @register side effect — populates the registry.
from livewall.backends import caelestia_aw as _caelestia_aw  # noqa: F401
from livewall.backends import mpvpaper as _mpvpaper  # noqa: F401

__all__ = [
    "WallpaperBackend",
    "BackendError",
    "BackendUnavailableError",
    "BackendApplyError",
    "get_backend",
    "available_backend_names",
]
