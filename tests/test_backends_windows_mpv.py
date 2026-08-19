"""Only the platform-independent parts — everything else in this backend
needs ctypes.windll / real Windows and can't run here. See the SIGPIPE
regression test file for the one thing about this backend that IS covered
on Linux (the stderr-pipe source guards)."""

from __future__ import annotations

import sys

import pytest

from livewall.backends import windows_mpv


def test_is_available_false_on_non_windows():
    if sys.platform == "win32":
        pytest.skip("this checks the non-Windows guard specifically")
    assert windows_mpv.WindowsMpvBackend().is_available() is False


def test_module_imports_without_windows(monkeypatch):
    # Confirms the module has zero module-level ctypes.windll access — it
    # must be importable on Linux (e.g. so `livewall doctor` can list this
    # backend as registered-but-unavailable) even though it can never do
    # anything real here.
    import importlib

    importlib.reload(windows_mpv)


def test_registered_under_expected_name():
    from livewall.backends.registry import available_backend_names

    assert "windows-mpv" in available_backend_names()


def test_capabilities():
    backend = windows_mpv.WindowsMpvBackend()
    assert backend.supports_video is True
    assert backend.supports_static_images is True
    assert backend.supports_pause is True
    assert backend.supports_resume is True
    assert backend.supports_multi_monitor is False
