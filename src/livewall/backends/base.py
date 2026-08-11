"""The backend interface every wallpaper renderer implements.

LiveWall's library, GUI, and CLI never talk to caelestia-aw or mpvpaper
directly — they only ever go through a ``WallpaperBackend``. Backends declare
what they support via capability flags rather than faking methods they can't
honestly implement; callers check the flag before calling the matching
optional method.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar


class BackendError(RuntimeError):
    """Base class for all backend failures."""


class BackendUnavailableError(BackendError):
    """Raised when a backend's underlying binary/service isn't available."""


class BackendApplyError(BackendError):
    """Raised when setting a wallpaper fails."""


class WallpaperBackend(ABC):
    """A renderer LiveWall can apply wallpapers through."""

    name: ClassVar[str]

    supports_video: ClassVar[bool] = True
    supports_static_images: ClassVar[bool] = True
    supports_audio: ClassVar[bool] = False
    supports_multi_monitor: ClassVar[bool] = False
    supports_pause: ClassVar[bool] = False
    supports_resume: ClassVar[bool] = False
    supports_restart: ClassVar[bool] = False
    supports_thumbnail_refresh: ClassVar[bool] = False
    supports_boot_fix: ClassVar[bool] = False

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this backend's underlying tool is installed/reachable."""

    @abstractmethod
    def is_running(self) -> bool:
        """Whether this backend currently has a wallpaper actively rendering."""

    @abstractmethod
    def current_path(self) -> Path | None:
        """The wallpaper file currently applied, if known."""

    @abstractmethod
    def set_wallpaper(self, path: Path, *, no_smart: bool = False) -> None:
        """Apply ``path`` as the wallpaper, replacing whatever was applied before."""

    @abstractmethod
    def stop(self) -> None:
        """Stop rendering and release anything this backend owns."""

    def health_check(self) -> list[tuple[str, bool, str]]:
        """Backend-specific rows for ``livewall doctor``: (name, ok, detail)."""
        return []

    # Capability-gated: only call these after checking the matching flag above.
    def pause(self) -> None:
        raise NotImplementedError

    def resume(self) -> None:
        raise NotImplementedError

    def restart_render_service(self) -> None:
        raise NotImplementedError

    def refresh_thumbnail_cache(self) -> None:
        raise NotImplementedError

    def ensure_playing(self) -> str:
        raise NotImplementedError
