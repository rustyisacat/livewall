"""Windows-only ctypes calls (_pid_alive, _terminate, SystemParametersInfoW,
the actual subprocess spawns) can't run on Linux — those are mocked out
here so the surrounding state-machine logic (target-keyed state, host
reuse, the ALL<->per-monitor transition) can still be verified. See the
SIGPIPE regression test file for the stderr-pipe source guards."""

from __future__ import annotations

import json
import sys

import pytest

from livewall.backends import windows_mpv
from livewall.backends.base import BackendApplyError


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(windows_mpv, "STATE_FILE", tmp_path / "windows_mpv_state.json")
    monkeypatch.setattr(windows_mpv, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(windows_mpv, "HOST_STDERR_LOG", tmp_path / "host_stderr.log")
    return tmp_path


@pytest.fixture
def backend(isolated):
    return windows_mpv.WindowsMpvBackend()


def _write_state(state: dict) -> None:
    windows_mpv.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    windows_mpv.STATE_FILE.write_text(json.dumps(state))


def test_is_available_false_on_non_windows():
    if sys.platform == "win32":
        pytest.skip("this checks the non-Windows guard specifically")
    assert windows_mpv.WindowsMpvBackend().is_available() is False


def test_module_imports_without_windows():
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
    assert backend.supports_multi_monitor is True
    assert backend.supports_per_monitor is True


def test_list_monitor_targets_empty_on_non_windows(backend):
    if sys.platform == "win32":
        pytest.skip("this checks the non-Windows guard specifically")
    assert backend.list_monitor_targets() == []


# ---- state read/migration -----------------------------------------------


def test_read_state_empty_when_missing(backend):
    assert backend._read_state() == {}


def test_read_state_migrates_old_flat_format(backend, tmp_path):
    video = tmp_path / "a.mp4"
    _write_state({"host_pid": 1, "mpv_pid": 2, "path": str(video), "static": False})

    state = backend._read_state()
    assert state["targets"] == {"ALL": {"pid": 2, "path": str(video), "static": False}}
    # the old host_pid is deliberately not carried forward — can't be
    # reused without a hwnd map, see the comment in _read_state()
    assert state["host_pid"] is None


def test_write_state_removes_file_when_no_targets(backend, tmp_path):
    _write_state({"targets": {"ALL": {"pid": 1, "path": "x", "static": False}}})
    assert windows_mpv.STATE_FILE.exists()
    backend._write_state({"targets": {}, "host_pid": None, "host_hwnds": {}})
    assert not windows_mpv.STATE_FILE.exists()


def test_current_path_and_current_path_for_monitor(backend, tmp_path, monkeypatch):
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    _write_state({
        "host_pid": 1, "host_hwnds": {},
        "targets": {
            "ALL": {"pid": 10, "path": str(a), "static": False},
            "eDP-1": {"pid": 11, "path": str(b), "static": False},
        },
    })
    monkeypatch.setattr(windows_mpv, "_pid_alive", lambda pid: True)

    assert backend.current_path() == a
    assert backend.current_path_for_monitor("eDP-1") == b
    assert backend.current_path_for_monitor("DP-2") is None


def test_last_applied_paths_by_monitor_excludes_all(backend, tmp_path):
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    _write_state({
        "host_pid": 1, "host_hwnds": {},
        "targets": {
            "ALL": {"pid": 10, "path": str(a), "static": False},
            "eDP-1": {"pid": 11, "path": str(b), "static": False},
        },
    })
    assert backend.last_applied_paths_by_monitor() == {"eDP-1": b}


# ---- stop / host lifecycle ------------------------------------------------


def test_stop_target_removes_entry_and_kills_pid(backend, tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(windows_mpv, "_pid_alive", lambda pid: pid not in killed)
    monkeypatch.setattr(windows_mpv, "_terminate", lambda pid: killed.append(pid))

    state = {
        "host_pid": 1, "host_hwnds": {"ALL": 100},
        "targets": {
            "ALL": {"pid": 10, "path": "a", "static": False},
            "eDP-1": {"pid": 11, "path": "b", "static": False},
        },
    }
    state = backend._stop_target("ALL", state)
    assert "ALL" not in state["targets"]
    assert "eDP-1" in state["targets"]
    assert 10 in killed
    # host survives — another target still needs it
    assert state["host_pid"] == 1


def test_stop_target_kills_host_when_last_target_removed(backend, monkeypatch):
    monkeypatch.setattr(windows_mpv, "_pid_alive", lambda pid: True)
    killed = []
    monkeypatch.setattr(windows_mpv, "_terminate", lambda pid: killed.append(pid))

    state = {
        "host_pid": 1, "host_hwnds": {"ALL": 100},
        "targets": {"ALL": {"pid": 10, "path": "a", "static": False}},
    }
    state = backend._stop_target("ALL", state)
    assert state["targets"] == {}
    assert state["host_pid"] is None
    assert state["host_hwnds"] == {}
    assert 1 in killed  # host was killed too


def test_stop_target_static_entry_has_nothing_to_kill(backend, monkeypatch):
    calls = []
    monkeypatch.setattr(windows_mpv, "_terminate", lambda pid: calls.append(pid))
    state = {"host_pid": None, "host_hwnds": {}, "targets": {"ALL": {"pid": None, "path": "a", "static": True}}}
    state = backend._stop_target("ALL", state)
    assert state["targets"] == {}
    assert calls == []


def test_get_or_start_host_reuses_live_host(backend, monkeypatch):
    monkeypatch.setattr(windows_mpv, "_pid_alive", lambda pid: True)
    spawn_calls = []
    monkeypatch.setattr(backend, "_spawn_host", lambda: spawn_calls.append(1) or (999, {"ALL": 1}))

    state = {"host_pid": 42, "host_hwnds": {"ALL": 111}, "targets": {}}
    host_pid, hwnds = backend._get_or_start_host(state)

    assert host_pid == 42
    assert hwnds == {"ALL": 111}
    assert spawn_calls == []  # never re-spawned


def test_get_or_start_host_spawns_fresh_when_host_dead(backend, monkeypatch):
    monkeypatch.setattr(windows_mpv, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(backend, "_spawn_host", lambda: (999, {"ALL": 222}))

    state = {"host_pid": 42, "host_hwnds": {"ALL": 111}, "targets": {}}
    host_pid, hwnds = backend._get_or_start_host(state)

    assert host_pid == 999
    assert hwnds == {"ALL": 222}


# ---- apply / per-monitor transition ---------------------------------------


def _stub_mpv_available(backend) -> None:
    # shutil.which("mpv") would fail on this Linux box regardless of PATH
    # contents — bypass by stubbing _mpv_path directly where needed.
    backend._mpv_path = lambda: "mpv.exe"


def test_set_wallpaper_for_monitor_rejects_static_extension(backend, tmp_path):
    image = tmp_path / "a.png"
    image.write_bytes(b"x")
    _stub_mpv_available(backend)
    with pytest.raises(BackendApplyError, match="per-monitor static"):
        backend.set_wallpaper_for_monitor("eDP-1", image)


def test_set_wallpaper_for_monitor_writes_only_that_target(backend, tmp_path, monkeypatch):
    _stub_mpv_available(backend)
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")

    monkeypatch.setattr(backend, "_apply_animated_target", lambda target, path, state: {
        **state, "targets": {**state.get("targets", {}), target: {"pid": 1, "path": str(path), "static": False}},
    })

    backend.set_wallpaper_for_monitor("eDP-1", video)

    state = json.loads(windows_mpv.STATE_FILE.read_text())
    assert set(state["targets"]) == {"eDP-1"}


def test_set_wallpaper_for_monitor_preserves_other_monitors_from_all(backend, tmp_path, monkeypatch):
    _stub_mpv_available(backend)
    old = tmp_path / "old.mp4"
    old.write_bytes(b"old")
    new = tmp_path / "new.mp4"
    new.write_bytes(b"new")

    _write_state({
        "host_pid": 1, "host_hwnds": {"ALL": 100},
        "targets": {"ALL": {"pid": 10, "path": str(old), "static": False}},
    })
    monkeypatch.setattr(windows_mpv, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(windows_mpv, "_terminate", lambda pid: None)
    monkeypatch.setattr(backend, "list_monitor_targets", lambda: ["eDP-1", "DP-2"])

    applied = []

    def fake_apply(target, path, state):
        applied.append((target, str(path)))
        state.setdefault("targets", {})[target] = {"pid": len(applied), "path": str(path), "static": False}
        return state

    monkeypatch.setattr(backend, "_apply_animated_target", fake_apply)

    backend.set_wallpaper_for_monitor("eDP-1", new)

    assert ("DP-2", str(old)) in applied  # preserved
    assert ("eDP-1", str(new)) in applied  # the actual assignment
    state = json.loads(windows_mpv.STATE_FILE.read_text())
    assert "ALL" not in state["targets"]
    assert state["targets"]["DP-2"]["path"] == str(old)
    assert state["targets"]["eDP-1"]["path"] == str(new)


# ---- pause/resume -----------------------------------------------------


def test_pause_resume_skip_static_targets(backend, tmp_path, monkeypatch):
    _write_state({
        "host_pid": 1, "host_hwnds": {},
        "targets": {
            "ALL": {"pid": None, "path": "a", "static": True},
            "eDP-1": {"pid": 5, "path": "b", "static": False},
        },
    })
    monkeypatch.setattr(windows_mpv, "_pid_alive", lambda pid: True)

    sent = []
    monkeypatch.setattr(windows_mpv.WindowsMpvBackend, "_mpv_ipc", lambda self, target, cmd: sent.append(target) or {})

    backend.pause()
    assert sent == ["eDP-1"]  # ALL is static — nothing to pause there


def test_health_check_reports_each_target(backend, tmp_path, monkeypatch):
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    b = tmp_path / "b.mp4"
    b.write_bytes(b"x")
    _write_state({
        "host_pid": 1, "host_hwnds": {},
        "targets": {
            "eDP-1": {"pid": 1, "path": str(a), "static": False},
            "DP-2": {"pid": 2, "path": str(b), "static": False},
        },
    })
    monkeypatch.setattr(windows_mpv, "_pid_alive", lambda pid: True)
    _stub_mpv_available(backend)

    checks = backend.health_check()
    labels = {c[0] for c in checks}
    assert "current wallpaper (eDP-1)" in labels
    assert "current wallpaper (DP-2)" in labels


# ---- mpv invocation regression ---------------------------------------
#
# Real bug found via a genuine Windows test session: this used to build
# `["-o", "loop-file=inf no-audio load-scripts=no input-ipc-server=..."]`
# — "-o" is mpv's real short form of --o=<file>, the ENCODE-to-file option,
# so mpv silently went into encode mode targeting a garbage filename and
# fatal-errored instead of ever rendering. "applied successfully" (mpv
# spawned, _spawn_mpv() didn't raise) but nothing ever appeared on screen.


class _FakeProc:
    pid = 4242

    def poll(self):
        return None  # still running


def test_spawn_mpv_never_uses_bare_dash_o_flag(backend, tmp_path, monkeypatch):
    _stub_mpv_available(backend)
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")

    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(windows_mpv.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(windows_mpv.time, "sleep", lambda _seconds: None)

    backend._spawn_mpv("ALL", 12345, video)

    cmd = captured["cmd"]
    assert "-o" not in cmd, "must never pass mpv's real --o=<file> encode-mode flag"
    assert "--wid=12345" in cmd
    assert "--loop-file=inf" in cmd
    assert "--no-audio" in cmd
    assert "--load-scripts=no" in cmd
    assert "--panscan=1.0" in cmd  # fill the screen instead of letterboxing
    assert "--profile=gpu-hq" in cmd  # higher-quality upscaling than plain bilinear
    assert any(arg.startswith("--input-ipc-server=") for arg in cmd)
    assert cmd[-1] == str(video)
