from __future__ import annotations

from datetime import datetime

import pytest

from livewall import rotation
from livewall.database import Database
from livewall.library import Library


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setattr(rotation, "HISTORY_FILE", tmp_path / "random_history.json")


@pytest.fixture(autouse=True)
def no_real_ffmpeg(monkeypatch):
    from livewall import thumbnail

    monkeypatch.setattr(
        thumbnail, "probe",
        lambda path: thumbnail.Metadata(None, None, None, True, None),
    )
    monkeypatch.setattr(thumbnail, "generate", lambda path, cache_key, metadata=None: None)


@pytest.fixture
def library(tmp_path):
    return Library(db=Database(path=tmp_path / "library.json"))


def add(library, tmp_path, name, content=b"x", tags=None):
    path = tmp_path / f"{name}.mp4"
    path.write_bytes(content)
    return library.add(path, tags=tags, name=name)


# ---- pick_wallpaper: repeat avoidance -------------------------------------


def test_pick_wallpaper_none_when_nothing_matches(isolated_history, library):
    assert rotation.pick_wallpaper(library, tags=None, favorites_only=False, current=None) is None


def test_pick_wallpaper_avoids_current(isolated_history, library, tmp_path):
    a = add(library, tmp_path, "a", content=b"a")
    add(library, tmp_path, "b", content=b"b")

    # Run many times — with only 2 candidates and "a" as current, it should
    # never pick "a" again as long as "b" is available.
    for _ in range(20):
        picked = rotation.pick_wallpaper(library, tags=None, favorites_only=False, current=a.file_path)
        assert picked.name == "b"


def test_pick_wallpaper_falls_back_to_current_if_only_option(isolated_history, library, tmp_path):
    a = add(library, tmp_path, "a", content=b"a")
    picked = rotation.pick_wallpaper(library, tags=None, favorites_only=False, current=a.file_path)
    assert picked.name == "a"  # nothing else exists, must still return something


def test_pick_wallpaper_avoids_recent_history_beyond_just_current(isolated_history, library, tmp_path):
    # 3 wallpapers, history already has "b" in it (from some earlier pick).
    # With "a" as current, a naive "exclude only current" selector could
    # still pick "b" right back — history exclusion prevents that for this
    # pick (this is a one-shot guarantee at call time, not a promise across
    # however many subsequent calls — each pick also re-records itself into
    # history, so a small enough pool will still eventually cycle back).
    add(library, tmp_path, "a", content=b"a")
    add(library, tmp_path, "b", content=b"b")
    add(library, tmp_path, "c", content=b"c")
    rotation.HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    import json

    rotation.HISTORY_FILE.write_text(json.dumps(["b"]))

    a_path = library.get("a").file_path
    picked = rotation.pick_wallpaper(library, tags=None, favorites_only=False, current=a_path)
    assert picked.name == "c"


def test_pick_wallpaper_records_history(isolated_history, library, tmp_path):
    add(library, tmp_path, "a", content=b"a")
    add(library, tmp_path, "b", content=b"b")

    rotation.pick_wallpaper(library, tags=None, favorites_only=False, current=None)
    assert len(rotation._read_history()) == 1


def test_pick_wallpaper_history_is_capped(isolated_history, library, tmp_path):
    for i in range(rotation.HISTORY_SIZE + 3):
        add(library, tmp_path, f"w{i}", content=str(i).encode())

    for _ in range(rotation.HISTORY_SIZE + 3):
        rotation.pick_wallpaper(library, tags=None, favorites_only=False, current=None)

    assert len(rotation._read_history()) == rotation.HISTORY_SIZE


def test_pick_wallpaper_falls_back_to_full_pool_when_history_excludes_everything(
    isolated_history, library, tmp_path
):
    # Only one wallpaper exists, and it happens to be in history (e.g. it
    # was just applied via a different path) — must still return it rather
    # than nothing.
    add(library, tmp_path, "only", content=b"only")
    import json

    rotation.HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    rotation.HISTORY_FILE.write_text(json.dumps(["only"]))

    picked = rotation.pick_wallpaper(library, tags=None, favorites_only=False, current=None)
    assert picked.name == "only"


def test_pick_wallpaper_respects_tags_and_favorites(isolated_history, library, tmp_path):
    add(library, tmp_path, "cozy_fav", content=b"1", tags=["cozy"])
    library.toggle_favorite("cozy_fav")
    add(library, tmp_path, "cozy_not_fav", content=b"2", tags=["cozy"])
    add(library, tmp_path, "space", content=b"3", tags=["space"])

    picked = rotation.pick_wallpaper(library, tags=["cozy"], favorites_only=True, current=None)
    assert picked.name == "cozy_fav"


# ---- tags_for_time_rules ----------------------------------------------


def test_tags_for_time_rules_no_rules_returns_none():
    assert rotation.tags_for_time_rules([]) is None


def test_tags_for_time_rules_matches_simple_range():
    rules = [{"start": 6, "end": 18, "tags": ["cozy"]}]
    assert rotation.tags_for_time_rules(rules, now=datetime(2026, 1, 1, 10, 0)) == ["cozy"]
    assert rotation.tags_for_time_rules(rules, now=datetime(2026, 1, 1, 20, 0)) is None


def test_tags_for_time_rules_end_is_exclusive():
    rules = [{"start": 6, "end": 12, "tags": ["cozy"]}]
    assert rotation.tags_for_time_rules(rules, now=datetime(2026, 1, 1, 11, 59)) == ["cozy"]
    assert rotation.tags_for_time_rules(rules, now=datetime(2026, 1, 1, 12, 0)) is None


def test_tags_for_time_rules_wraps_past_midnight():
    rules = [{"start": 22, "end": 6, "tags": ["night"]}]
    assert rotation.tags_for_time_rules(rules, now=datetime(2026, 1, 1, 23, 0)) == ["night"]
    assert rotation.tags_for_time_rules(rules, now=datetime(2026, 1, 1, 3, 0)) == ["night"]
    assert rotation.tags_for_time_rules(rules, now=datetime(2026, 1, 1, 12, 0)) is None


def test_tags_for_time_rules_first_match_wins():
    rules = [
        {"start": 0, "end": 24, "tags": ["always"]},
        {"start": 6, "end": 18, "tags": ["day"]},
    ]
    assert rotation.tags_for_time_rules(rules, now=datetime(2026, 1, 1, 10, 0)) == ["always"]
