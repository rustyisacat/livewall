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
    # True means this backend can assign a *different* wallpaper to each
    # monitor (see list_monitor_targets()/set_wallpaper_for_monitor() etc.
    # below) — distinct from supports_multi_monitor above, which only means
    # "renders across every attached display" (the same clip, mirrored).
    supports_per_monitor: ClassVar[bool] = False
    # Whether this backend already re-applies the last wallpaper on login by
    # itself (e.g. by watching a state file its own shell reads on startup).
    # False means LiveWall needs to do that explicitly — see last_applied_path().
    restores_on_login: ClassVar[bool] = False

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

    def last_applied_path(self) -> Path | None:
        """What was last applied, even if nothing is currently running (e.g.
        right after a reboot). Defaults to current_path(); override this if
        that's tied to a live process rather than durable state."""
        return self.current_path()

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

    # Per-monitor — only call these after checking supports_per_monitor.
    def list_monitor_targets(self) -> list[str]:
        """Monitor identifiers this backend can target individually (e.g.
        Hyprland output names, Windows device names) — empty if detection
        isn't possible right now. Never includes the implicit "ALL" target
        that the plain set_wallpaper()/current_path()/etc. above already
        cover."""
        return []

    def set_wallpaper_for_monitor(self, monitor: str, path: Path, *, no_smart: bool = False) -> None:
        raise NotImplementedError

    def current_path_for_monitor(self, monitor: str) -> Path | None:
        raise NotImplementedError

    def last_applied_paths_by_monitor(self) -> dict[str, Path]:
        """Per-monitor counterpart to last_applied_path() — what
        restore-on-boot should re-apply to each monitor. Empty when nothing
        is in per-monitor mode (the plain last_applied_path() covers the
        ALL/mirrored case)."""
        return {}
