"""Preview a wallpaper in a plain mpv window — unrelated to any backend."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

MPV_BIN = "mpv"


class MpvNotAvailableError(RuntimeError):
    """Raised when the ``mpv`` CLI isn't on PATH."""


def preview(path: Path, blocking: bool = True) -> subprocess.Popen | None:
    if shutil.which(MPV_BIN) is None:
        raise MpvNotAvailableError("mpv is not installed")
    cmd = [MPV_BIN, "--loop-file=inf", str(path)]
    if blocking:
        subprocess.run(cmd)
        return None
    return subprocess.Popen(cmd, start_new_session=True)
