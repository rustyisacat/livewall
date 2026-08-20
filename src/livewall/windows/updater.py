"""Self-update for the packaged Windows build. Unlike updater.py's
git-pull approach for the Linux dev checkout, a frozen .exe can't `git
pull` itself — this instead checks GitHub's latest release, and if it's
newer than the running build, downloads the release zip, stages it, and
hands off to a small batch-file helper that waits for this process to
exit, swaps the install directory, and relaunches.

Checked from gui_qt/app.py::run() at startup, alongside the existing
single-instance/tray setup — there's deliberately no separate scheduled
task for this (see task_scheduler.py's module docstring: a second
login-time entry would race with the existing autostart one, both
touching the same install directory around the same time).

Every public function here is designed to never raise: an update check
or apply failing must never be why LiveWall fails to open normally. Any
failure logs and returns None/False, and the caller falls through to a
normal launch.

NOTE: none of this has been run against a real Windows install (no
Windows machine was available during development) — the download →
stage → batch-helper → directory-swap → relaunch chain is meaningfully
higher-risk than the rest of this session's unverified Windows work,
which is exactly why the old install is renamed to a `.backup` directory
rather than deleted: if a build shipped here turns out to be broken, the
previous working install is still sitting right next to it for the user
to fall back to by hand. There's deliberately no automatic
rollback-on-launch-failure — detecting "did the relaunch actually
succeed" from an external batch script is real added complexity not
worth attempting blind on top of everything else here.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_GITHUB_API_URL = "https://api.github.com/repos/rustyisacat/livewall/releases/latest"
_RELEASE_ASSET_NAME = "LiveWall-windows.zip"
_HTTP_TIMEOUT_SECONDS = 15

# Fallback when importlib.metadata can't resolve a version inside a frozen
# build (PyInstaller doesn't bundle package metadata unless explicitly
# told to) — kept in sync with pyproject.toml's version by hand at each
# release, same as every other "no build-time templating step exists for
# this yet" spot in the Windows side of this project.
_FALLBACK_VERSION = "1.3.1"


def _current_version() -> str:
    try:
        from importlib.metadata import version

        return version("livewall")
    except Exception:
        return _FALLBACK_VERSION


@dataclass
class UpdateInfo:
    tag: str
    download_url: str
    size: int
    changelog: str


def check_for_update() -> UpdateInfo | None:
    request = urllib.request.Request(
        _GITHUB_API_URL, headers={"User-Agent": "LiveWall-Updater", "Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.warning("Update check failed: %s", exc)
        return None

    tag = data.get("tag_name")
    if not tag:
        return None
    if tag == f"v{_current_version()}":
        return None  # already on the latest release

    asset = next(
        (a for a in data.get("assets", []) if a.get("name") == _RELEASE_ASSET_NAME), None
    )
    if asset is None:
        logger.warning("Release %s has no %s asset — nothing to download", tag, _RELEASE_ASSET_NAME)
        return None

    return UpdateInfo(
        tag=tag,
        download_url=asset["browser_download_url"],
        size=asset.get("size", 0),
        changelog=data.get("body") or "",
    )


def _update_root() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    return Path(local_app_data) / "LiveWall" / "update"


def _install_dir() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None  # not a packaged build — nothing to self-update
    return Path(sys.executable).resolve().parent


def download_and_stage(info: UpdateInfo) -> Path | None:
    """Downloads and extracts the release zip, returning the staging
    directory on success or None on any failure (network error, a
    truncated download, a corrupt zip — never raises)."""
    root = _update_root()
    if root is None:
        return None

    try:
        root.mkdir(parents=True, exist_ok=True)
        zip_path = root / _RELEASE_ASSET_NAME
        request = urllib.request.Request(info.download_url, headers={"User-Agent": "LiveWall-Updater"})
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS * 4) as response:
            zip_path.write_bytes(response.read())

        if info.size and zip_path.stat().st_size != info.size:
            logger.warning(
                "Downloaded %s is %d bytes, expected %d — truncated download, aborting",
                zip_path, zip_path.stat().st_size, info.size,
            )
            return None

        staging_dir = root / "staging"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True)

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staging_dir)

        return staging_dir
    except (urllib.error.URLError, TimeoutError, OSError, zipfile.BadZipFile) as exc:
        logger.warning("Failed to download/stage update %s: %s", info.tag, exc)
        return None


_APPLY_BAT_TEMPLATE = """@echo off
setlocal enabledelayedexpansion
set "PID={pid}"
set "INSTALL_DIR={install_dir}"
set "STAGING_DIR={staging_dir}"
set "BACKUP_DIR={backup_dir}"
set "ZIP_PATH={zip_path}"

set COUNT=0
:waitloop
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 goto proceed
set /a COUNT+=1
if %COUNT% GEQ 15 goto proceed
timeout /t 1 /nobreak >nul
goto waitloop

:proceed
if exist "%BACKUP_DIR%" rmdir /s /q "%BACKUP_DIR%"
move "%INSTALL_DIR%" "%BACKUP_DIR%" >nul
mkdir "%INSTALL_DIR%"
xcopy "%STAGING_DIR%\\*" "%INSTALL_DIR%\\" /E /I /H /Y >nul
start "" "%INSTALL_DIR%\\LiveWall.exe" {relaunch_args}
rmdir /s /q "%STAGING_DIR%" 2>nul
del "%ZIP_PATH%" 2>nul
(goto) 2>nul & del "%~f0"
"""


def apply_and_relaunch(staging_dir: Path, info: UpdateInfo, *, tray: bool = False) -> bool:
    """Writes the batch helper, writes the "what's new" notice for the
    next launch to read, and spawns the helper detached. The caller is
    expected to quit the Qt app right after this returns — the helper is
    waiting for this process's PID to disappear."""
    install_dir = _install_dir()
    root = _update_root()
    if install_dir is None or root is None:
        return False

    try:
        from livewall.updater import NOTICE_FILE

        NOTICE_FILE.parent.mkdir(parents=True, exist_ok=True)
        NOTICE_FILE.write_text(json.dumps({
            "old": f"v{_current_version()}", "new": info.tag,
            "changelog": [line for line in info.changelog.splitlines() if line.strip()],
        }))

        bat_path = root / "apply.bat"
        bat_path.write_text(_APPLY_BAT_TEMPLATE.format(
            pid=os.getpid(),
            install_dir=install_dir,
            staging_dir=staging_dir,
            backup_dir=install_dir.parent / f"{install_dir.name}.backup",
            zip_path=root / _RELEASE_ASSET_NAME,
            relaunch_args="--tray" if tray else "",
        ))

        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            ["cmd", "/c", str(bat_path)],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        logger.info("Staged update %s, handed off to %s for relaunch", info.tag, bat_path)
        return True
    except OSError as exc:
        logger.warning("Failed to apply staged update %s: %s", info.tag, exc)
        return False
