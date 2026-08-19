"""Only what's testable on Linux: that the module imports cleanly despite
using ctypes.WINFUNCTYPE (which doesn't exist at all on non-Windows,
unlike ctypes.windll, which exists but fails at attribute-access time —
this bit windows/monitors.py once already during development, see the
lazy-construction fix inside list_monitors())."""

from __future__ import annotations

import importlib


def test_module_imports_without_windows():
    from livewall.windows import monitors

    importlib.reload(monitors)
