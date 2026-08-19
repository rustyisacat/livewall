"""Visual theme for the Windows GUI — a dark palette lifted straight from
data/livewall.svg's own gradients (deep indigo/navy background, the same
orange used for the logo's play-button badge as the one accent colour)
instead of leaving every widget at its unstyled default look.

Applied once, at QApplication level, in gui_qt/app.py's run(). Individual
widgets opt into the extra selectors below (accent buttons, pill-style
category filters, card frames) via setObjectName()/setProperty() rather
than needing bespoke styling of their own.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Pulled from data/livewall.svg's gradient stops, so the app chrome and the
# app icon read as the same product.
BG = "#1b1e3f"  # window background (gradient's dark end)
BG_ALT = "#20244a"  # toolbar / status bar — one step up from BG
PANEL = "#232c5c"  # cards, inputs, list items (peakFront's top stop)
PANEL_HOVER = "#2a3568"
BORDER = "#33397a"
TEXT = "#f2f1fa"  # near-white, matches the badge's play triangle
TEXT_MUTED = "#a9b0d9"
ACCENT = "#ff7a4d"  # the logo's play-button orange
ACCENT_HOVER = "#ff9068"
ACCENT_PRESSED = "#e8663c"
ACCENT_TEXT = "#1b1e3f"  # dark text on the light-ish orange accent
LINK = "#8fb4f0"  # the logo's sky-gradient blue, for secondary highlights
SUCCESS = "#6fcf97"
DANGER = "#ff6b6b"

STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 13px;
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_TEXT};
}}

QMainWindow, QDialog {{
    background: {BG};
}}

QToolBar {{
    background: {BG_ALT};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    spacing: 4px;
}}

QToolButton {{
    background: transparent;
    color: {TEXT};
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 6px 12px;
}}

QToolButton:hover {{
    background: {PANEL_HOVER};
    border: 1px solid {BORDER};
}}

QToolButton:pressed {{
    background: {ACCENT};
    color: {ACCENT_TEXT};
}}

QStatusBar {{
    background: {BG_ALT};
    color: {TEXT_MUTED};
    border-top: 1px solid {BORDER};
}}

QLineEdit, QComboBox, QSpinBox {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 7px 10px;
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {ACCENT};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_TEXT};
    outline: none;
}}

QListWidget {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
    outline: none;
}}

QListWidget::item {{
    padding: 8px 10px;
    margin: 2px 0;
    border-radius: 6px;
    color: {TEXT};
}}

QListWidget::item:hover {{
    background: {PANEL_HOVER};
}}

QListWidget::item:selected {{
    background: {ACCENT};
    color: {ACCENT_TEXT};
}}

/* Category filter pills — plain QPushButtons with setObjectName("pill") */
QPushButton#pill {{
    background: {PANEL};
    color: {TEXT_MUTED};
    border: 1px solid {BORDER};
    border-radius: 13px;
    padding: 5px 14px;
}}

QPushButton#pill:hover {{
    border: 1px solid {ACCENT};
    color: {TEXT};
}}

QPushButton#pill:checked {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: {ACCENT_TEXT};
    font-weight: 600;
}}

/* Ordinary dialog buttons (Settings, message boxes, quick-picker actions) */
QPushButton {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 7px 16px;
}}

QPushButton:hover {{
    background: {PANEL_HOVER};
    border: 1px solid {ACCENT};
}}

QPushButton:pressed {{
    background: {ACCENT};
    color: {ACCENT_TEXT};
    border: 1px solid {ACCENT};
}}

QPushButton:default {{
    background: {ACCENT};
    color: {ACCENT_TEXT};
    border: 1px solid {ACCENT};
    font-weight: 600;
}}

QPushButton:default:hover {{
    background: {ACCENT_HOVER};
}}

QPushButton:default:pressed {{
    background: {ACCENT_PRESSED};
}}

/* The "LiveWall was updated!" banner atop the library browser */
QFrame#updateBanner {{
    background: {ACCENT};
    border-radius: 8px;
}}

QFrame#updateBanner QLabel {{
    color: {ACCENT_TEXT};
    background: transparent;
}}

/* Card frame around the library browser's thumbnail/detail pane */
QFrame#detailCard {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}

QLabel#thumbnail {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 10px;
    font-weight: 600;
    color: {TEXT};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: {TEXT_MUTED};
}}

QCheckBox {{
    spacing: 8px;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {BORDER};
    background: {PANEL};
}}

QCheckBox::indicator:checked {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
}}

QSplitter::handle {{
    background: {BORDER};
    width: 1px;
}}

QScrollBar:vertical {{
    background: {BG};
    width: 12px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {BORDER};
    min-height: 24px;
    border-radius: 5px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background: {PANEL_HOVER};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

/* The quick picker is a frameless popup — its own outline stands in for
   the window chrome it doesn't have. */
QWidget#quickPicker {{
    background: {BG};
    border: 1px solid {ACCENT};
    border-radius: 10px;
}}

QToolTip {{
    background: {PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 8px;
}}
"""


def apply(app: QApplication) -> None:
    # Fusion renders identically across platforms and takes QSS cleanly —
    # the native Windows style fights custom stylesheets in a lot of corners
    # (combo boxes, checkboxes) that Fusion just doesn't.
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(PANEL))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(BG_ALT))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(PANEL))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(PANEL))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(ACCENT_TEXT))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_MUTED))
    palette.setColor(QPalette.ColorRole.Link, QColor(LINK))
    app.setPalette(palette)

    app.setStyleSheet(STYLESHEET)
