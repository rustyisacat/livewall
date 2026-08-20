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

import logging
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from livewall import bootstrap
from livewall.backends import BackendUnavailableError, get_backend
from livewall.config import Config
from livewall.gui_qt import theme
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


_SINGLE_INSTANCE_MUTEX_NAME = "Global\\LiveWallSingleInstance"
_ERROR_ALREADY_EXISTS = 183

# Keeps the mutex handle alive for the process's lifetime — releasing/GC'ing
# it early would let a second launch slip past the check below, same "must
# stay referenced somewhere real" pattern as
# _windows_wallpaper_host.py's _wndproc_ref.
_single_instance_mutex = None


def _already_running() -> bool:
    """Real Windows bug, confirmed via a genuine test session: nothing
    stopped multiple LiveWall.exe GUI instances from running concurrently.
    Each one independently tried to register the same global hotkey (every
    registration after the first silently fails) and raced to write
    windows_mpv_state.json — which is how that file ended up pointing at
    dead PIDs, one of them later recycled by an unrelated process.

    A named mutex is the standard Windows single-instance guard: the first
    process to call CreateMutexW with this name "owns" it; every later
    caller gets a valid (but redundant) handle back, and GetLastError()
    reports ERROR_ALREADY_EXISTS right after the call — that's how "someone
    else is already running" is detected, no other IPC needed."""
    import ctypes

    global _single_instance_mutex

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.GetLastError.argtypes = []
    kernel32.GetLastError.restype = ctypes.c_ulong

    handle = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
    if not handle:
        return False  # couldn't even create it — fail open, don't block a real launch
    _single_instance_mutex = handle
    return kernel32.GetLastError() == _ERROR_ALREADY_EXISTS


def _check_and_apply_update() -> None:
    """Best-effort self-update, run once at startup before anything else
    opens. Only ever does anything for a packaged build — see
    windows/updater.py's module docstring for why this doesn't run as its
    own scheduled task the way the Linux side's update-checker does."""
    if not getattr(sys, "frozen", False):
        return

    from livewall.windows import updater as win_updater

    try:
        info = win_updater.check_for_update()
        if info is None:
            return
        staging = win_updater.download_and_stage(info)
        if staging is None:
            return
        if win_updater.apply_and_relaunch(staging, info, tray="--tray" in sys.argv):
            # The batch helper is now waiting for this PID to exit before
            # it swaps the install directory and relaunches.
            sys.exit(0)
    except Exception:
        logging.getLogger(__name__).exception("Self-update check failed unexpectedly")


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

    if _already_running():
        logging.getLogger(__name__).info("Another LiveWall instance is already running — exiting")
        return

    _check_and_apply_update()

    config = Config.load()
    bootstrap.ensure_first_run_setup(config)
    try:
        backend = get_backend(config.backend)
    except BackendUnavailableError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # stay alive in the tray
    theme.apply(app)

    icon_path = _icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

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
