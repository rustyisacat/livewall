"""Entry point for the Windows GUI. Also doubles as the target Task
Scheduler invokes (see windows/task_scheduler.py's _livewall_bin(), which
points scheduled tasks at this same packaged executable): a frozen
LiveWall.exe with a recognized CLI-style first argument (e.g. `restore`,
`random`, `power-check`) delegates straight to the existing cli.py instead
of opening the GUI, so one executable serves both roles — exactly how the
Linux side has one `livewall` binary for both `livewall gui` and
`livewall restore` etc.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from livewall.backends import BackendUnavailableError, get_backend
from livewall.config import Config
from livewall.gui_qt.main_window import MainWindow
from livewall.gui_qt.quick_picker import QuickPicker
from livewall.gui_qt.tray import TrayIcon
from livewall.library import Library
from livewall.utils import setup_logging

# Recognized non-GUI subcommands a scheduled task might invoke — kept in
# sync with cli.py's dispatch-with-backend/lib-only command sets.
_CLI_SUBCOMMANDS = {
    "list", "add", "import", "sync", "refresh-thumbs", "remove", "rename",
    "favorite", "tag", "info", "apply", "random", "status", "restart-shell",
    "doctor", "ensure-playing", "power-check", "restore", "preview",
    "install", "uninstall",
}


def _icon_path() -> str | None:
    from pathlib import Path

    ico = Path(__file__).resolve().parent.parent.parent.parent / "data" / "livewall.ico"
    return str(ico) if ico.exists() else None


def run() -> None:
    # Hidden mode used by backends/windows_mpv.py to acquire the
    # WorkerW-parented window when running from a *frozen* build — there's
    # no standalone _windows_wallpaper_host.py file to point a subprocess at
    # once everything's bundled into one .exe, so the frozen build re-invokes
    # itself with this flag instead (dev/source runs still spawn the plain
    # script directly; see windows_mpv.py's _start_host()).
    if len(sys.argv) > 1 and sys.argv[1] == "--wallpaper-host":
        from livewall.backends._windows_wallpaper_host import main as wallpaper_host_main

        wallpaper_host_main()
        return

    if len(sys.argv) > 1 and sys.argv[1] in _CLI_SUBCOMMANDS:
        from livewall.cli import main as cli_main

        sys.exit(cli_main(sys.argv[1:]))

    setup_logging()
    config = Config.load()
    try:
        backend = get_backend(config.backend)
    except BackendUnavailableError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # stay alive in the tray

    library = Library()
    main_window = MainWindow(library, config, backend)

    quick_picker = QuickPicker()

    def open_main_window() -> None:
        main_window.show()
        main_window.raise_()
        main_window.activateWindow()

    def open_quick_picker() -> None:
        quick_picker.show()
        quick_picker.raise_()
        quick_picker.activateWindow()

    tray = TrayIcon(_icon_path(), open_main_window, open_quick_picker)
    tray.show()

    # `--tray`: launched via the login-autostart entry — stay minimized in
    # the tray instead of popping the library browser immediately.
    if "--tray" not in sys.argv:
        open_main_window()

    exit_code = app.exec()
    tray.shutdown()
    sys.exit(exit_code)


if __name__ == "__main__":
    run()
