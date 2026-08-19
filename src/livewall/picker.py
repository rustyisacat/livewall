"""Rofi-style quick wallpaper picker, meant to run in a small floating terminal."""

from __future__ import annotations

import logging

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, ListView

from livewall.backends import BackendApplyError, BackendUnavailableError, WallpaperBackend, get_backend
from livewall.config import Config
from livewall.gui import WallpaperItem
from livewall.library import Library, LiveWallError
from livewall.tui_theme import install as install_theme
from livewall.utils import setup_logging

logger = logging.getLogger("livewall.picker")


class PickerApp(App):
    """Search + arrow-key navigate + Enter to apply instantly + Esc to close."""

    TITLE = "LiveWall Picker"

    CSS = """
    Screen { align: center middle; }
    #picker-box { width: 100%; height: 100%; padding: 1; }
    #search { dock: top; border: round $accent; margin-bottom: 1; }
    ListView { height: 1fr; background: $surface; border: round $border-blurred; }
    ListView > ListItem.-highlight { background: $accent; color: $background; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("up", "move(-1)", "Up", show=False),
        Binding("down", "move(1)", "Down", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.library = Library()
        self.config = Config.load()
        # Backend construction must never stop this window from opening —
        # a global hotkey that sometimes silently does nothing is worse than
        # showing search/browse and reporting a clear error only if you try
        # to actually apply something.
        self.backend: WallpaperBackend | None = None
        self.backend_error: str | None = None
        try:
            self.backend = get_backend(self.config.backend)
        except BackendUnavailableError as exc:
            self.backend_error = str(exc)
            logger.error("Picker backend unavailable: %s", exc)

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Input(placeholder="Search wallpapers...", id="search")
            yield ListView(id="wall-list")

    def on_mount(self) -> None:
        install_theme(self)
        self.refresh_list("")
        self.query_one("#search", Input).focus()

    def refresh_list(self, query: str) -> None:
        list_view = self.query_one("#wall-list", ListView)
        list_view.clear()
        for wallpaper in self.library.search(query=query, prefer_animated_format=True):
            list_view.append(WallpaperItem(wallpaper))
        if list_view.children:
            list_view.index = 0

    @on(Input.Changed, "#search")
    def on_search_changed(self, event: Input.Changed) -> None:
        self.refresh_list(event.value)

    @on(Input.Submitted, "#search")
    def on_search_submitted(self) -> None:
        self.action_apply_selected()

    def action_move(self, delta: int) -> None:
        list_view = self.query_one("#wall-list", ListView)
        if not list_view.children:
            return
        count = len(list_view.children)
        current = list_view.index or 0
        list_view.index = (current + delta) % count

    def action_apply_selected(self) -> None:
        list_view = self.query_one("#wall-list", ListView)
        item = list_view.highlighted_child
        if not isinstance(item, WallpaperItem):
            self.exit()
            return

        if self.backend is None:
            self.notify(self.backend_error or "No backend configured", severity="error")
            return

        try:
            self.backend.set_wallpaper(
                self.library.get(item.wallpaper_name).file_path, no_smart=self.config.no_smart_colours
            )
        except (BackendUnavailableError, FileNotFoundError, BackendApplyError, LiveWallError) as exc:
            self.notify(str(exc), severity="error")
            return
        self.exit()

    def action_cancel(self) -> None:
        self.exit()


def run() -> None:
    setup_logging()
    PickerApp().run()


if __name__ == "__main__":
    run()
