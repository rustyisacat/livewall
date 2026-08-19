"""The Windows-native GUI (PySide6) — LiveWall's primary interface on
Windows, where a terminal isn't expected. Mirrors gui.py/picker.py's
feature set and business-logic pattern (thin delegation into
``Library``/``WallpaperBackend`` methods) re-expressed with Qt widgets
instead of Textual ones; shares no Textual code.
"""
