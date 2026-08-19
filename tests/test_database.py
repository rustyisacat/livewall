from __future__ import annotations

from livewall.database import Database, Wallpaper


def make_wallpaper(name: str, path: str = "/tmp/x.mp4", **kwargs) -> Wallpaper:
    return Wallpaper(name=name, path=path, kind="animated", hash="deadbeef", **kwargs)


def test_add_get_all(tmp_path):
    db = Database(path=tmp_path / "library.json")
    db.add(make_wallpaper("a"))
    db.add(make_wallpaper("b"))

    assert {w.name for w in db.all()} == {"a", "b"}
    assert db.get("a").name == "a"
    assert db.get("missing") is None


def test_remove(tmp_path):
    db = Database(path=tmp_path / "library.json")
    db.add(make_wallpaper("a"))

    removed = db.remove("a")
    assert removed is not None
    assert removed.name == "a"
    assert db.get("a") is None
    assert db.remove("a") is None  # already gone


def test_rename(tmp_path):
    db = Database(path=tmp_path / "library.json")
    db.add(make_wallpaper("old"))

    renamed = db.rename("old", "new")
    assert renamed is not None
    assert renamed.name == "new"
    assert db.get("old") is None
    assert db.get("new").name == "new"

    assert db.rename("nonexistent", "whatever") is None


def test_find_by_hash_and_path(tmp_path):
    db = Database(path=tmp_path / "library.json")
    db.add(make_wallpaper("a", path="/tmp/a.mp4"))

    found = db.find_by_hash("deadbeef")
    assert found is not None
    assert found.name == "a"
    assert db.find_by_hash("not-a-real-hash") is None

    # find_by_path compares against Path(...).resolve() — an absolute path
    # that's already normalized should match directly.
    found_by_path = db.find_by_path(db._wallpapers["a"].file_path)
    assert found_by_path is not None
    assert found_by_path.name == "a"


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "library.json"
    db = Database(path=path)
    db.add(make_wallpaper("a", tags=["cozy", "space"], favorite=True))
    db.save()

    reloaded = Database(path=path)
    wallpaper = reloaded.get("a")
    assert wallpaper is not None
    assert wallpaper.tags == ["cozy", "space"]
    assert wallpaper.favorite is True


def test_load_missing_file_is_empty(tmp_path):
    db = Database(path=tmp_path / "does_not_exist.json")
    assert db.all() == []


def test_load_corrupt_file_is_empty_not_a_crash(tmp_path):
    path = tmp_path / "library.json"
    path.write_text("{not valid json")
    db = Database(path=path)
    assert db.all() == []


def test_save_is_atomic_via_tmp_file(tmp_path):
    path = tmp_path / "library.json"
    db = Database(path=path)
    db.add(make_wallpaper("a"))
    db.save()

    assert path.exists()
    assert not path.with_suffix(".json.tmp").exists()
