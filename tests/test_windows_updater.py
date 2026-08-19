"""What's testable on Linux for the Windows self-update flow: the
version-comparison/parsing logic in check_for_update() (mocked HTTP) and
the download/extract/size-check logic in download_and_stage() (a real
local zip, HTTP mocked). The batch-helper handoff in apply_and_relaunch()
needs a real frozen Windows build to exercise for real — see the module
docstring for why that's an accepted, unverified gap, same as the rest
of the Windows backend.

check_for_update()/download_and_stage() were also both run live against
the real GitHub API and the real v1.2.0 release asset during development
(not part of this suite — a one-off manual check) and worked end to end."""

from __future__ import annotations

import io
import json
import urllib.error
import zipfile
from pathlib import Path

import pytest

from livewall.windows import updater


class FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _release_json(tag: str, *, asset_name: str = "LiveWall-windows.zip", size: int = 123, body: str = "") -> bytes:
    return json.dumps({
        "tag_name": tag,
        "body": body,
        "assets": [{"name": asset_name, "browser_download_url": f"https://example.com/{asset_name}", "size": size}],
    }).encode("utf-8")


def test_check_for_update_none_when_up_to_date(monkeypatch):
    monkeypatch.setattr(updater, "_current_version", lambda: "1.2.0")
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: FakeResponse(_release_json("v1.2.0"))
    )
    assert updater.check_for_update() is None


def test_check_for_update_returns_info_when_newer(monkeypatch):
    monkeypatch.setattr(updater, "_current_version", lambda: "1.2.0")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: FakeResponse(_release_json("v1.3.0", size=999, body="- new thing\n- another")),
    )
    info = updater.check_for_update()
    assert info is not None
    assert info.tag == "v1.3.0"
    assert info.size == 999
    assert info.changelog == "- new thing\n- another"
    assert info.download_url.endswith("LiveWall-windows.zip")


def test_check_for_update_none_when_asset_missing(monkeypatch):
    monkeypatch.setattr(updater, "_current_version", lambda: "1.2.0")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: FakeResponse(_release_json("v1.3.0", asset_name="something-else.zip")),
    )
    assert updater.check_for_update() is None


def test_check_for_update_none_on_network_error(monkeypatch):
    def raise_it(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(updater, "_current_version", lambda: "1.2.0")
    monkeypatch.setattr("urllib.request.urlopen", raise_it)
    assert updater.check_for_update() is None


def test_check_for_update_none_on_bad_json(monkeypatch):
    monkeypatch.setattr(updater, "_current_version", lambda: "1.2.0")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResponse(b"not json"))
    assert updater.check_for_update() is None


def _make_zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_download_and_stage_extracts_files(tmp_path, monkeypatch):
    zip_bytes = _make_zip_bytes({"LiveWall.exe": "fake exe", "_internal/thing.dll": "fake dll"})
    monkeypatch.setattr(updater, "_update_root", lambda: tmp_path / "update")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResponse(zip_bytes))

    info = updater.UpdateInfo(tag="v1.3.0", download_url="https://example.com/x.zip", size=len(zip_bytes), changelog="")
    staging = updater.download_and_stage(info)

    assert staging == tmp_path / "update" / "staging"
    assert (staging / "LiveWall.exe").read_text() == "fake exe"
    assert (staging / "_internal" / "thing.dll").read_text() == "fake dll"


def test_download_and_stage_rejects_truncated_download(tmp_path, monkeypatch):
    zip_bytes = _make_zip_bytes({"LiveWall.exe": "fake exe"})
    monkeypatch.setattr(updater, "_update_root", lambda: tmp_path / "update")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResponse(zip_bytes))

    # Reported size doesn't match what actually came down the wire.
    info = updater.UpdateInfo(tag="v1.3.0", download_url="https://example.com/x.zip", size=len(zip_bytes) + 500, changelog="")
    staging = updater.download_and_stage(info)

    assert staging is None


def test_download_and_stage_none_on_bad_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_update_root", lambda: tmp_path / "update")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResponse(b"not a zip"))

    info = updater.UpdateInfo(tag="v1.3.0", download_url="https://example.com/x.zip", size=9, changelog="")
    assert updater.download_and_stage(info) is None


def test_apply_and_relaunch_false_when_not_a_frozen_build(tmp_path, monkeypatch):
    # sys.frozen is unset in a normal dev/test run — _install_dir() returns
    # None, and apply_and_relaunch must never crash trying to self-update
    # something that isn't a packaged install.
    info = updater.UpdateInfo(tag="v1.3.0", download_url="https://example.com/x.zip", size=1, changelog="")
    assert updater.apply_and_relaunch(tmp_path, info) is False
