"""Textual GUI: the main LiveWall library browser."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
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

from livewall import updater
from livewall.backends import (
    BackendApplyError,
    BackendUnavailableError,
    WallpaperBackend,
    available_backend_names,
    get_backend,
)
from livewall.config import Config, RANDOM_INTERVAL_SECONDS
from livewall.database import Wallpaper
from livewall.library import DuplicateWallpaperError, Library, LiveWallError, WallpaperInfo
from livewall.preview import MpvNotAvailableError, preview as mpv_preview
from livewall.tui_theme import ACCENT, SUCCESS, install as install_theme
from livewall.utils import setup_logging

logger = logging.getLogger("livewall.gui")

CATEGORY_TAGS = ["Cozy", "Synthwave", "Anime", "Space", "Nature", "Pixel Art", "Cyberpunk"]


def _category_button_id(category: str) -> str:
    return "cat-" + category.lower().replace(" ", "-")

try:
    from textual_image.widget import Image as ImageWidget
except ImportError:  # pragma: no cover - optional at runtime
    ImageWidget = None


class ConfirmScreen(ModalScreen[bool]):
    """Yes/No confirmation dialog."""

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; background: $background 60%; }
    #confirm-box { width: 50; height: auto; background: $surface; border: round $accent; padding: 1 2; }
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
    TextPromptScreen { align: center middle; background: $background 60%; }
    #prompt-box { width: 60; height: auto; background: $surface; border: round $accent; padding: 1 2; }
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


class MonitorPickerScreen(ModalScreen[str | None]):
    """Which target to apply a wallpaper to — only ever pushed when the
    backend supports per-monitor wallpapers and more than one monitor was
    detected; a single-monitor system (or a backend without the
    capability) never sees this, applying everywhere exactly as before.

    Dismisses with "ALL" for the mirrored case, a real monitor name for a
    specific one, or None if cancelled — None is deliberately distinct
    from "ALL" (a real choice to apply everywhere) so the caller can tell
    "cancelled" from "apply to everything" apart.
    """

    DEFAULT_CSS = """
    MonitorPickerScreen { align: center middle; background: $background 60%; }
    #monitor-box { width: 40; height: auto; background: $surface; border: round $accent; padding: 1 2; }
    #monitor-box Button { width: 100%; margin-top: 1; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, monitors: list[str]) -> None:
        super().__init__()
        self.monitors = monitors

    def compose(self) -> ComposeResult:
        with Vertical(id="monitor-box"):
            yield Static("Apply to which monitor?")
            yield Button("All monitors", id="target-ALL", variant="primary")
            for monitor in self.monitors:
                yield Button(monitor, id=f"target-{monitor}")

    @on(Button.Pressed)
    def on_button(self, event: Button.Pressed) -> None:
        target_id = event.button.id or ""
        if target_id.startswith("target-"):
            self.dismiss(target_id.removeprefix("target-"))

    def action_cancel(self) -> None:
        self.dismiss(None)


