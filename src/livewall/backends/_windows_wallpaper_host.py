"""Windows-only helper process: acquires a window behind the desktop icons
and prints its handle, then idles pumping messages until killed.

This is the "WorkerW trick" used by Wallpaper Engine, Lively Wallpaper, and
various other live-wallpaper tools: ask Progman (the Program Manager window
that owns the desktop) to spawn a WorkerW window behind the desktop icons,
find it, and reparent a window of our own into it. mpv is then pointed at
that window's handle (``--wid=<hwnd>``) by the caller — this script's only
job is acquiring and holding the window; it does not know about mpv at all.

Deliberately a standalone script (not a class/importable API) run as its own
subprocess: WallpaperBackend.set_wallpaper() spawns this, reads the HWND it
prints on its first stdout line, then spawns mpv separately pointed at that
HWND. Killing this process (which windows_mpv.py tracks the PID of, same as
the Linux mpvpaper backend tracks mpvpaper's PID) destroys the window along
with it.

NOTE: everything here is unofficial/undocumented Windows behavior — the same
technique Wallpaper Engine/Lively Wallpaper rely on, but Microsoft could
change desktop-composition internals in a future Windows release. This has
not been tested on real Windows (no Windows machine was available during
development); it needs real-Windows validation before being relied on.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":
    raise RuntimeError("_windows_wallpaper_host is Windows-only")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SM_CXSCREEN = 0
SM_CYSCREEN = 1
WM_CLOSE = 0x0010
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
SW_SHOW = 5
SMTO_NORMAL = 0x0000
GWL_STYLE = -16

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


def _wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_CLOSE:
        kernel32.ExitProcess(0)
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _find_target_workerw() -> int:
    """Runs the Progman handshake and returns the WorkerW HWND that sits
    directly behind the desktop icons (the empty one — not the one hosting
    SHELLDLL_DefView, which owns the icons themselves)."""
    progman = user32.FindWindowW("Progman", None)
    # Undocumented message that tells Progman to spawn a WorkerW behind the
    # icons. No-ops harmlessly if one already exists. Sent via
    # SendMessageTimeoutW (not plain SendMessageW) so a slow/unresponsive
    # Progman can't hang this process indefinitely.
    result = wintypes.DWORD()
    user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, SMTO_NORMAL, 1000, ctypes.byref(result))

    target = ctypes.c_void_p(0)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum_windows(hwnd, _lparam):
        def_view = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if def_view:
            # The WorkerW we actually want is the *next* sibling after the
            # one hosting SHELLDLL_DefView — the empty one behind it.
            candidate = user32.FindWindowExW(None, hwnd, "WorkerW", None)
            if candidate:
                target.value = candidate
        return True

    user32.EnumWindows(_enum_windows, 0)
    return target.value or 0


def main() -> None:
    hinstance = kernel32.GetModuleHandleW(None)
    class_name = "LiveWallWallpaperHost"

    wndproc = WNDPROC(_wndproc)
    wc = WNDCLASS()
    wc.style = 0
    wc.lpfnWndProc = wndproc
    wc.hInstance = hinstance
    wc.lpszClassName = class_name
    user32.RegisterClassW(ctypes.byref(wc))

    width = user32.GetSystemMetrics(SM_CXSCREEN)
    height = user32.GetSystemMetrics(SM_CYSCREEN)

    hwnd = user32.CreateWindowExW(
        0, class_name, "LiveWall Wallpaper", WS_POPUP,
        0, 0, width, height, None, None, hinstance, None,
    )
    if not hwnd:
        print("ERROR: CreateWindowExW failed", file=sys.stderr, flush=True)
        sys.exit(1)

    workerw = _find_target_workerw()
    if not workerw:
        print("ERROR: could not locate a WorkerW window behind the desktop icons", file=sys.stderr, flush=True)
        sys.exit(1)

    user32.SetParent(hwnd, workerw)
    # Strip any remaining caption/border bits and show it filling the parent.
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style | WS_VISIBLE)
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.MoveWindow(hwnd, 0, 0, width, height, True)

    # The caller (windows_mpv.py) reads this single line to get the HWND to
    # hand to `mpv.exe --wid=<hwnd>`.
    print(hwnd, flush=True)

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    main()
