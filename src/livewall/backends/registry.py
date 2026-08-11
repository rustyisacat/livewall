"""Plain name -> backend-class registry.

Adding a new backend later means writing one module with a ``@register``-ed
class and importing it in ``backends/__init__.py`` — nothing else in
LiveWall needs to change.
"""

from __future__ import annotations

from livewall.backends.base import BackendUnavailableError, WallpaperBackend

_REGISTRY: dict[str, type[WallpaperBackend]] = {}


def register(cls: type[WallpaperBackend]) -> type[WallpaperBackend]:
    _REGISTRY[cls.name] = cls
    return cls


def get_backend(name: str) -> WallpaperBackend:
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise BackendUnavailableError(f"Unknown backend '{name}'") from None


def available_backend_names() -> list[str]:
    return sorted(_REGISTRY)
