"""Coverage for the --monitor flag on apply/random, the `monitors` command,
and per-monitor-aware status/restore — the CLI-level wiring for
per-monitor wallpapers (see test_backends_mpvpaper.py for the backend
logic itself)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from livewall import cli, rotation
from livewall.backends.base import BackendApplyError, BackendUnavailableError, WallpaperBackend
from livewall.config import Config
from livewall.database import Database
from livewall.library import Library


class FakePerMonitorBackend(WallpaperBackend):
    name = "fake-per-monitor"
    supports_per_monitor = True

    def __init__(self, monitors=("eDP-1", "DP-2")):
        self._monitors = list(monitors)
        self.all_path: Path | None = None
        self.per_monitor: dict[str, Path] = {}
        self.raise_on_apply: Exception | None = None

    def is_available(self):
        return True

    def is_running(self):
        return self.all_path is not None or bool(self.per_monitor)

    def current_path(self):
        return self.all_path

    def current_path_for_monitor(self, monitor):
        return self.per_monitor.get(monitor)

    def list_monitor_targets(self):
        return self._monitors

    def set_wallpaper(self, path, *, no_smart=False):
        if self.raise_on_apply:
            raise self.raise_on_apply
        self.all_path = path
        self.per_monitor = {}

    def set_wallpaper_for_monitor(self, monitor, path, *, no_smart=False):
        if self.raise_on_apply:
            raise self.raise_on_apply
        self.per_monitor[monitor] = path
        self.all_path = None

    def last_applied_paths_by_monitor(self):
        return dict(self.per_monitor)

    def stop(self):
        self.all_path = None
        self.per_monitor = {}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from livewall import thumbnail

    monkeypatch.setattr(rotation, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(
        thumbnail, "probe",
        lambda path: thumbnail.Metadata(None, None, None, True, None),
    )
    monkeypatch.setattr(thumbnail, "generate", lambda path, cache_key, metadata=None: None)


@pytest.fixture
def library(tmp_path):
    lib = Library(db=Database(path=tmp_path / "library.json"))
    for name in ("a", "b"):
        path = tmp_path / f"{name}.mp4"
        path.write_bytes(name.encode())
        lib.add(path, name=name)
    return lib


def make_args(**overrides):
    defaults = dict(tag=None, favorites=False, no_smart=False, monitor=None)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---- monitors command --------------------------------------------------


def test_cmd_monitors_lists_targets(capsys):
    backend = FakePerMonitorBackend(monitors=["eDP-1", "DP-2"])
    result = cli.cmd_monitors(argparse.Namespace(), backend)
    assert result == 0
    out = capsys.readouterr().out
    assert "eDP-1" in out
    assert "DP-2" in out


def test_cmd_monitors_unsupported_backend(capsys):
    class NoPerMonitor(WallpaperBackend):
        name = "plain"

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

    result = cli.cmd_monitors(argparse.Namespace(), NoPerMonitor())
    assert result == 0
    assert "doesn't support" in capsys.readouterr().out


def test_cmd_monitors_none_detected(capsys):
    backend = FakePerMonitorBackend(monitors=[])
    cli.cmd_monitors(argparse.Namespace(), backend)
    assert "No monitors detected" in capsys.readouterr().out


# ---- apply --monitor ----------------------------------------------------


def test_apply_with_monitor(library, capsys):
    backend = FakePerMonitorBackend()
    config = Config()
    args = argparse.Namespace(name="a", no_smart=False, monitor="eDP-1")
    result = cli.cmd_apply(args, library, config, backend)
    assert result == 0
    assert backend.per_monitor["eDP-1"].name == "a.mp4"
    assert "on eDP-1" in capsys.readouterr().out


def test_apply_with_unknown_monitor_errors(library, capsys):
    backend = FakePerMonitorBackend(monitors=["eDP-1"])
    config = Config()
    args = argparse.Namespace(name="a", no_smart=False, monitor="DP-9")
    result = cli.cmd_apply(args, library, config, backend)
    assert result == 1
    assert "unknown monitor" in capsys.readouterr().err


def test_apply_monitor_on_unsupported_backend_errors(library, capsys):
    class NoPerMonitor(WallpaperBackend):
        name = "plain"

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

    config = Config()
    args = argparse.Namespace(name="a", no_smart=False, monitor="eDP-1")
    result = cli.cmd_apply(args, library, config, NoPerMonitor())
    assert result == 1
    assert "doesn't support per-monitor" in capsys.readouterr().err


# ---- random --monitor ----------------------------------------------------


def test_random_with_monitor_applies_and_uses_per_monitor_history(library):
    backend = FakePerMonitorBackend()
    config = Config()
    args = make_args(monitor="eDP-1")
    result = cli.cmd_random(args, library, config, backend)
    assert result == 0
    assert "eDP-1" in backend.per_monitor
    assert backend.all_path is None
    # picked into eDP-1's own history, not ALL's
    assert rotation._read_history("eDP-1")
    assert rotation._read_history("ALL") == []


def test_random_monitor_uses_current_path_for_monitor_for_repeat_avoidance(library, monkeypatch):
    backend = FakePerMonitorBackend()
    backend.per_monitor["eDP-1"] = library.get("a").file_path
    config = Config()

    captured = {}
    real_pick = rotation.pick_wallpaper

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_pick(*args, **kwargs)

    monkeypatch.setattr(rotation, "pick_wallpaper", spy)

    cli.cmd_random(make_args(monitor="eDP-1"), library, config, backend)
    assert captured["current"] == library.get("a").file_path
    assert captured["target"] == "eDP-1"


# ---- status ---------------------------------------------------------------


def test_status_single_target_unchanged_format(library, capsys):
    backend = FakePerMonitorBackend()
    backend.all_path = library.get("a").file_path
    result = cli.cmd_status(argparse.Namespace(), library, backend)
    assert result == 0
    out = capsys.readouterr().out
    assert out.startswith("Current:")


def test_status_multi_target_lists_each(library, capsys):
    backend = FakePerMonitorBackend()
    backend.per_monitor = {
        "eDP-1": library.get("a").file_path,
        "DP-2": library.get("b").file_path,
    }
    result = cli.cmd_status(argparse.Namespace(), library, backend)
    assert result == 0
    out = capsys.readouterr().out
    assert "eDP-1:" in out
    assert "DP-2:" in out


# ---- restore --------------------------------------------------------------


def test_restore_per_monitor(library, capsys):
    backend = FakePerMonitorBackend()
    backend.per_monitor = {
        "eDP-1": library.get("a").file_path,
        "DP-2": library.get("b").file_path,
    }
    # last_applied_paths_by_monitor() reflects what's already tracked —
    # set_wallpaper_for_monitor being called again during restore should
    # succeed against the same paths.
    result = cli.cmd_restore(argparse.Namespace(), backend)
    assert result == 0
    out = capsys.readouterr().out
    assert "Restored wallpaper on eDP-1" in out
    assert "Restored wallpaper on DP-2" in out


def test_restore_per_monitor_missing_file_errors(library, tmp_path, capsys):
    backend = FakePerMonitorBackend()
    missing = tmp_path / "gone.mp4"
    backend.per_monitor = {"eDP-1": missing}
    result = cli.cmd_restore(argparse.Namespace(), backend)
    assert result == 1
    assert "missing" in capsys.readouterr().err
