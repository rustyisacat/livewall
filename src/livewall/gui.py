"""Textual GUI: the main LiveWall library browser."""

from __future__ import annotations

import logging

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
    Switch,
)

from livewall import engine
from livewall.config import Config, RANDOM_INTERVAL_SECONDS
from livewall.database import Wallpaper
from livewall.engine import ApplyError, CaelestiaNotAvailableError
from livewall.library import Library, LiveWallError, WallpaperInfo
from livewall.utils import setup_logging

logger = logging.getLogger("livewall.gui")

CATEGORY_TAGS = ["Cozy", "Synthwave", "Anime", "Space", "Nature", "Pixel Art", "Cyberpunk"]

try:
    from textual_image.widget import Image as ImageWidget
except ImportError:  # pragma: no cover - optional at runtime
    ImageWidget = None


class ConfirmScreen(ModalScreen[bool]):
    """Yes/No confirmation dialog."""

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    #confirm-box { width: 50; height: auto; border: round $accent; padding: 1 2; }
    #confirm-buttons { height: auto; align: center middle; margin-top: 1; }
    #confirm-buttons Button { margin: 0 1; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self.message)
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", id="yes", variant="error")
                yield Button("No", id="no", variant="primary")

    @on(Button.Pressed, "#yes")
    def confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def cancel_btn(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class TextPromptScreen(ModalScreen[str | None]):
    """A single-line text input prompt."""

    DEFAULT_CSS = """
    TextPromptScreen { align: center middle; }
    #prompt-box { width: 60; height: auto; border: round $accent; padding: 1 2; }
    #prompt-buttons { height: auto; align: center middle; margin-top: 1; }
    #prompt-buttons Button { margin: 0 1; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, initial: str = "") -> None:
        super().__init__()
        self.prompt = prompt
        self.initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-box"):
            yield Static(self.prompt)
            yield Input(value=self.initial, id="value")
            with Horizontal(id="prompt-buttons"):
                yield Button("OK", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#value", Input).focus()

    @on(Input.Submitted, "#value")
    def submit(self) -> None:
        self.dismiss(self.query_one("#value", Input).value)

    @on(Button.Pressed, "#ok")
    def ok(self) -> None:
        self.dismiss(self.query_one("#value", Input).value)

    @on(Button.Pressed, "#cancel")
    def cancel_btn(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SettingsScreen(Screen):
    """Editable view over the persistent Config."""

    BINDINGS = [("escape", "close", "Back")]

    DEFAULT_CSS = """
    SettingsScreen { align: center top; }
    #settings-box { width: 70; height: auto; margin-top: 2; border: round $accent; padding: 1 2; }
    .settings-row { height: 3; align: left middle; }
    .settings-row Label { width: 24; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.config = Config.load()

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="settings-box"):
            yield Static(
                "[dim]Rendering, theming, HW decode, and pause-on-battery/fullscreen "
                "are configured in Caelestia's own Nexus settings — LiveWall only "
                "controls what it adds on top.[/dim]"
            )
            with Horizontal(classes="settings-row"):
                yield Label("Random interval")
                yield Select(
                    [(k, k) for k in RANDOM_INTERVAL_SECONDS],
                    value=self.config.random_interval,
                    id="random_interval",
                )
            with Horizontal(classes="settings-row"):
                yield Label("Random: favorites only")
                yield Switch(value=self.config.random_favorites_only, id="random_favorites_only")
            with Horizontal(classes="settings-row"):
                yield Label("Random: tags")
                yield Input(value=", ".join(self.config.random_tags), id="random_tags")
            with Horizontal(classes="settings-row"):
                yield Label("Skip Material You recolour")
                yield Switch(value=self.config.no_smart_colours, id="no_smart_colours")
            with Horizontal(classes="settings-row"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")
        yield Footer()

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        self.config.random_interval = self.query_one("#random_interval", Select).value
        self.config.random_favorites_only = self.query_one("#random_favorites_only", Switch).value
        raw_tags = self.query_one("#random_tags", Input).value
        self.config.random_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        self.config.no_smart_colours = self.query_one("#no_smart_colours", Switch).value
        self.config.save()
        self.notify("Settings saved")
        self.app.pop_screen()

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.app.pop_screen()

    def action_close(self) -> None:
        self.app.pop_screen()


class WallpaperItem(ListItem):
    """One row in the library list."""

    def __init__(self, wallpaper: Wallpaper) -> None:
        super().__init__(Label(self._label(wallpaper)))
        self.wallpaper_name = wallpaper.name

    @staticmethod
    def _label(w: Wallpaper) -> str:
        star = "★" if w.favorite else " "
        kind = "\U0001f3ac" if w.kind == "animated" else "\U0001f5bc"
        tags = f"  [{', '.join(w.tags)}]" if w.tags else ""
        return f"{star} {kind} {w.name}{tags}"

    def refresh_label(self, wallpaper: Wallpaper) -> None:
        self.query_one(Label).update(self._label(wallpaper))


class DetailPane(Vertical):
    """Right-hand pane: thumbnail + metadata for the selected wallpaper."""

    DEFAULT_CSS = """
    DetailPane { width: 46; padding: 1; border-left: solid $panel; }
    DetailPane #thumb-holder { height: 16; content-align: center middle; }
    DetailPane #meta { height: auto; margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Vertical(id="thumb-holder")
        yield Static(id="meta")

    def show(self, info: WallpaperInfo | None) -> None:
        holder = self.query_one("#thumb-holder", Vertical)
        holder.remove_children()
        meta = self.query_one("#meta", Static)

        if info is None:
            meta.update("(no wallpapers in library)")
            return

        w = info.wallpaper
        if ImageWidget is not None and info.thumbnail_path is not None:
            holder.mount(ImageWidget(str(info.thumbnail_path)))
        else:
            holder.mount(Static("[dim](no preview)[/dim]"))

        lines = [
            f"[bold]{w.name}[/bold]",
            f"Type: {w.kind}  Favorite: {'yes' if w.favorite else 'no'}",
            f"Resolution: {info.metadata.resolution}",
            f"Aspect ratio: {info.metadata.aspect_ratio or 'unknown'}",
            f"Duration: {info.duration_human}",
            f"Animated: {'yes' if info.metadata.animated else 'no'}",
            f"Size: {info.size_human}",
            f"Tags: {', '.join(w.tags) or '(none)'}",
        ]
        meta.update("\n".join(lines))


class LibraryScreen(Screen):
    """Main screen: search/filter list on the left, details on the right."""

    BINDINGS = [
        ("/", "focus_search", "Search"),
        ("a", "apply_selected", "Apply"),
        ("f", "toggle_favorite", "Favorite"),
        ("p", "preview_selected", "Preview"),
        ("d", "delete_selected", "Delete"),
        ("r", "rename_selected", "Rename"),
        ("t", "edit_tags", "Tags"),
        ("i", "import_folder", "Import"),
        ("s", "open_settings", "Settings"),
    ]

    DEFAULT_CSS = """
    LibraryScreen #body { height: 1fr; }
    LibraryScreen #left { width: 1fr; }
    LibraryScreen #search { dock: top; }
    """

    def __init__(self, library: Library, config: Config) -> None:
        super().__init__()
        self.library = library
        self.config = config

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Input(placeholder="Search name or tag...", id="search")
                yield ListView(id="wall-list")
            yield DetailPane(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_list()

    def refresh_list(self, query: str = "") -> None:
        list_view = self.query_one("#wall-list", ListView)
        list_view.clear()
        for wallpaper in self.library.search(query=query):
            list_view.append(WallpaperItem(wallpaper))
        if list_view.children:
            list_view.index = 0
        else:
            self.query_one(DetailPane).show(None)

    def selected_name(self) -> str | None:
        list_view = self.query_one("#wall-list", ListView)
        item = list_view.highlighted_child
        return item.wallpaper_name if isinstance(item, WallpaperItem) else None

    @on(Input.Changed, "#search")
    def on_search_changed(self, event: Input.Changed) -> None:
        self.refresh_list(event.value)

    @on(ListView.Highlighted, "#wall-list")
    def on_highlighted(self) -> None:
        name = self.selected_name()
        info = self.library.info(name) if name else None
        self.query_one(DetailPane).show(info)

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_apply_selected(self) -> None:
        name = self.selected_name()
        if not name:
            return
        try:
            engine.apply(self.library.get(name), no_smart=self.config.no_smart_colours)
        except CaelestiaNotAvailableError:
            self.notify("caelestia is not installed", severity="error")
            return
        except (FileNotFoundError, ApplyError, LiveWallError) as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(f"Applied '{name}'")

    def action_toggle_favorite(self) -> None:
        name = self.selected_name()
        if not name:
            return
        wallpaper = self.library.toggle_favorite(name)
        self._refresh_current_item(wallpaper)
        self.query_one(DetailPane).show(self.library.info(name))

    def action_preview_selected(self) -> None:
        name = self.selected_name()
        if not name:
            return
        wallpaper = self.library.get(name)
        try:
            engine.preview(wallpaper.file_path, blocking=False)
        except CaelestiaNotAvailableError:
            self.notify("mpv is not installed", severity="error")
            return
        self.notify(f"Previewing '{name}' in mpv")

    def action_delete_selected(self) -> None:
        name = self.selected_name()
        if not name:
            return

        def handle(confirmed: bool | None) -> None:
            if confirmed:
                self.library.remove(name)
                self.refresh_list(self.query_one("#search", Input).value)
                self.notify(f"Removed '{name}'")

        self.app.push_screen(ConfirmScreen(f"Delete '{name}' from the library?"), handle)

    def action_rename_selected(self) -> None:
        name = self.selected_name()
        if not name:
            return

        def handle(new_name: str | None) -> None:
            if new_name and new_name != name:
                try:
                    self.library.rename(name, new_name)
                except LiveWallError as exc:
                    self.notify(str(exc), severity="error")
                    return
                self.refresh_list(self.query_one("#search", Input).value)

        self.app.push_screen(TextPromptScreen("New name:", name), handle)

    def action_edit_tags(self) -> None:
        name = self.selected_name()
        if not name:
            return
        wallpaper = self.library.get(name)

        def handle(raw: str | None) -> None:
            if raw is None:
                return
            tags = [t.strip() for t in raw.split(",") if t.strip()]
            updated = self.library.set_tags(name, tags)
            self._refresh_current_item(updated)
            self.query_one(DetailPane).show(self.library.info(name))

        hint = f"Tags for '{name}' (comma-separated, e.g. {', '.join(CATEGORY_TAGS[:3])}):"
        self.app.push_screen(TextPromptScreen(hint, ", ".join(wallpaper.tags)), handle)

    def action_import_folder(self) -> None:
        def handle(folder: str | None) -> None:
            if not folder:
                return
            from pathlib import Path

            result = self.library.import_folder(Path(folder))
            self.refresh_list(self.query_one("#search", Input).value)
            self.notify(
                f"Imported {len(result.added)}, "
                f"skipped {len(result.duplicates)} duplicates, "
                f"{len(result.unsupported)} unsupported"
            )

        self.app.push_screen(TextPromptScreen("Folder to import:", "~/Pictures/Wallpapers"), handle)

    def action_open_settings(self) -> None:
        self.app.push_screen(SettingsScreen())

    def _refresh_current_item(self, wallpaper: Wallpaper) -> None:
        list_view = self.query_one("#wall-list", ListView)
        item = list_view.highlighted_child
        if isinstance(item, WallpaperItem):
            item.refresh_label(wallpaper)


class LiveWallApp(App):
    """The LiveWall Textual application."""

    TITLE = "LiveWall"

    def __init__(self) -> None:
        super().__init__()
        self.library = Library()
        self.config = Config.load()

    def on_mount(self) -> None:
        self.push_screen(LibraryScreen(self.library, self.config))


def run() -> None:
    setup_logging()
    LiveWallApp().run()


if __name__ == "__main__":
    run()