class UpdateChangelogScreen(ModalScreen[None]):
    """What changed in the last auto-update — pushed when the "What's
    new?" banner button on LibraryScreen is clicked."""

    DEFAULT_CSS = """
    UpdateChangelogScreen { align: center middle; background: $background 60%; }
    #changelog-box { width: 70; height: auto; max-height: 80%; background: $surface; border: round $accent; padding: 1 2; }
    #changelog-list { height: auto; max-height: 20; margin-top: 1; }
    #changelog-buttons { height: auto; align: center middle; margin-top: 1; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, changelog: list[str]) -> None:
        super().__init__()
        self.changelog = changelog

    def compose(self) -> ComposeResult:
        with Vertical(id="changelog-box"):
            yield Static("[bold]LiveWall was updated — what's new[/bold]")
            with VerticalScroll(id="changelog-list"):
                yield Static("\n".join(self.changelog) or "(no details available)")
            with Horizontal(id="changelog-buttons"):
                yield Button("Close", id="close", variant="primary")

    @on(Button.Pressed, "#close")
    def close_btn(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class SettingsScreen(Screen):
    """Editable view over the persistent Config."""

    BINDINGS = [("escape", "close", "Back")]

    DEFAULT_CSS = """
    SettingsScreen { align: center top; }
    #settings-box {
        width: 70; height: auto; margin-top: 2;
        background: $surface; border: round $accent; padding: 1 2;
    }
    .settings-row { height: 3; }
    .settings-row Label { width: 24; height: 3; content-align: left middle; color: $text-muted; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.config = Config.load()

    @staticmethod
    def _timer_status() -> str:
        from livewall import systemd

        if systemd.is_installed():
            return "[dim]Timer status: installed and enabled[/dim]"
        return "[dim]Timer status: not installed[/dim]"

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="settings-box"):
            yield Static(
                "[dim]Rendering, theming, HW decode, and pause-on-battery/fullscreen "
                "are configured in Caelestia's own Nexus settings — LiveWall only "
                "controls what it adds on top.[/dim]"
            )
            with Horizontal(classes="settings-row"):
                yield Label("Wallpaper backend")
                yield Select(
                    [(n, n) for n in available_backend_names()],
                    value=self.config.backend,
                    id="backend",
                )
            with Horizontal(classes="settings-row"):
                yield Label("Random interval")
                yield Select(
                    [(k, k) for k in RANDOM_INTERVAL_SECONDS],
                    value=self.config.random_interval,
                    id="random_interval",
                )
            yield Static(self._timer_status(), id="timer-status")
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
        old_backend_name = self.config.backend
        new_backend_name = self.query_one("#backend", Select).value
        backend_changed = new_backend_name != old_backend_name

        new_interval = self.query_one("#random_interval", Select).value
        interval_changed = new_interval != self.config.random_interval

        self.config.backend = new_backend_name
        self.config.random_interval = new_interval
        self.config.random_favorites_only = self.query_one("#random_favorites_only", Switch).value
        raw_tags = self.query_one("#random_tags", Input).value
        self.config.random_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        self.config.no_smart_colours = self.query_one("#no_smart_colours", Switch).value
        self.config.save()

        if backend_changed:
            try:
                get_backend(old_backend_name).stop()
            except Exception as exc:
                self.notify(f"Couldn't cleanly stop the previous backend: {exc}", severity="warning")
            self.app.backend = get_backend(new_backend_name)
            self.notify(f"Switched to '{new_backend_name}'. Re-apply a wallpaper to activate it.")

        if interval_changed:
            from livewall import systemd

            try:
                if new_interval == "off":
                    if systemd.is_installed():
                        systemd.uninstall()
                else:
                    systemd.install(new_interval)
            except (subprocess.CalledProcessError, OSError) as exc:
                self.notify(f"Settings saved, but the timer update failed: {exc}", severity="warning")
                self.app.pop_screen()
                return

            self.query_one("#timer-status", Static).update(self._timer_status())

        self.notify("Settings saved")
        self.app.pop_screen()

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.app.pop_screen()

    def action_close(self) -> None:
        self.app.pop_screen()


class WallpaperItem(ListItem):
    """One row in the library list."""

    def __init__(self, wallpaper: Wallpaper, is_current: bool = False) -> None:
        super().__init__(Label(self._label(wallpaper, is_current)))
        self.wallpaper_name = wallpaper.name

    @staticmethod
    def _label(w: Wallpaper, is_current: bool = False) -> str:
        current = "▶" if is_current else " "
        star = "★" if w.favorite else " "
        kind = "\U0001f3ac" if w.kind == "animated" else "\U0001f5bc"
        tags = f"  [{', '.join(w.tags)}]" if w.tags else ""
        return f"{current}{star} {kind} {w.name}{tags}"

    def refresh_label(self, wallpaper: Wallpaper, is_current: bool = False) -> None:
        self.query_one(Label).update(self._label(wallpaper, is_current))


