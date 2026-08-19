"""System tray integration — owns the global quick-picker hotkey while
LiveWall runs in the background, the Windows-GUI counterpart to hypr.py's
Super+Shift+B keybind. Real architectural difference from Linux: Hyprland
owns that keybind permanently by patching its own config, independent of
whether LiveWall is running; RegisterHotKey (see windows/hotkey.py) only
works while this tray process is alive, which is why "run at login" (see
windows/startup.py's autostart functions) matters here.
"""

from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtCore import QAbstractNativeEventFilter
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

logger = logging.getLogger("livewall.gui_qt")

_WM_HOTKEY = 0x0312


class _HotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(self, hotkey_id: int, on_trigger) -> None:
        super().__init__()
        self._hotkey_id = hotkey_id
        self._on_trigger = on_trigger

    def nativeEventFilter(self, event_type, message):
        if sys.platform != "win32":
            return False, 0
        # MSG* — id is the WPARAM field, which carries our registered hotkey id.
        msg = ctypes.cast(int(message), ctypes.POINTER(ctypes.c_long * 6)).contents
        if msg[1] == _WM_HOTKEY and msg[2] == self._hotkey_id:
            self._on_trigger()
            return True, 0
        return False, 0


class TrayIcon(QSystemTrayIcon):
    """Owns the hotkey and the quick-picker popup; the main library window
    is opened on demand from the tray menu, not required to be open."""

    def __init__(self, icon_path: str | None, open_main_window, open_quick_picker) -> None:
        icon = QIcon(icon_path) if icon_path else QIcon()
        super().__init__(icon)
        self.setToolTip("LiveWall")

        menu = QMenu()
        menu.addAction("Open LiveWall", open_main_window)
        menu.addSeparator()
        menu.addAction("Quit", QApplication.quit)
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

        self._open_main_window = open_main_window
        self._open_quick_picker = open_quick_picker
        self._hotkey_widget: QWidget | None = None
        self._event_filter: _HotkeyEventFilter | None = None
        if sys.platform == "win32":
            self._register_hotkey()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._open_main_window()

    def _register_hotkey(self) -> None:
        from livewall.windows import hotkey

        # A hidden widget purely to own a real HWND for RegisterHotKey to
        # attach to — decoupled from whether the library browser is open.
        self._hotkey_widget = QWidget()
        self._hotkey_widget.setWindowFlag(True)
        hwnd = int(self._hotkey_widget.winId())
        ok = hotkey.register(hwnd)
        if not ok:
            logger.warning("Could not register the quick-picker global hotkey")
            return
        self._event_filter = _HotkeyEventFilter(hotkey.QUICK_PICKER_HOTKEY_ID, self._open_quick_picker)
        QApplication.instance().installNativeEventFilter(self._event_filter)

    def shutdown(self) -> None:
        if sys.platform == "win32" and self._hotkey_widget is not None:
            from livewall.windows import hotkey

            hotkey.unregister(int(self._hotkey_widget.winId()))
