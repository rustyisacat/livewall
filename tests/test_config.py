from __future__ import annotations

import json

from livewall.config import Config


def test_defaults():
    config = Config()
    assert config.random_interval == "off"
    assert config.random_favorites_only is False
    assert config.random_tags == []
    assert config.no_smart_colours is False
    assert config.battery_saver_low == 15
    assert config.battery_saver_high == 25


def test_backend_default_is_platform_specific(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "win32")
    assert Config().backend == "windows-mpv"

    monkeypatch.setattr(sys, "platform", "linux")
    assert Config().backend == "caelestia-aw"


def test_load_creates_default_when_missing(redirect_paths):
    from livewall import config

    assert not config.CONFIG_FILE.exists()
    loaded = Config.load()
    assert loaded == Config()
    assert config.CONFIG_FILE.exists()


def test_save_then_load_round_trip(redirect_paths):
    original = Config(
        random_interval="30m",
        random_favorites_only=True,
        random_tags=["cozy", "space"],
        no_smart_colours=True,
        battery_saver_low=10,
        battery_saver_high=40,
        backend="mpvpaper",
    )
    original.save()

    loaded = Config.load()
    assert loaded == original


def test_load_ignores_unknown_fields(redirect_paths):
    from livewall import config

    config.ensure_dirs()
    config.CONFIG_FILE.write_text(json.dumps({"backend": "mpvpaper", "some_future_field": 123}))

    loaded = Config.load()
    assert loaded.backend == "mpvpaper"
    assert not hasattr(loaded, "some_future_field")


def test_load_falls_back_to_defaults_on_corrupt_json(redirect_paths):
    from livewall import config

    config.ensure_dirs()
    config.CONFIG_FILE.write_text("{not valid json")

    loaded = Config.load()
    assert loaded == Config()


def test_ensure_dirs_creates_everything(redirect_paths):
    from livewall import config

    config.ensure_dirs()
    assert config.CONFIG_DIR.is_dir()
    assert config.DATA_DIR.is_dir()
    assert config.CACHE_DIR.is_dir()
    assert config.THUMBNAIL_DIR.is_dir()