class DetailPane(Vertical):
    """Right-hand pane: thumbnail + metadata for the selected wallpaper."""

    DEFAULT_CSS = """
    DetailPane {
        width: 46; padding: 1 2; margin-left: 1;
        background: $surface; border: round $border-blurred;
    }
    DetailPane #thumb-holder {
        height: 16; content-align: center middle;
        background: $background; border: round $border-blurred;
    }
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

        current = self.app.backend.current_path()
        is_current = current is not None and w.file_path == current
        lines = [
            f"[bold]{w.name}[/bold]" + (f"  [{SUCCESS}]▶ currently applied[/{SUCCESS}]" if is_current else ""),
        ]
        if w.favorite:
            lines.append(f"[{ACCENT}]★ favorite[/{ACCENT}]")
        lines += [
            f"Type: {w.kind}",
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
        ("o", "add_file", "Add File"),
        ("s", "open_settings", "Settings"),
    ]

    DEFAULT_CSS = """
    LibraryScreen #body { height: 1fr; padding: 1 1 1 2; }
    LibraryScreen #left { width: 1fr; padding-right: 1; }
    LibraryScreen #update-banner { height: 3; background: $accent; color: $background; padding: 0 1; margin-bottom: 1; }
    LibraryScreen #update-banner Static { width: 1fr; content-align: left middle; height: 3; }
    LibraryScreen #update-banner Button { margin-left: 1; }
    LibraryScreen #update-banner #update-dismiss { min-width: 3; }
    LibraryScreen #search { height: 3; border: round $border-blurred; margin-bottom: 1; }
    LibraryScreen #search:focus { border: round $accent; }
    LibraryScreen #category-row { height: 3; margin-bottom: 1; }
    LibraryScreen .category-btn { min-width: 3; height: 3; margin-right: 1; }
    LibraryScreen #wall-list { height: 1fr; background: $surface; border: round $border-blurred; }
    LibraryScreen #wall-list > ListItem.-highlight { background: $accent; color: $background; }
    """

    def __init__(self, library: Library, config: Config) -> None:
        super().__init__()
        self.library = library
        self.config = config
        self.active_category: str | None = None
        self._update_changelog: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Input(placeholder="Search name or tag...", id="search")
                with Horizontal(id="category-row"):
                    for category in CATEGORY_TAGS:
                        yield Button(category, id=_category_button_id(category), classes="category-btn")
                yield ListView(id="wall-list")
            yield DetailPane(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_list()
        notice = updater.read_and_clear_notice()
        if notice and notice.get("changelog"):
            self._update_changelog = notice["changelog"]
            self.query_one("#left", Vertical).mount(
                Horizontal(
                    Static("[bold]LiveWall was updated![/bold]"),
                    Button("What's new?", id="update-whats-new"),
                    Button("×", id="update-dismiss"),
                    id="update-banner",
                ),
                before="#search",
            )

    @on(Button.Pressed, "#update-whats-new")
    def on_update_whats_new(self) -> None:
        self.app.push_screen(UpdateChangelogScreen(self._update_changelog))

    @on(Button.Pressed, "#update-dismiss")
    def on_update_dismiss(self) -> None:
        self.query_one("#update-banner").remove()

    def refresh_list(self, query: str = "") -> None:
        list_view = self.query_one("#wall-list", ListView)
        list_view.clear()
        current = self.app.backend.current_path()
        tags = [self.active_category] if self.active_category else None
        for wallpaper in self.library.search(query=query, tags=tags):
            is_current = current is not None and wallpaper.file_path == current
            list_view.append(WallpaperItem(wallpaper, is_current=is_current))
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

    @on(Button.Pressed, ".category-btn")
    def on_category_button(self, event: Button.Pressed) -> None:
        pressed_id = event.button.id or ""
        category = next((c for c in CATEGORY_TAGS if _category_button_id(c) == pressed_id), None)
        if category is None:
            return

        if self.active_category == category:
            self.active_category = None
            event.button.variant = "default"
        else:
            for btn in self.query(".category-btn"):
                btn.variant = "default"
            self.active_category = category
            event.button.variant = "primary"

        self.refresh_list(self.query_one("#search", Input).value)

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

        def apply_to(target: str | None) -> None:
            if target is None:
                return  # cancelled out of the monitor picker
            monitor = None if target == "ALL" else target
            try:
                if monitor is None:
                    self.app.backend.set_wallpaper(
                        self.library.get(name).file_path, no_smart=self.config.no_smart_colours
                    )
                else:
                    self.app.backend.set_wallpaper_for_monitor(
                        monitor, self.library.get(name).file_path, no_smart=self.config.no_smart_colours
                    )
            except BackendUnavailableError as exc:
                self.notify(str(exc), severity="error")
                return
            except (FileNotFoundError, BackendApplyError, LiveWallError) as exc:
                self.notify(str(exc), severity="error")
                return
            self.notify(f"Applied '{name}'" + (f" on {monitor}" if monitor else ""))

        backend = self.app.backend
        if backend.supports_per_monitor:
            monitors = backend.list_monitor_targets()
            if len(monitors) > 1:
                self.app.push_screen(MonitorPickerScreen(monitors), apply_to)
                return
        apply_to("ALL")

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
            mpv_preview(wallpaper.file_path, blocking=False)
        except MpvNotAvailableError:
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

            result = self.library.import_folder(Path(folder))
            self.refresh_list(self.query_one("#search", Input).value)
            self.notify(
                f"Imported {len(result.added)}, "
                f"skipped {len(result.duplicates)} duplicates, "
                f"{len(result.unsupported)} unsupported"
            )

        self.app.push_screen(TextPromptScreen("Folder to import:", "~/Pictures/Wallpapers"), handle)

    def action_add_file(self) -> None:
        self.run_worker(self._add_file_via_picker(), exclusive=True)

    async def _add_file_via_picker(self) -> None:
        if shutil.which("zenity") is None:
            self.notify("zenity is not installed — can't open a file picker", severity="error")
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                "zenity",
                "--file-selection",
                "--title=Add Wallpaper to LiveWall",
                "--file-filter=Wallpapers | *.mp4 *.webm *.gif *.png *.jpg *.jpeg",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
        except OSError as exc:
            self.notify(f"Could not open file picker: {exc}", severity="error")
            return

        if proc.returncode != 0:
            return  # cancelled

        path_str = stdout.decode().strip()
        if not path_str:
            return

        try:
            wallpaper = self.library.add(Path(path_str))
        except DuplicateWallpaperError as exc:
            self.notify(f"Already in library as '{exc.existing.name}'", severity="warning")
            return
        except LiveWallError as exc:
            self.notify(str(exc), severity="error")
            return

        self.refresh_list(self.query_one("#search", Input).value)
        self.notify(f"Added '{wallpaper.name}' ({wallpaper.kind})")

    def action_open_settings(self) -> None:
        self.app.push_screen(SettingsScreen())

    def _refresh_current_item(self, wallpaper: Wallpaper) -> None:
        list_view = self.query_one("#wall-list", ListView)
        item = list_view.highlighted_child
        if isinstance(item, WallpaperItem):
            current = self.app.backend.current_path()
            is_current = current is not None and wallpaper.file_path == current
            item.refresh_label(wallpaper, is_current=is_current)


class LiveWallApp(App):
    """The LiveWall Textual application."""

    TITLE = "LiveWall"

    def __init__(self, backend: WallpaperBackend) -> None:
        super().__init__()
        self.library = Library()
        self.config = Config.load()
        self.backend = backend

    def on_mount(self) -> None:
        install_theme(self)
        self.push_screen(LibraryScreen(self.library, self.config))


def run() -> None:
    setup_logging()
    config = Config.load()
    try:
        backend = get_backend(config.backend)
    except BackendUnavailableError as exc:
        print(f"Error: {exc}")
        raise SystemExit(1) from exc
    LiveWallApp(backend).run()


if __name__ == "__main__":
    run()
