"""Windows-only helper process: acquires a window behind the desktop icons
and prints its handle, then idles pumping messages until killed.

This is the "WorkerW trick" used by Wallpaper Engine, Lively Wallpaper, and
various other live-wallpaper tools: ask Progman (the Program Manager window
that owns the desktop) to spawn a WorkerW window behind the desktop icons,
find it, and reparent a window of our own into it. mpv is then pointed at
that window's handle (``--wid=<hwnd>``) by the caller — this script's only
job is acquiring and holding the window(s); it does not know about mpv at
all.

Per-monitor support: creates one top-level window sized to the bounding
rect of *every* attached monitor (not just the primary — the old
single-monitor version only covered the primary display, so a Windows
secondary monitor likely showed nothing at all), reparented into WorkerW as
before, plus one borderless WS_CHILD window per monitor positioned to that
monitor's own rect within it. Prints one "<target> <hwnd>" line per target
("ALL <top-level hwnd>" for the existing mirrored case — one mpv instance
scaled across everything — then "<device name> <child hwnd>" per monitor
for a per-monitor render), followed by a final "DONE" line once every
target has been reported (the caller has no other way to know how many
lines are coming, since monitor count varies). backends/windows_mpv.py's
caller only ever points mpv at ONE of these hwnds at a time per target —
using the top-level ALL hwnd and per-monitor child hwnds simultaneously
would double-render, so that mutual exclusion is enforced on the
windows_mpv.py side, not here.

Deliberately a standalone script (not a class/importable API) run as its
own subprocess: WallpaperBackend.set_wallpaper()/set_wallpaper_for_monitor()
spawn this once (reusing an already-running one — see windows_mpv.py's
host-reuse logic — rather than spawning a second one, which would duplicate
this whole window stack), read the hwnd map it prints, then spawn mpv
separately pointed at the hwnd for whichever target is being applied.
Killing this process (which windows_mpv.py tracks the PID of, same as the
Linux mpvpaper backend tracks mpvpaper's PID) destroys every window along
with it.

NOTE: everything here is unofficial/undocumented Windows behavior — the same
technique Wallpaper Engine/Lively Wallpaper rely on, but Microsoft could
change desktop-composition internals in a future Windows release. This has
not been tested on real Windows (no Windows machine was available during
development); it needs real-Windows validation before being relied on. The
per-monitor child-window positioning/DPI handling below is new and
particularly untested — a real ctypes mistake already slipped through once
in this same GUI (gui_qt/tray.py, fixed after the first real-hardware run),
so treat this file's correctness with real skepticism until it's actually
been run on Windows.

A second real ctypes mistake was caught the same way (a crash screenshot
from an actual Windows run): every windll call here was missing explicit
.argtypes/.restype declarations. Without them, ctypes has to guess how to
marshal each argument/return value — for a plain Python int with no
declared type, it guesses a 32-bit C `int`/`long`, which is wrong for
WPARAM/LPARAM (which are pointer-sized — 64-bit on x64 Windows) and for
every HWND/HMODULE/pointer return value. This mostly went unnoticed
because handle values are usually small enough to fit, but _wndproc's
DefWindowProcW call crashed for real the moment a genuine wide LPARAM
value came through: `ctypes.ArgumentError: argument 4: OverflowError:
int too long to convert`. ctypes.wintypes.WPARAM/LPARAM themselves are
ALSO too narrow for this (defined as plain c_ulong/c_long, which are only
32-bit under Windows' LLP64 model) — the explicit c_size_t/c_ssize_t
aliases below are the actually-correct pointer-width types, used both in
the WNDPROC/MONITORENUMPROC callback signatures and in every argtypes/
restype declaration that touches a WPARAM, LPARAM, or LRESULT.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":
    raise RuntimeError("_windows_wallpaper_host is Windows-only")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_CLOSE = 0x0010
WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
SW_SHOW = 5
SMTO_NORMAL = 0x0000
GWL_STYLE = -16
ALL_TARGET = "ALL"

# Pointer-width-correct WPARAM/LPARAM/LRESULT — see the module docstring's
# second note. Used everywhere instead of ctypes.wintypes.WPARAM/LPARAM.
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, LPARAM)


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


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), LPARAM
)

# Explicit argtypes/restype for every windll function called below — see
# the module docstring's second note for why this matters (it's the fix
# for a real crash, not just tidiness). HWND/HMODULE/HMENU/HANDLE-typed
# values are already pointer-width via ctypes.wintypes, so only the
# WPARAM/LPARAM/LRESULT/LONG_PTR spots below use the explicit aliases
# from above instead.
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, WPARAM, LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
]
user32.SendMessageTimeoutW.restype = LRESULT
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.EnumWindows.argtypes = [WNDENUMPROC, LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC, LPARAM]
user32.EnumDisplayMonitors.restype = wintypes.BOOL
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.SetParent.restype = wintypes.HWND
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]
user32.MoveWindow.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
user32.SetProcessDPIAware.argtypes = []
user32.SetProcessDPIAware.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.ExitProcess.argtypes = [wintypes.UINT]
kernel32.ExitProcess.restype = None


# Keeps the ctypes WNDPROC callback object alive for the process's
# lifetime — it would otherwise be garbage-collected once
# _register_window_class() returns, and Windows calling into a dangling
# callback pointer afterward would crash the process.
_wndproc_ref: WNDPROC | None = None


def _wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_CLOSE:
        kernel32.ExitProcess(0)
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _set_dpi_aware() -> None:
    """Best-effort per-monitor DPI awareness — without this, monitor rects
    from GetMonitorInfoW can come back virtualized/scaled on a mixed-DPI
    multi-monitor setup, silently breaking the coordinate translation math
    below. Falls back through older APIs for pre-Windows-10-1607 systems;
    failing outright here isn't fatal (single-DPI setups are unaffected
    either way), so every failure is swallowed rather than raised."""
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4, per
        # Microsoft's winuser.h — passed as an opaque pointer-sized value.
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE == 2
        shcore = ctypes.windll.shcore
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long  # HRESULT
        if shcore.SetProcessDpiAwareness(2) == 0:  # S_OK
            return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def _enumerate_monitors() -> list[tuple[str, RECT]]:
    """(device name, rect) for every attached monitor, in whatever order
    EnumDisplayMonitors happens to enumerate them."""
    monitors: list[tuple[str, RECT]] = []

    def _callback(hmonitor, _hdc, _rect_ptr, _lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            monitors.append((info.szDevice, info.rcMonitor))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_callback), 0)
    return monitors


def _bounding_rect(monitors: list[tuple[str, RECT]]) -> RECT:
    """The smallest rect containing every monitor's rect — covers the
    whole virtual desktop, not just the primary display, and correctly
    handles monitors arranged left-of/above the primary (which have
    negative coordinates in Windows' virtual-screen space)."""
    lefts = [rect.left for _name, rect in monitors]
    tops = [rect.top for _name, rect in monitors]
    rights = [rect.right for _name, rect in monitors]
    bottoms = [rect.bottom for _name, rect in monitors]
    return RECT(min(lefts), min(tops), max(rights), max(bottoms))


def _register_window_class(name: str) -> None:
    global _wndproc_ref
    _wndproc_ref = WNDPROC(_wndproc)
    wc = WNDCLASS()
    wc.style = 0
    wc.lpfnWndProc = _wndproc_ref
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = name
    user32.RegisterClassW(ctypes.byref(wc))


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _is_plausible_desktop_window(hwnd: int, expected_width: int, expected_height: int) -> bool:
    """Real Windows 11 finding from a genuine test session: repeated app
    launches (before the gui_qt/app.py single-instance fix existed) can
    leave behind tiny, disconnected "WorkerW"-classed ghost windows
    (129x36 in one observed case) that strategy 2 below would otherwise
    happily latch onto — a ghost satisfies "class is WorkerW, no
    SHELLDLL_DefView child" just as well as a real one does. Rejects any
    candidate under half the expected virtual-desktop size in either
    dimension — generous enough to accept a real full-screen WorkerW
    across DPI-scaling rounding, while trivially rejecting scraps like
    that."""
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    width, height = rect.right - rect.left, rect.bottom - rect.top
    return width >= expected_width * 0.5 and height >= expected_height * 0.5


def _find_target_workerw(expected_width: int, expected_height: int) -> int:
    """Runs the Progman handshake and returns an HWND to reparent our own
    window into, positioned behind the desktop icons.

    The classic technique (what Wallpaper Engine/Lively Wallpaper use on
    Windows 10): ask Progman to spawn an empty WorkerW via an undocumented
    message, then find it as the WorkerW sibling immediately after
    whichever top-level window hosts SHELLDLL_DefView (the icon view).

    Confirmed via a real Windows 11 report that this classic search alone
    isn't enough there — "could not locate a WorkerW window", 100%
    reproducible, not an intermittent timing race — some Windows 11
    configurations don't spawn a distinct empty WorkerW sibling for this
    at all. Falls back through two progressively less strict strategies,
    each validated against expected_width/expected_height via
    _is_plausible_desktop_window() before being trusted, printing which
    one succeeded (or every candidate it considered, if none did) to
    stderr — this can't be reproduced without a Windows machine, so the
    fallback logic here is a best-effort based on how Wallpaper Engine/
    Lively Wallpaper style tools have documented handling this exact
    Windows 11 difference, not something verified locally; the stderr
    trail is what makes a *second* real failure actually diagnosable
    instead of another guess.

    A second real finding from that same session, worth knowing before
    trusting any of this too far: even a window that passes every check
    here (correct Progman parentage, correct size, IsWindowVisible=True)
    was confirmed to still not actually appear on screen on one affected
    build — Explorer's desktop rendering has apparently moved to a
    XAML/DWM-composited layer (window classes XamlExplorerHostIslandWindow
    and WinUIDesktopWin32WindowClass were observed) that sits outside the
    classic Progman/WorkerW tree entirely on that build. No amount of
    window-discovery logic here can fix that — it isn't a bug in this
    file, it's this whole decade-old technique (used by Wallpaper Engine,
    Lively Wallpaper, RainWallpaper, etc. alike) no longer applying on
    however Windows versions/configurations have made that same move.
    """
    progman = user32.FindWindowW("Progman", None)
    # Undocumented message that tells Progman to spawn a WorkerW behind the
    # icons. No-ops harmlessly if one already exists. Sent via
    # SendMessageTimeoutW (not plain SendMessageW) so a slow/unresponsive
    # Progman can't hang this process indefinitely. The result out-param is
    # DWORD_PTR (pointer-sized), not DWORD — a plain wintypes.DWORD() buffer
    # here would be 4 bytes too small on x64 and risk a stack overwrite;
    # unused either way (only the call's own return value matters), but
    # sized correctly regardless.
    result = ctypes.c_size_t()
    user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, SMTO_NORMAL, 1000, ctypes.byref(result))

    # Strategy 1 (classic): the WorkerW sibling immediately after whichever
    # top-level window hosts SHELLDLL_DefView.
    target = ctypes.c_void_p(0)
    host_of_defview = ctypes.c_void_p(0)

    @WNDENUMPROC
    def _enum_for_sibling(hwnd, _lparam):
        def_view = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if def_view:
            host_of_defview.value = hwnd
            candidate = user32.FindWindowExW(None, hwnd, "WorkerW", None)
            if candidate:
                target.value = candidate
        return True

    user32.EnumWindows(_enum_for_sibling, 0)
    if target.value and _is_plausible_desktop_window(target.value, expected_width, expected_height):
        print(f"DEBUG: WorkerW found via classic sibling search: {target.value}", file=sys.stderr, flush=True)
        return target.value
    if target.value:
        print(
            f"DEBUG: classic sibling search found {target.value} but it's implausibly small "
            "(likely a leftover ghost window) -- rejected",
            file=sys.stderr, flush=True,
        )

    # Strategy 2: no plausible WorkerW sibling was found — look for ANY
    # top-level WorkerW window that doesn't itself host SHELLDLL_DefView and
    # is a plausible full-screen size (there may be exactly one, spawned by
    # the message above but not positioned where strategy 1 expects it; there
    # may also be several tiny disconnected ghost WorkerW windows left behind
    # by earlier launches — real Windows 11 finding — which the size check
    # exists specifically to skip past).
    fallback = ctypes.c_void_p(0)
    seen_classes: list[str] = []

    @WNDENUMPROC
    def _enum_for_empty_workerw(hwnd, _lparam):
        class_name = _class_name(hwnd)
        seen_classes.append(class_name)
        if (
            class_name == "WorkerW"
            and not user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
            and _is_plausible_desktop_window(hwnd, expected_width, expected_height)
        ):
            fallback.value = hwnd
            return False  # stop at the first plausible one
        return True

    user32.EnumWindows(_enum_for_empty_workerw, 0)
    if fallback.value:
        print(f"DEBUG: WorkerW found via empty-WorkerW fallback: {fallback.value}", file=sys.stderr, flush=True)
        return fallback.value

    # Strategy 3 (last resort): reparent directly into Progman. Not the
    # dedicated empty layer the classic technique targets, but Progman
    # always exists, and several Windows 11 configurations render content
    # parented here behind the icons correctly anyway.
    if host_of_defview.value:
        print(
            f"DEBUG: no WorkerW found at all (SHELLDLL_DefView hosted directly by "
            f"hwnd={host_of_defview.value}, class={_class_name(host_of_defview.value)}) "
            "-- falling back to Progman itself",
            file=sys.stderr, flush=True,
        )
        return progman

    print(
        f"DEBUG: SHELLDLL_DefView not found under any top-level window -- "
        f"top-level classes seen: {seen_classes}",
        file=sys.stderr, flush=True,
    )
    return 0


