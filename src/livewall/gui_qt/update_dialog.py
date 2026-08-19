"""Changelog dialog shown when the "LiveWall was updated!" banner's
"What's new?" button is clicked — the Qt counterpart to gui.py's
UpdateChangelogScreen. Reads the same notice shape both updater.py
(Linux) and windows/updater.py write, so this one dialog serves either
update path."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QScrollArea, QVBoxLayout, QWidget


class UpdateChangelogDialog(QDialog):
    def __init__(self, changelog: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LiveWall was updated")
        self.resize(480, 360)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        root.addWidget(QLabel("<b>What's new</b>"))

        body = QLabel("<br>".join(changelog) if changelog else "(no details available)")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        body.setContentsMargins(4, 4, 4, 4)
        scroll = QScrollArea()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        root.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
