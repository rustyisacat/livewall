from __future__ import annotations

import pytest

from livewall.backends.base import BackendUnavailableError, WallpaperBackend
from livewall.backends.registry import _REGISTRY, available_backend_names, get_backend, register


@pytest.fixture
def clean_registry(monkeypatch):
    """Isolates the registry so test registrations don't leak into other
    tests (or shadow the real backends already registered at import time)."""
    monkeypatch.setattr("livewall.backends.registry._REGISTRY", dict(_REGISTRY))


def test_register_and_get_backend(clean_registry):
    @register
    class FakeBackend(WallpaperBackend):
        name = "fake"

        def is_available(self):
            return True

        def is_running(self):
            return False

        def current_path(self):
            return None

        def set_wallpaper(self, path, *, no_smart=False):
            pass

        def stop(self):
            pass

    backend = get_backend("fake")
    assert isinstance(backend, FakeBackend)
    assert "fake" in available_backend_names()


def test_get_unknown_backend_raises():
    with pytest.raises(BackendUnavailableError):
        get_backend("does-not-exist")


def test_real_backends_are_registered():
    # Importing livewall.backends (done implicitly by conftest/other tests)
    # should have registered all three real backends via their @register
    # decorators.
    import livewall.backends  # noqa: F401

    names = available_backend_names()
    assert {"caelestia-aw", "mpvpaper", "windows-mpv"}.issubset(set(names))


def test_available_backend_names_is_sorted():
    import livewall.backends  # noqa: F401

    names = available_backend_names()
    assert names == sorted(names)
