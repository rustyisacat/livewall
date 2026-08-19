"""cmd_random's tag precedence (--tag > time-of-day rule > static
random_tags) and that it's actually wired to rotation.pick_wallpaper —
the deeper selection-logic behavior itself is covered in test_rotation.py.
"""

from __future__ import annotations

import argparse
from datetime import datetime

import pytest

from livewall import cli, rotation
from livewall.backends.base import WallpaperBackend
from livewall.config import Config
from livewall.database import Database
from livewall.library import Library


class FakeBackend(WallpaperBackend):
    name = "fake"

    def __init__(self):
        self.applied = None

    def is_available(self):
        return True

    def is_running(self):
        return self.applied is not None

    def current_path(self):
        return self.applied

    def set_wallpaper(self, path, *, no_smart=False):
        self.applied = path

    def stop(self):
        self.applied = None


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
    for name, tags in [("cozy1", ["cozy"]), ("night1", ["night"]), ("plain", [])]:
        path = tmp_path / f"{name}.mp4"
        path.write_bytes(name.encode())
        lib.add(path, name=name, tags=tags)
    return lib


def make_args(tag=None, favorites=False, no_smart=False, monitor=None):
    return argparse.Namespace(tag=tag, favorites=favorites, no_smart=no_smart, monitor=monitor)


def test_explicit_tag_wins_over_everything(library):
    config = Config(random_tags=["night"], random_time_rules=[{"start": 0, "end": 24, "tags": ["night"]}])
    backend = FakeBackend()
    result = cli.cmd_random(make_args(tag="cozy"), library, config, backend)
    assert result == 0
    assert backend.applied.name == "cozy1.mp4"


def test_time_rule_wins_over_static_random_tags(library, monkeypatch):
    monkeypatch.setattr(rotation, "tags_for_time_rules", lambda rules, now=None: ["night"])
    config = Config(random_tags=["cozy"], random_time_rules=[{"start": 0, "end": 24, "tags": ["night"]}])
    backend = FakeBackend()
    result = cli.cmd_random(make_args(), library, config, backend)
    assert result == 0
    assert backend.applied.name == "night1.mp4"


def test_falls_back_to_static_random_tags_when_no_time_rule_matches(library):
    config = Config(random_tags=["cozy"], random_time_rules=[])
    backend = FakeBackend()
    result = cli.cmd_random(make_args(), library, config, backend)
    assert result == 0
    assert backend.applied.name == "cozy1.mp4"


def test_no_match_prints_error_and_returns_1(library, capsys):
    config = Config()
    backend = FakeBackend()
    result = cli.cmd_random(make_args(tag="nonexistent-tag"), library, config, backend)
    assert result == 1
    captured = capsys.readouterr()
    assert "No wallpapers match" in captured.err
