"""Shared Textual theme for the Linux TUI (gui.py, picker.py) — the same
palette as the Windows GUI's gui_qt/theme.py, lifted from data/livewall.svg's
own gradients, so both platforms read as the same product rather than one
being styled and the other left at Textual's stock defaults.

Register once per App (register_theme + set app.theme) and Textual's
built-in widgets (Button, Input, ListView, Header, Footer, scrollbars, ...)
pick the new colours up automatically through Textual's own $variable
system — this isn't reimplementing widget styling, just retargeting it.
"""

from __future__ import annotations

from textual.theme import Theme

BACKGROUND = "#1b1e3f"
SURFACE = "#232c5c"
PANEL = "#20244a"
BORDER = "#3a4180"  # visible but subtle against SURFACE — see the border-blurred override below
FOREGROUND = "#f2f1fa"
ACCENT = "#ff7a4d"  # the logo's play-button orange
SECONDARY = "#8fb4f0"  # the logo's sky-gradient blue
SUCCESS = "#6fcf97"
WARNING = "#f2c14e"
ERROR = "#ff6b6b"

LIVEWALL_THEME = Theme(
    name="livewall",
    primary=ACCENT,
    secondary=SECONDARY,
    accent=ACCENT,
    background=BACKGROUND,
    surface=SURFACE,
    panel=PANEL,
    foreground=FOREGROUND,
    success=SUCCESS,
    warning=WARNING,
    error=ERROR,
    dark=True,
    # Textual derives border-blurred from the palette on its own, and the
    # result was nearly invisible against SURFACE — every unfocused Input
    # looked borderless. Pinning it explicitly keeps inputs scannable at a
    # glance instead of only readable once focused (when border turns
    # $accent).
    variables={"border-blurred": BORDER},
)


def install(app) -> None:
    app.register_theme(LIVEWALL_THEME)
    app.theme = "livewall"
