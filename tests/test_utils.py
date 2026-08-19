from __future__ import annotations

from pathlib import Path

from livewall.utils import (
    human_duration,
    human_size,
    is_animated,
    is_supported,
    sha256_file,
    unique_name,
)


def test_is_supported_and_is_animated():
    assert is_supported(Path("a.mp4"))
    assert is_supported(Path("a.PNG"))  # case-insensitive
    assert not is_supported(Path("a.txt"))
    assert is_animated(Path("a.gif"))
    assert not is_animated(Path("a.png"))


def test_sha256_file(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"hello world")
    import hashlib

    assert sha256_file(path) == hashlib.sha256(b"hello world").hexdigest()


def test_human_size():
    assert human_size(500) == "500 B"
    assert human_size(2048) == "2.0 KB"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"


def test_human_duration():
    assert human_duration(None) == "--:--"
    assert human_duration(0) == "--:--"
    assert human_duration(65) == "1:05"
    assert human_duration(3725) == "1:02:05"


def test_unique_name():
    existing = {"sunset", "sunset (2)"}
    assert unique_name("cozy", existing) == "cozy"
    assert unique_name("sunset", existing) == "sunset (3)"
