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


def _fake_popen_ok(pid=999):
    return lambda *a, **kw: FakeProc(pid=pid)


def _write_state(state: dict) -> None:
    mpvpaper.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    mpvpaper.STATE_FILE.write_text(json.dumps(state))


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


def test_set_wallpaper_success_writes_all_target_state(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: "/usr/bin/mpvpaper")
    monkeypatch.setattr(mpvpaper.subprocess, "Popen", _fake_popen_ok(pid=999))
    monkeypatch.setattr(mpvpaper.time, "sleep", lambda *_: None)

    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    backend.set_wallpaper(video)

    state = json.loads(mpvpaper.STATE_FILE.read_text())
    assert state == {"ALL": {"pid": 999, "path": str(video)}}


def test_set_wallpaper_uses_all_as_the_mpvpaper_output_arg(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: "/usr/bin/mpvpaper")
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        return FakeProc(pid=999)

    monkeypatch.setattr(mpvpaper.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(mpvpaper.time, "sleep", lambda *_: None)

    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    backend.set_wallpaper(video)

    assert captured["cmd"][-2:] == ["ALL", str(video)]


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
    _write_state({"ALL": {"pid": 4242, "path": str(video)}})

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


def test_reads_old_flat_state_format_as_implicit_all_target(backend, tmp_path, monkeypatch):
    # Pre-per-monitor format, from before this feature existed.
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    _write_state({"pid": 777, "path": str(video)})

    monkeypatch.setattr(backend, "_pid_alive", lambda pid: pid == 777)
    assert backend.is_running()
    assert backend.current_path() == video
    assert backend.last_applied_path() == video


def test_stop_sends_sigterm_then_clears_state(monkeypatch, backend, tmp_path):
    _write_state({"ALL": {"pid": 555, "path": str(tmp_path / "a.mp4")}})

    calls = []
    monkeypatch.setattr(mpvpaper.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    # Alive once (so stop() attempts SIGTERM), then reports dead immediately after.
    alive_sequence = iter([True, False])
    monkeypatch.setattr(backend, "_pid_alive", lambda pid: next(alive_sequence, False))

    backend.stop()

    assert calls == [(555, signal.SIGTERM)]
    assert not mpvpaper.STATE_FILE.exists()


def test_stop_escalates_to_sigkill_if_still_alive(monkeypatch, backend, tmp_path):
    _write_state({"ALL": {"pid": 555, "path": str(tmp_path / "a.mp4")}})

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


def test_stop_kills_every_tracked_target(monkeypatch, backend, tmp_path):
    _write_state({
        "ALL": {"pid": 1, "path": str(tmp_path / "a.mp4")},
        "eDP-1": {"pid": 2, "path": str(tmp_path / "b.mp4")},
    })
    calls = []
    monkeypatch.setattr(mpvpaper.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    # Alive the first time _pid_alive is asked about each pid (so stop()
    # attempts SIGTERM on both), dead every time after.
    seen = set()

    def fake_pid_alive(pid):
        if pid in seen:
            return False
        seen.add(pid)
        return True

    monkeypatch.setattr(backend, "_pid_alive", fake_pid_alive)

    backend.stop()

    assert {c[0] for c in calls} == {1, 2}
    assert not mpvpaper.STATE_FILE.exists()


def test_pause_resume_is_paused_via_ipc(monkeypatch, backend, tmp_path):
    _write_state({"ALL": {"pid": 1, "path": str(tmp_path / "a.mp4")}})
    monkeypatch.setattr(backend, "_pid_alive", lambda pid: True)

    sent_commands = []

    def fake_ipc(self, target, command):
        sent_commands.append((target, command))
        if command[0] == "get_property":
            return {"data": True}
        return {}

    monkeypatch.setattr(mpvpaper.MpvpaperBackend, "_mpv_ipc", fake_ipc)

    backend.pause()
    backend.resume()
    assert backend.is_paused() is True
    assert sent_commands == [
        ("ALL", ["set_property", "pause", True]),
        ("ALL", ["set_property", "pause", False]),
        ("ALL", ["get_property", "pause"]),
    ]


def test_pause_resume_acts_on_every_live_target(monkeypatch, backend, tmp_path):
    _write_state({
        "eDP-1": {"pid": 1, "path": str(tmp_path / "a.mp4")},
        "DP-2": {"pid": 2, "path": str(tmp_path / "b.mp4")},
    })
    monkeypatch.setattr(backend, "_pid_alive", lambda pid: True)

    sent_targets = []
    monkeypatch.setattr(
        mpvpaper.MpvpaperBackend, "_mpv_ipc",
        lambda self, target, command: sent_targets.append(target) or {},
    )

    backend.pause()
    assert set(sent_targets) == {"eDP-1", "DP-2"}


def test_is_paused_none_when_nothing_running(backend):
    assert backend.is_paused() is None


def test_health_check_reports_current_wallpaper(monkeypatch, backend, tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    _write_state({"ALL": {"pid": 1, "path": str(video)}})
    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: "/usr/bin/mpvpaper")
    monkeypatch.setattr(backend, "_pid_alive", lambda pid: True)

    checks = backend.health_check()
    names = {c[0] for c in checks}
    assert "mpvpaper CLI" in names
    assert all(ok for _name, ok, _detail in checks)


def test_health_check_reports_each_per_monitor_target(monkeypatch, backend, tmp_path):
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    b = tmp_path / "b.mp4"
    b.write_bytes(b"x")
    _write_state({"eDP-1": {"pid": 1, "path": str(a)}, "DP-2": {"pid": 2, "path": str(b)}})
    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: "/usr/bin/mpvpaper")
    monkeypatch.setattr(backend, "_pid_alive", lambda pid: True)

    checks = backend.health_check()
    labels = {c[0] for c in checks}
    assert "current wallpaper (eDP-1)" in labels
    assert "current wallpaper (DP-2)" in labels


# ---- per-monitor -----------------------------------------------------


def test_list_monitor_targets_delegates_to_hypr(monkeypatch, backend):
    from livewall import hypr

    monkeypatch.setattr(hypr, "list_monitors", lambda: ["eDP-1", "DP-2"])
    assert backend.list_monitor_targets() == ["eDP-1", "DP-2"]


def test_set_wallpaper_for_monitor_missing_file_raises(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: "/usr/bin/mpvpaper")
    with pytest.raises(FileNotFoundError):
        backend.set_wallpaper_for_monitor("eDP-1", tmp_path / "nope.mp4")


def test_set_wallpaper_for_monitor_writes_only_that_targets_entry(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: "/usr/bin/mpvpaper")
    monkeypatch.setattr(mpvpaper.subprocess, "Popen", _fake_popen_ok(pid=42))
    monkeypatch.setattr(mpvpaper.time, "sleep", lambda *_: None)

    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    backend.set_wallpaper_for_monitor("eDP-1", video)

    state = json.loads(mpvpaper.STATE_FILE.read_text())
    assert state == {"eDP-1": {"pid": 42, "path": str(video)}}


def test_set_wallpaper_for_monitor_uses_monitor_as_output_arg(monkeypatch, backend, tmp_path):
    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: "/usr/bin/mpvpaper")
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        return FakeProc(pid=1)

    monkeypatch.setattr(mpvpaper.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(mpvpaper.time, "sleep", lambda *_: None)

    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    backend.set_wallpaper_for_monitor("DP-2", video)

    assert captured["cmd"][-2:] == ["DP-2", str(video)]


def test_set_wallpaper_for_monitor_preserves_other_monitors_from_all(monkeypatch, backend, tmp_path):
    from livewall import hypr

    monkeypatch.setattr(mpvpaper.shutil, "which", lambda name: "/usr/bin/mpvpaper")
    monkeypatch.setattr(hypr, "list_monitors", lambda: ["eDP-1", "DP-2"])
    monkeypatch.setattr(mpvpaper.time, "sleep", lambda *_: None)
    monkeypatch.setattr(backend, "_pid_alive", lambda pid: True)

    old = tmp_path / "old.mp4"
    old.write_bytes(b"old")
    new = tmp_path / "new.mp4"
    new.write_bytes(b"new")

    # ALL is currently mirroring `old` on both monitors.
    _write_state({"ALL": {"pid": 1, "path": str(old)}})

    spawned = []

    def fake_popen(cmd, **kw):
        spawned.append(cmd[-2:])
        return FakeProc(pid=100 + len(spawned))

    monkeypatch.setattr(mpvpaper.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(mpvpaper.os, "kill", lambda *a: None)

    # Assign eDP-1 a new wallpaper — DP-2 (not being reassigned) should keep
    # showing `old`, explicitly, instead of going dark.
    backend.set_wallpaper_for_monitor("eDP-1", new)

    state = json.loads(mpvpaper.STATE_FILE.read_text())
    assert "ALL" not in state
    assert state["eDP-1"]["path"] == str(new)
    assert state["DP-2"]["path"] == str(old)
    assert [str(old)] == [c[1] for c in spawned if c[0] == "DP-2"]
    assert [str(new)] == [c[1] for c in spawned if c[0] == "eDP-1"]


def test_current_path_for_monitor(backend, tmp_path, monkeypatch):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    _write_state({"eDP-1": {"pid": 1, "path": str(video)}})
    monkeypatch.setattr(backend, "_pid_alive", lambda pid: True)

    assert backend.current_path_for_monitor("eDP-1") == video
    assert backend.current_path_for_monitor("DP-2") is None


def test_last_applied_paths_by_monitor_excludes_all(backend, tmp_path):
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    b = tmp_path / "b.mp4"
    b.write_bytes(b"x")
    _write_state({
        "ALL": {"pid": 1, "path": str(a)},
        "eDP-1": {"pid": 2, "path": str(b)},
    })
    assert backend.last_applied_paths_by_monitor() == {"eDP-1": b}
