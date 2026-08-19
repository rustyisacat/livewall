from __future__ import annotations

import pytest

from livewall import thumbnail
from livewall.database import Database
from livewall.library import (
    DuplicateWallpaperError,
    Library,
    LiveWallError,
    UnsupportedFormatError,
    WallpaperNotFoundError,
    prefer_non_gif,
    sort_by_animated_format,
)


@pytest.fixture(autouse=True)
def no_real_ffmpeg(monkeypatch):
    """library.add() calls thumbnail.probe()/generate() — stub both out so
    tests don't depend on ffmpeg/ffprobe actually being installed, or on
    the fixture files being real playable media."""
    monkeypatch.setattr(
        thumbnail, "probe",
        lambda path: thumbnail.Metadata(None, None, None, path.suffix.lower() != ".png", None),
    )
    monkeypatch.setattr(thumbnail, "generate", lambda path, cache_key, metadata=None: None)


@pytest.fixture
def library(tmp_path):
    return Library(db=Database(path=tmp_path / "library.json"))


def make_file(tmp_path, name: str, content: bytes = b"fake media") -> "Path":  # noqa: F821
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_add_file(tmp_path, library):
    path = make_file(tmp_path, "sunset.mp4")
    wallpaper = library.add(path)

    assert wallpaper.name == "sunset"
    assert wallpaper.kind == "animated"
    assert library.get("sunset").name == "sunset"


def test_add_rejects_unsupported_format(tmp_path, library):
    path = make_file(tmp_path, "notes.txt")
    with pytest.raises(UnsupportedFormatError):
        library.add(path)


def test_add_rejects_missing_file(tmp_path, library):
    with pytest.raises(LiveWallError):
        library.add(tmp_path / "does_not_exist.mp4")


def test_add_duplicate_by_path(tmp_path, library):
    path = make_file(tmp_path, "sunset.mp4")
    library.add(path)
    with pytest.raises(DuplicateWallpaperError):
        library.add(path)


def test_add_duplicate_by_content_hash(tmp_path, library):
    # Same bytes, different filename/path — should still be caught as a
    # duplicate via content hash, not just path comparison.
    make_file(tmp_path, "a.mp4", content=b"identical bytes")
    library.add(tmp_path / "a.mp4")
    make_file(tmp_path, "b.mp4", content=b"identical bytes")
    with pytest.raises(DuplicateWallpaperError):
        library.add(tmp_path / "b.mp4")


def test_add_dedupes_display_name(tmp_path, library):
    make_file(tmp_path, "sunset.mp4", content=b"one")
    library.add(tmp_path / "sunset.mp4", name="sunset")

    make_file(tmp_path, "sunset2.mp4", content=b"two")
    second = library.add(tmp_path / "sunset2.mp4", name="sunset")
    assert second.name == "sunset (2)"


def test_rename(tmp_path, library):
    library.add(make_file(tmp_path, "a.mp4"))
    library.rename("a", "b")
    assert library.get("b").name == "b"
    with pytest.raises(WallpaperNotFoundError):
        library.get("a")


def test_rename_rejects_taken_name(tmp_path, library):
    library.add(make_file(tmp_path, "a.mp4", content=b"one"))
    library.add(make_file(tmp_path, "b.mp4", content=b"two"))
    with pytest.raises(LiveWallError):
        library.rename("a", "b")


def test_rename_missing_raises(library):
    with pytest.raises(WallpaperNotFoundError):
        library.rename("ghost", "whatever")


def test_toggle_favorite(tmp_path, library):
    library.add(make_file(tmp_path, "a.mp4"))
    assert library.get("a").favorite is False
    library.toggle_favorite("a")
    assert library.get("a").favorite is True
    library.toggle_favorite("a")
    assert library.get("a").favorite is False


def test_set_tags_dedupes_and_sorts(tmp_path, library):
    library.add(make_file(tmp_path, "a.mp4"))
    library.set_tags("a", ["space", "cozy", "space"])
    assert library.get("a").tags == ["cozy", "space"]


def test_remove(tmp_path, library):
    library.add(make_file(tmp_path, "a.mp4"))
    library.remove("a")
    with pytest.raises(WallpaperNotFoundError):
        library.get("a")


def test_remove_missing_raises(library):
    with pytest.raises(WallpaperNotFoundError):
        library.remove("ghost")


def test_search_by_query_matches_name_or_tag(tmp_path, library):
    library.add(make_file(tmp_path, "cozy-room.mp4", content=b"a"))
    library.add(make_file(tmp_path, "space-station.mp4", content=b"b"))
    library.set_tags("space-station", ["sci-fi"])

    assert [w.name for w in library.search(query="cozy")] == ["cozy-room"]
    assert [w.name for w in library.search(query="sci-fi")] == ["space-station"]
    assert library.search(query="nonexistent") == []


def test_search_by_tags_requires_all(tmp_path, library):
    library.add(make_file(tmp_path, "a.mp4", content=b"a"))
    library.add(make_file(tmp_path, "b.mp4", content=b"b"))
    library.set_tags("a", ["cozy", "night"])
    library.set_tags("b", ["cozy"])

    assert [w.name for w in library.search(tags=["cozy", "night"])] == ["a"]
    assert {w.name for w in library.search(tags=["cozy"])} == {"a", "b"}


def test_search_favorites_only(tmp_path, library):
    library.add(make_file(tmp_path, "a.mp4", content=b"a"))
    library.add(make_file(tmp_path, "b.mp4", content=b"b"))
    library.toggle_favorite("a")

    assert [w.name for w in library.search(favorites_only=True)] == ["a"]


def test_import_folder_categorizes_everything(tmp_path, library):
    src = tmp_path / "import_me"
    src.mkdir()
    (src / "a.mp4").write_bytes(b"one")
    (src / "b.txt").write_bytes(b"not a wallpaper")
    (src / "a_dup.mp4").write_bytes(b"one")  # same content as a.mp4

    result = library.import_folder(src)
    assert [w.name for w in result.added] == ["a"]
    assert result.unsupported == [src / "b.txt"]
    assert result.duplicates == [src / "a_dup.mp4"]


def test_sort_by_animated_format_prefers_non_gif():
    from livewall.database import Wallpaper

    gif = Wallpaper(name="clip", path="/tmp/clip.gif", kind="animated", hash="1")
    mp4 = Wallpaper(name="clip", path="/tmp/clip.mp4", kind="animated", hash="2")
    result = sort_by_animated_format([gif, mp4])
    assert result[0] is mp4


def test_prefer_non_gif_drops_gif_when_alternative_exists():
    from livewall.database import Wallpaper

    gif = Wallpaper(name="clip", path="/tmp/clip.gif", kind="animated", hash="1")
    mp4 = Wallpaper(name="clip", path="/tmp/clip.mp4", kind="animated", hash="2")
    assert prefer_non_gif([gif, mp4]) == [mp4]
    assert prefer_non_gif([gif]) == [gif]  # nothing else to prefer
