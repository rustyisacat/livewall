"""What's testable on Linux: pathenv's frozen-build guard and its
already-on-PATH dedup logic (mocked winreg). The real registry write and
WM_SETTINGCHANGE broadcast can't be exercised without a Windows machine —
same limitation as every other windows/ module in this project."""

from __future__ import annotations

from pathlib import Path

from livewall.windows import pathenv

# A fake install dir expressed with POSIX-style separators — Windows-style
# backslash paths aren't parsed as multi-component paths by pathlib on
# Linux (no test machine has ever run this against real Windows paths),
# so _install_dir() itself is mocked directly instead of faking
# sys.executable/sys.frozen through it.
_FAKE_INSTALL_DIR = Path("/fake/LiveWall-windows")


def test_is_on_path_true_when_not_frozen(monkeypatch):
    # Only a packaged build has a real install directory to add.
    monkeypatch.setattr(pathenv, "_install_dir", lambda: None)
    assert pathenv.is_on_path() is True


def test_add_to_path_noop_when_not_frozen(monkeypatch):
    monkeypatch.setattr(pathenv, "_install_dir", lambda: None)
    pathenv.add_to_path()  # must not raise, must not touch winreg


def test_add_to_path_skips_when_already_present(monkeypatch):
    monkeypatch.setattr(pathenv, "_install_dir", lambda: _FAKE_INSTALL_DIR)
    monkeypatch.setattr(
        pathenv, "_current_path_entries", lambda: (["/usr/bin", str(_FAKE_INSTALL_DIR)], 2)
    )

    calls = []
    monkeypatch.setattr(pathenv, "_broadcast_environment_change", lambda: calls.append(1))

    pathenv.add_to_path()

    assert calls == []  # never broadcast — nothing was actually added


def test_is_on_path_case_insensitive(monkeypatch):
    monkeypatch.setattr(pathenv, "_install_dir", lambda: _FAKE_INSTALL_DIR)
    monkeypatch.setattr(pathenv, "_current_path_entries", lambda: ([str(_FAKE_INSTALL_DIR).upper()], 2))

    assert pathenv.is_on_path() is True
