"""Windows-only OS-integration modules (Task Scheduler, Start Menu, global
hotkey, battery reading) — the Windows counterparts to systemd.py/hypr.py/
desktop.py/power_saver.py's Linux automation. No code is shared with those
modules; only the *shape* (same function names/signatures) is mirrored so
the GUI can call whichever OS's module the same way.
"""
