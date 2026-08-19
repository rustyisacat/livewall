"""The quick-picker popup — the Windows-GUI counterpart to picker.py's
PickerApp. Search + Enter-to-apply + Escape-to-cancel, and the same
resilience pattern worth preserving: if the configured backend is broken,
the window still opens and shows the problem inline rather than silently
failing to appear (the whole point of a hotkey-triggered popup is that it's
always there when you press the key).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from livewall.backends import BackendApplyError, BackendUnavailableError, get_backend
from livewall.config import Config
from livewall.gui_qt import theme
from livewall.library import Library, LiveWallError

_NAME_ROLE = Qt.ItemDataRole.UserRole


class QuickPicker(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LiveWall Picker")
        self.setWindowFlag(Qt.WindowType.Popup)
        self.setObjectName("quickPicker")
        self.resize(480, 360)

        self.library = Library()
        self.config = Config.load()
        self.backend = None
        self.backend_error: str | None = None
        try:
            self.backend = get_backend(self.config.backend)
        except BackendUnavailableError as exc:
            self.backend_error = str(exc)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        self.search_box = QLineEdit(placeholderText="Search wallpapers…")
        self.search_box.textChanged.connect(self._refresh_list)
        self.search_box.returnPressed.connect(self._apply_selected)
        layout.addWidget(self.search_box)

        self.list_widget = QListWidget()
        self.list_widget.itemActivated.connect(lambda _item: self._apply_selected())
        layout.addWidget(self.list_widget)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming convention)
        super().showEvent(event)
        self.search_box.setFocus()
        self._refresh_list("")

    def _refresh_list(self, query: str) -> None:
        self.search_box.setStyleSheet("")
        self.list_widget.clear()
        for wallpaper in self.library.search(query=query, prefer_animated_format=True):
            item = QListWidgetItem(wallpaper.name)
            item.setData(_NAME_ROLE, wallpaper.name)
            self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def _apply_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            self.close()
            return

        if self.backend is None:
            self.search_box.setText(self.backend_error or "No backend configured")
            self.search_box.setStyleSheet(f"color: {theme.DANGER};")
            return

        name = item.data(_NAME_ROLE)
        try:
            self.backend.set_wallpaper(self.library.get(name).file_path, no_smart=self.config.no_smart_colours)
        except (BackendUnavailableError, FileNotFoundError, BackendApplyError, LiveWallError) as exc:
            self.search_box.setText(str(exc))
            self.search_box.setStyleSheet(f"color: {theme.DANGER};")
            return
        self.close()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