def main() -> None:
    _set_dpi_aware()

    monitors = _enumerate_monitors()
    if not monitors:
        print("ERROR: no monitors detected via EnumDisplayMonitors", file=sys.stderr, flush=True)
        sys.exit(1)
    bounds = _bounding_rect(monitors)
    width = bounds.right - bounds.left
    height = bounds.bottom - bounds.top

    class_name = "LiveWallWallpaperHost"
    _register_window_class(class_name)

    top_level = user32.CreateWindowExW(
        0, class_name, "LiveWall Wallpaper", WS_POPUP,
        bounds.left, bounds.top, width, height, None, None, kernel32.GetModuleHandleW(None), None,
    )
    if not top_level:
        print("ERROR: CreateWindowExW failed for the top-level window", file=sys.stderr, flush=True)
        sys.exit(1)

    workerw = _find_target_workerw(width, height)
    if not workerw:
        print("ERROR: could not locate a WorkerW window behind the desktop icons", file=sys.stderr, flush=True)
        sys.exit(1)

    user32.SetParent(top_level, workerw)
    # Strip any remaining caption/border bits and show it filling the parent.
    style = user32.GetWindowLongPtrW(top_level, GWL_STYLE)
    user32.SetWindowLongPtrW(top_level, GWL_STYLE, style | WS_VISIBLE)
    user32.ShowWindow(top_level, SW_SHOW)
    user32.MoveWindow(top_level, bounds.left, bounds.top, width, height, True)

    # One borderless child per monitor, positioned to that monitor's own
    # rect translated into top-level-window-relative coordinates (the
    # child's (0, 0) is the top-level window's top-left corner, which is
    # bounds.left/bounds.top in screen space — not necessarily (0, 0)
    # itself, since a monitor arranged above/left of the primary has
    # negative screen coordinates).
    hwnd_lines = [f"{ALL_TARGET} {top_level}"]
    for device_name, rect in monitors:
        child = user32.CreateWindowExW(
            0, class_name, "LiveWall Wallpaper Monitor", WS_CHILD | WS_VISIBLE,
            rect.left - bounds.left, rect.top - bounds.top,
            rect.right - rect.left, rect.bottom - rect.top,
            top_level, None, kernel32.GetModuleHandleW(None), None,
        )
        if not child:
            print(f"WARNING: CreateWindowExW failed for monitor {device_name}", file=sys.stderr, flush=True)
            continue
        hwnd_lines.append(f"{device_name} {child}")

    # The caller (windows_mpv.py) reads these lines to map each target
    # ("ALL", or a monitor's device name) to the hwnd mpv.exe --wid=
    # should point at. DONE is an explicit end-of-list sentinel — the
    # caller has no other way to know how many lines are coming (monitor
    # count varies), and this process never prints anything else again
    # after this point (it just pumps window messages).
    for line in hwnd_lines:
        print(line, flush=True)
    print("DONE", flush=True)

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    main()
