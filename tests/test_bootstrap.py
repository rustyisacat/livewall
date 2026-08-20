"""ensure_first_run_setup() is the one place this project silently enables
something instead of asking first (see bootstrap.py's module docstring for
why) — these tests lean hard on the one-shot flag actually being one-shot,
and on failures never propagating (this runs unattended on every CLI/GUI
launch)."""

from __future__ import annotations

from livewall import bootstrap
from livewall.config import Config


def test_linux_first_run_installs_update_checker(redirect_paths, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    from livewall import systemd

    monkeypatch.setattr(systemd, "is_update_checker_installed", lambda: False)
    calls = []
    monkeypatch.setattr(systemd, "install_update_checker", lambda: calls.append(1))

    config = Config()
    bootstrap.ensure_first_run_setup(config)

    assert calls == [1]
    assert config.did_first_run_setup is True
    assert Config.load().did_first_run_setup is True  # persisted


def test_linux_skips_install_call_if_already_installed(redirect_paths, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    from livewall import systemd

    monkeypatch.setattr(systemd, "is_update_checker_installed", lambda: True)
    calls = []
    monkeypatch.setattr(systemd, "install_update_checker", lambda: calls.append(1))

    config = Config()
    bootstrap.ensure_first_run_setup(config)

    assert calls == []
    assert config.did_first_run_setup is True


def test_second_run_is_a_noop(redirect_paths, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    from livewall import systemd

    calls = []
    monkeypatch.setattr(systemd, "is_update_checker_installed", lambda: False)
    monkeypatch.setattr(systemd, "install_update_checker", lambda: calls.append(1))

    config = Config(did_first_run_setup=True)
    bootstrap.ensure_first_run_setup(config)

    assert calls == []  # already done once — never re-triggered


def test_failure_is_swallowed_and_flag_still_set(redirect_paths, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    from livewall import systemd

    def raise_it():
        raise RuntimeError("no systemctl on this box")

    monkeypatch.setattr(systemd, "is_update_checker_installed", lambda: False)
    monkeypatch.setattr(systemd, "install_update_checker", raise_it)

    config = Config()
    bootstrap.ensure_first_run_setup(config)  # must not raise

    assert config.did_first_run_setup is True


def test_windows_first_run_enables_autostart(redirect_paths, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    from livewall.windows import startup

    monkeypatch.setattr(startup, "is_autostart_installed", lambda: False)
    calls = []
    monkeypatch.setattr(startup, "install_autostart", lambda: calls.append(1))

    config = Config()
    bootstrap.ensure_first_run_setup(config)

    assert calls == [1]
    assert config.did_first_run_setup is True


def test_windows_skips_if_autostart_already_on(redirect_paths, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    from livewall.windows import startup

    monkeypatch.setattr(startup, "is_autostart_installed", lambda: True)
    calls = []
    monkeypatch.setattr(startup, "install_autostart", lambda: calls.append(1))

    config = Config()
    bootstrap.ensure_first_run_setup(config)

    assert calls == []
    assert config.did_first_run_setup is True


def test_windows_first_run_adds_to_path(redirect_paths, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    from livewall.windows import pathenv, startup

    monkeypatch.setattr(startup, "is_autostart_installed", lambda: True)  # not under test here
    monkeypatch.setattr(pathenv, "is_on_path", lambda: False)
    calls = []
    monkeypatch.setattr(pathenv, "add_to_path", lambda: calls.append(1))

    config = Config()
    bootstrap.ensure_first_run_setup(config)

    assert calls == [1]


def test_windows_skips_path_when_already_on_it(redirect_paths, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    from livewall.windows import pathenv, startup

    monkeypatch.setattr(startup, "is_autostart_installed", lambda: True)
    monkeypatch.setattr(pathenv, "is_on_path", lambda: True)
    calls = []
    monkeypatch.setattr(pathenv, "add_to_path", lambda: calls.append(1))

    config = Config()
    bootstrap.ensure_first_run_setup(config)

    assert calls == []
