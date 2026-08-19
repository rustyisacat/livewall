"""Shared fixtures.

The core modules bind their file-path constants (STATE_FILE, CONFIG_FILE,
...) at import time from livewall.config's module-level constants — a
plain monkeypatch of livewall.config doesn't reach those already-bound
names in other modules. redirect_paths patches every module that owns one
of these constants directly, so tests never touch the real
~/.config/livewall, ~/.local/share/livewall, or ~/.cache/livewall.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def redirect_paths(tmp_path, monkeypatch):
    from livewall import config

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    thumbnail_dir = cache_dir / "thumbnails"

    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(config, "THUMBNAIL_DIR", thumbnail_dir)
    monkeypatch.setattr(config, "CONFIG_FILE", config_dir / "config.json")
    monkeypatch.setattr(config, "LIBRARY_FILE", data_dir / "library.json")
    monkeypatch.setattr(config, "LOG_FILE", cache_dir / "livewall.log")

    # backends/mpvpaper.py binds its own STATE_FILE/CACHE_DIR from
    # livewall.config at import time — redirect those too so backend tests
    # never touch a real cache dir either. CACHE_DIR is what
    # _ipc_socket()/_stderr_log() derive each target's own paths from.
    from livewall.backends import mpvpaper

    monkeypatch.setattr(mpvpaper, "STATE_FILE", cache_dir / "mpvpaper_state.json")
    monkeypatch.setattr(mpvpaper, "CACHE_DIR", cache_dir)

    from livewall import power_saver

    monkeypatch.setattr(power_saver, "STATE_FILE", cache_dir / "power_saver_state.json")

    from livewall import updater

    monkeypatch.setattr(updater, "NOTICE_FILE", cache_dir / "update_notice.json")

    return tmp_path
