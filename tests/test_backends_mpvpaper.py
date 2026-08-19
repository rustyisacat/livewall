from __future__ import annotations

import json
import signal

import pytest

from livewall.backends import mpvpaper
from livewall.backends.base import BackendApplyError, BackendUnavailableError


@pytest.fixture
def backend(redirect_paths):
    return mpvpaper.MpvpaperBackend()


class FakeProc:
    def __init__(self, pid=1234, alive=True, stderr_text=""):
        self.pid = pid
        self._alive = alive
        self.returncode = None if alive else 1
        self.stderr = _FakeStderr(stderr_text)

    def poll(self):
        return None if self._alive else self.returncode


class _FakeStderr:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text


def test_is_available(monkeypatch, backend):
    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: "/usr/bin/mpvpaper")
    assert backend.is_available()
    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: None)
    assert not backend.is_available()


def test_set_wallpaper_missing_file_raises(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: "/usr/bin/mpvpaper")
    with pytest.raises(FileNotFoundError):
        backend.set_wallpaper(tmp_path / "nope.mp4")


def test_set_wallpaper_unavailable_raises(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: None)
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    with pytest.raises(BackendUnavailableError):
        backend.set_wallpaper(video)


def test_set_wallpaper_success_writes_state(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: "/usr/bin/mpvpaper")
    monkeypatch.setattr(mpvpaper.subprocess, "Popen", lambda *a, **kw: FakeProc(pid=999))
    monkeypatch.setattr(mpvpaper.time, "sleep", lambda *_: None)

    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    backend.set_wallpaper(video)

    state = json.loads(mpvpaper.STATE_FILE.read_text())
    assert state == {"pid": 999, "path": str(video)}


def test_set_wallpaper_dies_immediately_raises_with_stderr(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: "/usr/bin/mpvpaper")

    def fake_popen(*args, stderr=None, **kwargs):
        # stderr is a real file handle now (see the regression test file for
        # why) — write into it the way the real subprocess would, instead of
        # the old proc.stderr.read() pipe interface.
        if stderr is not None:
            stderr.write("mpv: fatal error")
        return FakeProc(alive=False)

    monkeypatch.setattr(mpvpaper.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(mpvpaper.time, "sleep", lambda *_: None)

    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    with pytest.raises(BackendApplyError, match="fatal error"):
        backend.set_wallpaper(video)


def test_is_running_and_current_path(monkeypatch, backend, tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    mpvpaper.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    mpvpaper.STATE_FILE.write_text(json.dumps({"pid": 4242, "path": str(video)}))

    monkeypatch.setattr(backend, "_pid_alive", lambda pid: pid == 4242)
    assert backend.is_running()
    assert backend.current_path() == video

    monkeypatch.setattr(backend, "_pid_alive", lambda pid: False)
    assert not backend.is_running()
    assert backend.current_path() is None  # not alive -> nothing "currently" rendering
    # but last_applied_path() ignores liveness (needed for restore-on-boot)
    assert backend.last_applied_path() == video


def test_current_path_none_when_no_state(backend):
    assert backend.current_path() is None
    assert backend.last_applied_path() is None
    assert not backend.is_running()


def test_stop_sends_sigterm_then_clears_state(monkeypatch, backend, tmp_path):
    mpvpaper.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    mpvpaper.STATE_FILE.write_text(json.dumps({"pid": 555, "path": str(tmp_path / "a.mp4")}))

    calls = []
    monkeypatch.setattr(mpvpaper.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    # Alive once (so stop() attempts SIGTERM), then reports dead immediately after.
    alive_sequence = iter([True, False])
    monkeypatch.setattr(backend, "_pid_alive", lambda pid: next(alive_sequence, False))

    backend.stop()

    assert calls == [(555, signal.SIGTERM)]
    assert not mpvpaper.STATE_FILE.exists()


def test_stop_escalates_to_sigkill_if_still_alive(monkeypatch, backend, tmp_path):
    mpvpaper.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    mpvpaper.STATE_FILE.write_text(json.dumps({"pid": 555, "path": str(tmp_path / "a.mp4")}))

    calls = []
    monkeypatch.setattr(mpvpaper.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    monkeypatch.setattr(mpvpaper, "_STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(mpvpaper, "_STOP_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(backend, "_pid_alive", lambda pid: True)  # never dies on its own

    backend.stop()

    assert calls[0] == (555, signal.SIGTERM)
    assert calls[-1] == (555, signal.SIGKILL)


def test_stop_with_no_state_is_a_noop(monkeypatch, backend):
    calls = []
    monkeypatch.setattr(mpvpaper.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    backend.stop()
    assert calls == []


def test_pause_resume_is_paused_via_ipc(monkeypatch, backend, tmp_path):
    mpvpaper.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    mpvpaper.STATE_FILE.write_text(json.dumps({"pid": 1, "path": str(tmp_path / "a.mp4")}))
    monkeypatch.setattr(backend, "_pid_alive", lambda pid: True)  # is_running() -> True

    sent_commands = []

    def fake_ipc(self, command):
        sent_commands.append(command)
        if command[0] == "get_property":
            return {"data": True}
        return {}

    monkeypatch.setattr(mpvpaper.MpvpaperBackend, "_mpv_ipc", fake_ipc)

    backend.pause()
    backend.resume()
    assert backend.is_paused() is True
    assert sent_commands == [
        ["set_property", "pause", True],
        ["set_property", "pause", False],
        ["get_property", "pause"],
    ]


def test_is_paused_none_when_nothing_running(backend):
    assert backend.is_paused() is None


def test_health_check_reports_current_wallpaper(monkeypatch, backend, tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    mpvpaper.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    mpvpaper.STATE_FILE.write_text(json.dumps({"pid": 1, "path": str(video)}))
    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: "/usr/bin/mpvpaper")
    monkeypatch.setattr(backend, "_pid_alive", lambda pid: True)

    checks = backend.health_check()
    names = {c[0] for c in checks}
    assert "mpvpaper CLI" in names
    assert all(ok for _name, ok, _detail in checks)
