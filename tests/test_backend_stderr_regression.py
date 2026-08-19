"""Regression coverage for the bug where every applied wallpaper died a few
seconds after `livewall apply`/`livewall restore` exited (including on every
boot, via the restore-on-login systemd service).

Root cause: the renderer was spawned with stderr=subprocess.PIPE and nothing
ever read that pipe past the initial startup check. The renderer keeps
writing to stderr periodically during normal playback; once the short-lived
caller (the CLI process, or the boot-time systemd service) exited, its end
of the pipe closed, and the renderer's next stderr write raised SIGPIPE and
killed it outright. See backends/mpvpaper.py's set_wallpaper() docstring
comment and the git history around the fix for the full story.

Two layers of protection here:
- test_mpvpaper_survives_caller_exit: an end-to-end reproduction of the
  actual bug, using a stub "mpvpaper" that behaves like the real one
  (spawned via a short-lived subprocess standing in for the CLI/systemd
  caller, writing to stderr periodically, checked for survival after that
  caller has fully exited).
- test_*_never_uses_unread_stderr_pipe: fast source-level guards for every
  Popen call spawning a long-running renderer/host process (including
  windows_mpv.py, which can't be functionally exercised here since it's
  Windows-only), so a future edit can't silently reintroduce
  stderr=subprocess.PIPE on one of these call sites.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from livewall.backends import mpvpaper, windows_mpv

FAKE_MPVPAPER = """\
#!/usr/bin/env python3
# Stands in for the real mpvpaper: never exits on its own, and periodically
# writes to stderr during "playback" the way the real one does.
import sys
import time

while True:
    print("frame update", file=sys.stderr, flush=True)
    time.sleep(0.05)
"""

RUNNER = """\
import sys
from pathlib import Path

from livewall.backends import mpvpaper

mpvpaper.STATE_FILE = Path(sys.argv[1])
mpvpaper.CACHE_DIR = Path(sys.argv[1]).parent

backend = mpvpaper.MpvpaperBackend()
backend.set_wallpaper(Path(sys.argv[2]))
# Exit immediately — this stands in for the short-lived CLI command or the
# boot-time systemd service, which is exactly what triggered the original bug.
"""


@pytest.mark.skipif(sys.platform == "win32", reason="mpvpaper is Linux-only")
def test_mpvpaper_survives_caller_exit(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_bin = bin_dir / "mpvpaper"
    fake_bin.write_text(FAKE_MPVPAPER)
    fake_bin.chmod(0o755)

    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"not real media, never actually read by the stub")

    runner = tmp_path / "runner.py"
    runner.write_text(RUNNER)

    state_file = tmp_path / "state.json"

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

    # This subprocess is the short-lived "caller" — it applies the
    # wallpaper and exits immediately, exactly like `livewall apply` or
    # `livewall restore` do.
    subprocess.run(
        [sys.executable, str(runner), str(state_file), str(fake_video)],
        env=env, timeout=10, check=True,
    )

    state = json.loads(state_file.read_text())
    pid = state["ALL"]["pid"]

    try:
        # The original bug killed the renderer within a couple of seconds
        # of the caller exiting (the fake's next stderr write after that
        # point). Surviving comfortably past that window is the actual
        # regression check.
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            os.kill(pid, 0)  # raises ProcessLookupError if it died
            time.sleep(0.1)
        os.kill(pid, 0)
    except ProcessLookupError:
        pytest.fail(
            "the stub mpvpaper process died after its caller exited — "
            "the stderr=subprocess.PIPE bug has regressed"
        )
    finally:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


def test_mpvpaper_spawn_never_uses_unread_stderr_pipe():
    # set_wallpaper()/set_wallpaper_for_monitor() both delegate the actual
    # subprocess spawn to _spawn() — that's the one call site that matters.
    source = inspect.getsource(mpvpaper.MpvpaperBackend._spawn)
    assert "subprocess.Popen" in source, "sanity check: _spawn should still spawn mpvpaper directly"
    assert "stderr=subprocess.PIPE" not in source

    for method in (mpvpaper.MpvpaperBackend.set_wallpaper, mpvpaper.MpvpaperBackend.set_wallpaper_for_monitor):
        assert "_spawn(" in inspect.getsource(method), f"{method.__name__} should route through _spawn()"


def test_windows_mpv_spawn_never_uses_unread_stderr_pipe():
    # set_wallpaper()/set_wallpaper_for_monitor() both delegate the actual
    # mpv spawn to _spawn_mpv() via _apply_animated_target().
    source = inspect.getsource(windows_mpv.WindowsMpvBackend._spawn_mpv)
    assert "subprocess.Popen" in source, "sanity check: _spawn_mpv should still spawn mpv directly"
    assert "stderr=subprocess.PIPE" not in source

    for method in (windows_mpv.WindowsMpvBackend.set_wallpaper, windows_mpv.WindowsMpvBackend.set_wallpaper_for_monitor):
        source = inspect.getsource(method)
        assert "_apply_animated_target(" in source, f"{method.__name__} should route through _apply_animated_target()"


def test_windows_mpv_spawn_host_never_uses_unread_stderr_pipe():
    source = inspect.getsource(windows_mpv.WindowsMpvBackend._spawn_host)
    assert "subprocess.Popen" in source, "sanity check: _spawn_host should still spawn the wallpaper host"
    assert "stderr=subprocess.PIPE" not in source
