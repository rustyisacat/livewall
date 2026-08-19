"""Covers the auto-update "LiveWall was updated!" banner and changelog
modal on the Textual LibraryScreen (see test_updater.py for the git-pull
logic that produces the notice file this reads)."""

from __future__ import annotations

import asyncio
import json

import pytest

from livewall import updater
from livewall.backends.base import WallpaperBackend
from livewall.config import Config
from livewall.gui import LiveWallApp
from livewall.library import Library


class FakeBackend(WallpaperBackend):
    name = "fake"

    def is_available(self):
        return True

    def is_running(self):
        return False

    def current_path(self):
        return None

    def set_wallpaper(self, path, *, no_smart=False):
        pass

    def stop(self):
        pass


@pytest.fixture
def app(redirect_paths):
    return LiveWallApp(FakeBackend())


async def _run(app, coro):
    async with app.run_test() as pilot:
        await pilot.pause()
        await coro(pilot)


def test_no_notice_means_no_banner(app):
    async def body(pilot):
        assert list(app.screen.query("#update-banner")) == []

    asyncio.run(_run(app, body))


def test_notice_shows_banner_and_is_cleared(app):
    updater.NOTICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    updater.NOTICE_FILE.write_text(json.dumps({
        "old": "a", "new": "b", "changelog": ["- did a thing"],
    }))

    async def body(pilot):
        assert len(app.screen.query("#update-banner")) == 1
        # shown-once: reading the notice on mount clears it
        assert not updater.NOTICE_FILE.exists()

    asyncio.run(_run(app, body))


def test_whats_new_opens_changelog_screen(app):
    updater.NOTICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    updater.NOTICE_FILE.write_text(json.dumps({
        "old": "a", "new": "b", "changelog": ["- did a thing", "- did another"],
    }))

    async def body(pilot):
        await pilot.click("#update-whats-new")
        await pilot.pause()
        from livewall.gui import UpdateChangelogScreen

        assert isinstance(app.screen, UpdateChangelogScreen)
        assert app.screen.changelog == ["- did a thing", "- did another"]

    asyncio.run(_run(app, body))


def test_dismiss_removes_banner(app):
    updater.NOTICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    updater.NOTICE_FILE.write_text(json.dumps({
        "old": "a", "new": "b", "changelog": ["- did a thing"],
    }))

    async def body(pilot):
        await pilot.click("#update-dismiss")
        await pilot.pause()
        assert list(app.screen.query("#update-banner")) == []

    asyncio.run(_run(app, body))
