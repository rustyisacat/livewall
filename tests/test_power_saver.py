from __future__ import annotations

import pytest

from livewall import power_saver
from livewall.backends.base import WallpaperBackend


class FakeBackend(WallpaperBackend):
    name = "fake"
    supports_pause = True
    supports_resume = True

    def __init__(self):
        self.paused = False
        self.pause_calls = 0
        self.resume_calls = 0

    def is_available(self):
        return True

    def is_running(self):
        return True

    def current_path(self):
        return None

    def set_wallpaper(self, path, *, no_smart=False):
        pass

    def stop(self):
        pass

    def pause(self):
        self.paused = True
        self.pause_calls += 1

    def resume(self):
        self.paused = False
        self.resume_calls += 1


class NoPauseBackend(FakeBackend):
    supports_pause = False
    supports_resume = False


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(power_saver, "STATE_FILE", tmp_path / "power_saver_state.json")


def test_backend_without_pause_support_is_a_noop(isolated_state, monkeypatch):
    monkeypatch.setattr(power_saver, "_read_battery_percent", lambda: 5)
    backend = NoPauseBackend()
    result = power_saver.check(backend, low=15, high=25)
    assert "doesn't support pause/resume" in result
    assert backend.pause_calls == 0


def test_no_battery_is_a_noop(isolated_state, monkeypatch):
    monkeypatch.setattr(power_saver, "_read_battery_percent", lambda: None)
    backend = FakeBackend()
    result = power_saver.check(backend, low=15, high=25)
    assert "no battery detected" in result
    assert backend.pause_calls == 0


def test_pauses_when_crossing_low_threshold(isolated_state, monkeypatch):
    monkeypatch.setattr(power_saver, "_read_battery_percent", lambda: 10)
    backend = FakeBackend()
    result = power_saver.check(backend, low=15, high=25)
    assert backend.pause_calls == 1
    assert "paused" in result


def test_stays_paused_between_thresholds_no_redundant_calls(isolated_state, monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(power_saver, "_read_battery_percent", lambda: 10)
    power_saver.check(backend, low=15, high=25)  # pauses
    assert backend.pause_calls == 1

    monkeypatch.setattr(power_saver, "_read_battery_percent", lambda: 18)  # between low/high
    result = power_saver.check(backend, low=15, high=25)
    assert backend.pause_calls == 1  # no redundant pause call
    assert backend.resume_calls == 0
    assert "no change (paused)" in result


def test_resumes_when_crossing_high_threshold(isolated_state, monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(power_saver, "_read_battery_percent", lambda: 10)
    power_saver.check(backend, low=15, high=25)  # pauses

    monkeypatch.setattr(power_saver, "_read_battery_percent", lambda: 30)
    result = power_saver.check(backend, low=15, high=25)
    assert backend.resume_calls == 1
    assert "resumed" in result


def test_no_redundant_pause_when_already_active(isolated_state, monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(power_saver, "_read_battery_percent", lambda: 5)
    power_saver.check(backend, low=15, high=25)
    assert backend.pause_calls == 1

    # still below the low threshold on the next check — must not pause again
    power_saver.check(backend, low=15, high=25)
    assert backend.pause_calls == 1


def test_no_change_while_playing_above_low(isolated_state, monkeypatch):
    monkeypatch.setattr(power_saver, "_read_battery_percent", lambda: 80)
    backend = FakeBackend()
    result = power_saver.check(backend, low=15, high=25)
    assert backend.pause_calls == 0
    assert "no change (playing)" in result


def test_is_available_reflects_battery_presence(monkeypatch):
    monkeypatch.setattr(power_saver, "_read_battery_percent", lambda: 50)
    assert power_saver.is_available()
    monkeypatch.setattr(power_saver, "_read_battery_percent", lambda: None)
    assert not power_saver.is_available()


def test_read_battery_percent_linux_no_power_supply_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(power_saver, "POWER_SUPPLY_DIR", tmp_path / "does_not_exist")
    assert power_saver._read_battery_percent_linux() is None


def test_read_battery_percent_linux_finds_battery(monkeypatch, tmp_path):
    supply_dir = tmp_path / "power_supply"
    battery = supply_dir / "BAT0"
    battery.mkdir(parents=True)
    (battery / "type").write_text("Battery\n")
    (battery / "capacity").write_text("42\n")

    ac = supply_dir / "AC"
    ac.mkdir()
    (ac / "type").write_text("Mains\n")

    monkeypatch.setattr(power_saver, "POWER_SUPPLY_DIR", supply_dir)
    assert power_saver._read_battery_percent_linux() == 42
