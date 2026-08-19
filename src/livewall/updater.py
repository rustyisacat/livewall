"""Self-update for the Linux install: `livewall` runs directly from a git
checkout (`uv tool install --editable`), so "update" here just means
`git pull` in that checkout — no packaging, no version numbers to compare
(editable-install metadata is stale the moment pyproject.toml changes
without a reinstall, confirmed while building this: importlib.metadata
reported an old version well after the real one had moved on). git commit
SHAs are the only reliable signal of "is there something new".

Deliberately conservative: this repo is also where development happens,
so the one rule that matters more than anything else here is never
touching anything if the working tree isn't clean, and never doing
anything other than a plain fast-forward. See check_and_apply()'s early
returns.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from livewall.config import CACHE_DIR

logger = logging.getLogger(__name__)

NOTICE_FILE = CACHE_DIR / "update_notice.json"
_GIT_TIMEOUT_SECONDS = 30


def _repo_root() -> Path:
    # This module lives at src/livewall/updater.py — three parents up is
    # the repo root, same pattern gui_qt/app.py's _icon_path() already
    # uses to locate data/livewall.ico from a module's own __file__.
    return Path(__file__).resolve().parent.parent.parent


@dataclass
class UpdateResult:
    old_sha: str
    new_sha: str
    changelog: list[str] = field(default_factory=list)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        timeout=_GIT_TIMEOUT_SECONDS, check=False,
    )


def check_and_apply() -> UpdateResult | None:
    """Fetches and fast-forwards the LiveWall checkout if it's safe to do
    so, returning what changed (or None if there was nothing to do, or it
    wasn't safe to touch). Never raises — any git failure just means "no
    update this time", logged, not surfaced as an error, since this runs
    unattended (a login-time systemd service on Linux)."""
    root = _repo_root()
    if not (root / ".git").is_dir():
        logger.debug("%s isn't a git checkout — nothing to update", root)
        return None

    try:
        status = _run_git(["status", "--porcelain"], root)
        if status.returncode != 0:
            logger.warning("git status failed: %s", status.stderr.strip())
            return None
        if status.stdout.strip():
            # This is also where development happens — never pull over
            # uncommitted work, silently or otherwise.
            logger.info("Skipping update check: %s has uncommitted changes", root)
            return None

        fetch = _run_git(["fetch", "origin"], root)
        if fetch.returncode != 0:
            logger.warning("git fetch failed: %s", fetch.stderr.strip())
            return None

        old_sha = _run_git(["rev-parse", "HEAD"], root).stdout.strip()
        upstream = _run_git(["rev-parse", "@{u}"], root)
        if upstream.returncode != 0:
            logger.debug("No upstream configured for %s — nothing to update", root)
            return None
        new_sha = upstream.stdout.strip()

        if old_sha == new_sha:
            return None

        ancestor = _run_git(["merge-base", "--is-ancestor", old_sha, new_sha], root)
        if ancestor.returncode != 0:
            # Local history has diverged from upstream (e.g. an unpushed
            # local commit) — a plain fast-forward isn't possible, and
            # this never force-merges or rebases anything automatically.
            logger.warning(
                "Skipping update: %s has diverged from its upstream (not a fast-forward)", root
            )
            return None

        log = _run_git(["log", "--pretty=format:- %s", f"{old_sha}..{new_sha}"], root)
        changelog = [line for line in log.stdout.splitlines() if line.strip()]

        pull = _run_git(["pull", "--ff-only"], root)
        if pull.returncode != 0:
            # Belt-and-suspenders on top of the is-ancestor check above —
            # if this somehow still isn't fast-forwardable, bail rather
            # than let git do anything else.
            logger.warning("git pull --ff-only failed: %s", pull.stderr.strip())
            return None

        result = UpdateResult(old_sha=old_sha, new_sha=new_sha, changelog=changelog)
        _write_notice(result)
        logger.info("Updated %s: %d new commit(s)", root, len(changelog))
        return result
    except subprocess.SubprocessError as exc:
        logger.warning("Update check failed: %s", exc)
        return None


def _write_notice(result: UpdateResult) -> None:
    NOTICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    NOTICE_FILE.write_text(json.dumps({
        "old": result.old_sha, "new": result.new_sha, "changelog": result.changelog,
    }))


def read_and_clear_notice() -> dict | None:
    """What the GUI calls on startup: the pending "you were just updated"
    notice, if any — deleted once read, so it's shown exactly once."""
    try:
        data = json.loads(NOTICE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    NOTICE_FILE.unlink(missing_ok=True)
    return data
