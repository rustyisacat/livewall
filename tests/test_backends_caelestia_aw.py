from __future__ import annotations

import subprocess

import pytest

from livewall.backends import caelestia_aw
from livewall.backends.base import BackendApplyError, BackendUnavailableError


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(caelestia_aw, "CURRENT_WALLPAPER_STATE", tmp_path / "path.txt")
    return caelestia_aw.CaelestiaAwBackend()


def test_is_available(monkeypatch, backend):
    monkeypatch.setattr(caelestia_aw.shutil, "which", lambda name: "/usr/bin/caelestia")
    assert backend.is_available()
    monkeypatch.setattr(caelestia_aw.shutil, "which", lambda name: None)
    assert not backend.is_available()


def test_current_path_reads_state_file(backend):
    assert backend.current_path() is None  # file doesn't exist yet

    caelestia_aw.CURRENT_WALLPAPER_STATE.write_text("/home/user/Pictures/sunset.mp4\n")
    from pathlib import Path

    assert backend.current_path() == Path("/home/user/Pictures/sunset.mp4")


def test_current_path_empty_file_is_none(backend):
    caelestia_aw.CURRENT_WALLPAPER_STATE.write_text("")
    assert backend.current_path() is None


def test_set_wallpaper_missing_file_raises(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(caelestia_aw.shutil, "which", lambda name: "/usr/bin/caelestia")
    with pytest.raises(FileNotFoundError):
        backend.set_wallpaper(tmp_path / "nope.mp4")


def test_set_wallpaper_unavailable_raises(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(caelestia_aw.shutil, "which", lambda name: None)
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    with pytest.raises(BackendUnavailableError):
        backend.set_wallpaper(video)


def test_set_wallpaper_success_calls_expected_command(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(caelestia_aw.shutil, "which", lambda name: "/usr/bin/caelestia")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(caelestia_aw.subprocess, "run", fake_run)

    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    backend.set_wallpaper(video)

    assert calls == [["caelestia", "wallpaper", "-f", str(video)]]


def test_set_wallpaper_no_smart_appends_flag(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(caelestia_aw.shutil, "which", lambda name: "/usr/bin/caelestia")
    calls = []
    monkeypatch.setattr(
        caelestia_aw.subprocess, "run",
        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )

    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    backend.set_wallpaper(video, no_smart=True)

    assert calls[0][-1] == "--no-smart"


def test_set_wallpaper_failure_raises_backend_apply_error(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(caelestia_aw.shutil, "which", lambda name: "/usr/bin/caelestia")

    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="something went wrong")

    monkeypatch.setattr(caelestia_aw.subprocess, "run", fake_run)

    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    with pytest.raises(BackendApplyError, match="something went wrong"):
        backend.set_wallpaper(video)


def test_set_wallpaper_timeout_raises_backend_apply_error(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(caelestia_aw.shutil, "which", lambda name: "/usr/bin/caelestia")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(caelestia_aw.subprocess, "run", fake_run)

    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    with pytest.raises(BackendApplyError, match="timed out"):
        backend.set_wallpaper(video)


def test_stop_is_a_true_noop(backend):
    # Explicitly never kills the user's Caelestia shell as a side effect of
    # switching backends — see the docstring in caelestia_aw.py.
    backend.stop()  # should not raise, not call subprocess at all


def test_ensure_playing_no_wallpaper_applied(backend):
    assert backend.ensure_playing() == "no wallpaper applied, nothing to check"


def test_ensure_playing_non_video_current_wallpaper(backend):
    caelestia_aw.CURRENT_WALLPAPER_STATE.write_text("/home/user/Pictures/static.png")
    result = backend.ensure_playing()
    assert "isn't a video" in result


def test_capabilities():
    backend = caelestia_aw.CaelestiaAwBackend()
    assert backend.supports_pause is False
    assert backend.supports_resume is False
    assert backend.supports_boot_fix is True
    assert backend.restores_on_login is True
