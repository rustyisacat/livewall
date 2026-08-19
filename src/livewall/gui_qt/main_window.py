"""The main library browser window — the Windows-GUI counterpart to
gui.py's LibraryScreen. Same feature set (search, category filters, apply/
favorite/preview/delete/rename/tag/import/add-file, settings), same
delegation into Library/WallpaperBackend, expressed with Qt widgets.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from livewall.gui_qt import theme

from livewall.backends import BackendApplyError, BackendUnavailableError, WallpaperBackend
from livewall.config import Config
from livewall.gui_qt.settings_dialog import SettingsDialog
from livewall.library import DuplicateWallpaperError, Library, LiveWallError
from livewall.preview import MpvNotAvailableError, preview as mpv_preview

logger = logging.getLogger("livewall.gui_qt")

CATEGORY_TAGS = ["Cozy", "Synthwave", "Anime", "Space", "Nature", "Pixel Art", "Cyberpunk"]

_NAME_ROLE = Qt.ItemDataRole.UserRole


def _item_label(w, is_current: bool) -> str:
    current = "▶" if is_current else " "
    star = "★" if w.favorite else " "
    kind = "\U0001f3ac" if w.kind == "animated" else "\U0001f5bc"
    tags = f"  [{', '.join(w.tags)}]" if w.tags else ""
    return f"{current}{star} {kind} {w.name}{tags}"


class MainWindow(QMainWindow):
    def __init__(self, library: Library, config: Config, backend: WallpaperBackend) -> None:
        super().__init__()
        self.library = library
        self.config = config
        self.backend = backend
        self.active_category: str | None = None

        self.setWindowTitle("LiveWall")
        self.resize(1000, 650)
        self._build_ui()
        self.refresh_list()

    # ---- layout ----------------------------------------------------

    def _build_ui(self) -> None:
        toolbar = QToolBar("Actions")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        actions = [
            ("Apply", self.apply_selected),
            ("Favorite", self.toggle_favorite),
            ("Preview", self.preview_selected),
            ("Rename", self.rename_selected),
            ("Tags", self.edit_tags),
            ("Delete", self.delete_selected),
            ("Import Folder…", self.import_folder),
            ("Add File…", self.add_file),
            ("Settings", self.open_settings),
        ]
        for label, handler in actions:
            toolbar.addAction(label, handler)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.search_box = QLineEdit(placeholderText="Search name or tag…")
        self.search_box.textChanged.connect(self._on_search_changed)
        root.addWidget(self.search_box)

        category_row = QHBoxLayout()
        category_row.setSpacing(6)
        self.category_buttons: dict[str, QPushButton] = {}
        for category in CATEGORY_TAGS:
            btn = QPushButton(category)
            btn.setObjectName("pill")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, c=category: self._on_category_clicked(c))
            category_row.addWidget(btn)
            self.category_buttons[category] = btn
        category_row.addStretch()
        root.addLayout(category_row)

        splitter = QSplitter()
        splitter.setHandleWidth(12)
        self.list_widget = QListWidget()
        self.list_widget.setSpacing(1)
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        splitter.addWidget(self.list_widget)

        detail = QFrame()
        detail.setObjectName("detailCard")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(16, 16, 16, 16)
        detail_layout.setSpacing(12)
        self.thumbnail_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setObjectName("thumbnail")
        self.thumbnail_label.setMinimumHeight(220)
        self.meta_label = QLabel(wordWrap=True)
        self.meta_label.setTextFormat(Qt.TextFormat.RichText)
        detail_layout.addWidget(self.thumbnail_label)
        detail_layout.addWidget(self.meta_label)
        detail_layout.addStretch()
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter)

        self.setStatusBar(QStatusBar())

    # ---- list/search/filter -----------------------------------------

    def refresh_list(self, query: str = "") -> None:
        self.list_widget.clear()
        current = self.backend.current_path()
        tags = [self.active_category] if self.active_category else None
        for wallpaper in self.library.search(query=query, tags=tags):
            is_current = current is not None and wallpaper.file_path == current
            item = QListWidgetItem(_item_label(wallpaper, is_current))
            item.setData(_NAME_ROLE, wallpaper.name)
            self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        else:
            self._show_detail(None)

    def _on_search_changed(self, text: str) -> None:
        self.refresh_list(text)

    def _on_category_clicked(self, category: str) -> None:
        if self.active_category == category:
            self.active_category = None
            self.category_buttons[category].setChecked(False)
        else:
            for name, btn in self.category_buttons.items():
                btn.setChecked(name == category)
            self.active_category = category
        self.refresh_list(self.search_box.text())

    def selected_name(self) -> str | None:
        item = self.list_widget.currentItem()
        return item.data(_NAME_ROLE) if item else None

    def _on_selection_changed(self, *_args) -> None:
        name = self.selected_name()
        self._show_detail(self.library.info(name) if name else None)

    def _show_detail(self, info) -> None:
        if info is None:
            self.thumbnail_label.clear()
            self.meta_label.setText("(no wallpapers in library)")
            return

        w = info.wallpaper
        if info.thumbnail_path is not None and info.thumbnail_path.exists():
            from PySide6.QtGui import QPixmap

            pixmap = QPixmap(str(info.thumbnail_path))
            self.thumbnail_label.setPixmap(
                pixmap.scaled(320, 220, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        else:
            self.thumbnail_label.clear()
            self.thumbnail_label.setText("(no preview)")

        current = self.backend.current_path()
        is_current = current is not None and w.file_path == current
        title = f"<div style='font-size:16px; font-weight:600; color:{theme.TEXT}'>{w.name}</div>"
        if is_current:
            title += (
                f"<div style='color:{theme.SUCCESS}; font-weight:600; margin-bottom:4px'>"
                "▶ currently applied</div>"
            )
        if w.favorite:
            title += f"<div style='color:{theme.ACCENT}; margin-bottom:4px'>★ favorite</div>"
        rows = [
            ("Type", w.kind),
            ("Resolution", info.metadata.resolution),
            ("Aspect ratio", info.metadata.aspect_ratio or "unknown"),
            ("Duration", info.duration_human),
            ("Animated", "yes" if info.metadata.animated else "no"),
            ("Size", info.size_human),
            ("Tags", ", ".join(w.tags) or "(none)"),
        ]
        table = "".join(
            f"<tr>"
            f"<td style='color:{theme.TEXT_MUTED}; padding:2px 10px 2px 0'>{label}</td>"
            f"<td style='color:{theme.TEXT}'>{value}</td>"
            f"</tr>"
            for label, value in rows
        )
        self.meta_label.setText(f"{title}<table style='margin-top:6px'>{table}</table>")

    def _refresh_current_item(self, wallpaper) -> None:
        item = self.list_widget.currentItem()
        if item is not None:
            current = self.backend.current_path()
            is_current = current is not None and wallpaper.file_path == current
            item.setText(_item_label(wallpaper, is_current))

    # ---- actions ------------------------------------------------------

    def apply_selected(self) -> None:
        name = self.selected_name()
        if not name:
            return
        try:
            self.backend.set_wallpaper(self.library.get(name).file_path, no_smart=self.config.no_smart_colours)
        except (BackendUnavailableError, FileNotFoundError, BackendApplyError, LiveWallError) as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        self.statusBar().showMessage(f"Applied '{name}'", 3000)

    def toggle_favorite(self) -> None:
        name = self.selected_name()
        if not name:
            return
        wallpaper = self.library.toggle_favorite(name)
        self._refresh_current_item(wallpaper)
        self._show_detail(self.library.info(name))

    def preview_selected(self) -> None:
        name = self.selected_name()
        if not name:
            return
        wallpaper = self.library.get(name)
        try:
            mpv_preview(wallpaper.file_path, blocking=False)
        except MpvNotAvailableError:
            self.statusBar().showMessage("mpv is not installed", 5000)
            return
        self.statusBar().showMessage(f"Previewing '{name}' in mpv", 3000)

    def delete_selected(self) -> None:
        name = self.selected_name()
        if not name:
            return
        reply = QMessageBox.question(self, "Delete wallpaper", f"Delete '{name}' from the library?")
        if reply == QMessageBox.StandardButton.Yes:
            self.library.remove(name)
            self.refresh_list(self.search_box.text())
            self.statusBar().showMessage(f"Removed '{name}'", 3000)

    def rename_selected(self) -> None:
        name = self.selected_name()
        if not name:
            return
        new_name, ok = QInputDialog.getText(self, "Rename wallpaper", "New name:", text=name)
        if ok and new_name and new_name != name:
            try:
                self.library.rename(name, new_name)
            except LiveWallError as exc:
                self.statusBar().showMessage(str(exc), 5000)
                return
            self.refresh_list(self.search_box.text())

    def edit_tags(self) -> None:
        name = self.selected_name()
        if not name:
            return
        wallpaper = self.library.get(name)
        raw, ok = QInputDialog.getText(
            self, "Edit tags",
            f"Tags for '{name}' (comma-separated, e.g. {', '.join(CATEGORY_TAGS[:3])}):",
            text=", ".join(wallpaper.tags),
        )
        if ok:
            tags = [t.strip() for t in raw.split(",") if t.strip()]
            updated = self.library.set_tags(name, tags)
            self._refresh_current_item(updated)
            self._show_detail(self.library.info(name))

    def import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder to import", str(Path.home() / "Pictures"))
        if not folder:
            return
        result = self.library.import_folder(Path(folder))
        self.refresh_list(self.search_box.text())
        self.statusBar().showMessage(
            f"Imported {len(result.added)}, skipped {len(result.duplicates)} duplicates, "
            f"{len(result.unsupported)} unsupported", 5000,
        )

    def add_file(self) -> None:
        path_str, _filter = QFileDialog.getOpenFileName(
            self, "Add Wallpaper to LiveWall", str(Path.home() / "Pictures"),
            "Wallpapers (*.mp4 *.webm *.mkv *.gif *.png *.jpg *.jpeg)",
        )
        if not path_str:
            return
        try:
            wallpaper = self.library.add(Path(path_str))
        except DuplicateWallpaperError as exc:
            self.statusBar().showMessage(f"Already in library as '{exc.existing.name}'", 5000)
            return
        except LiveWallError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        self.refresh_list(self.search_box.text())
        self.statusBar().showMessage(f"Added '{wallpaper.name}' ({wallpaper.kind})", 3000)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self.backend, self)
        if dialog.exec():
            self.backend = dialog.backend  # may have been swapped
