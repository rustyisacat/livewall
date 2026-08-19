"""Exercises updater.py's git-safety logic against disposable temp git
repos — never the real ~/Projects/livewall checkout (that's where
development happens; this module's whole point is to never touch a dirty
or diverged checkout, and testing against the real one would defeat the
purpose of that safety net)."""

from __future__ import annotations

import json
import subprocess

import pytest

from livewall import updater


def _git(args: list[str], cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
        env={"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com",
             "PATH": __import__("os").environ.get("PATH", "")},
    )


@pytest.fixture
def repo_pair(tmp_path):
    """A bare "remote" repo and a clone of it that tracks it — the clone
    is what updater.py operates on, mirroring how the real checkout
    tracks origin/master."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(["init", "--bare", "-b", "master"], remote)

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(["init", "-b", "master"], seed)
    (seed / "a.txt").write_text("1")
    _git(["add", "a.txt"], seed)
    _git(["commit", "-m", "initial"], seed)
    _git(["remote", "add", "origin", str(remote)], seed)
    _git(["push", "-u", "origin", "master"], seed)

    clone = tmp_path / "clone"
    _git(["clone", str(remote), str(clone)], tmp_path)
    _git(["config", "user.name", "Test"], clone)
    _git(["config", "user.email", "t@t.com"], clone)

    return seed, clone


@pytest.fixture
def isolated_notice(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "NOTICE_FILE", tmp_path / "update_notice.json")


@pytest.fixture(autouse=True)
def point_at_clone(repo_pair, monkeypatch):
    _seed, clone = repo_pair
    monkeypatch.setattr(updater, "_repo_root", lambda: clone)
    return clone


def _push_new_commit(seed_repo, message="a change") -> None:
    (seed_repo / "b.txt").write_text(message)
    _git(["add", "b.txt"], seed_repo)
    _git(["commit", "-m", message], seed_repo)
    _git(["push", "origin", "master"], seed_repo)


def test_up_to_date_returns_none(isolated_notice, repo_pair):
    assert updater.check_and_apply() is None


def test_pulls_new_commit_and_returns_result(isolated_notice, repo_pair):
    seed, clone = repo_pair
    _push_new_commit(seed, "a new feature")

    result = updater.check_and_apply()

    assert result is not None
    assert result.changelog == ["- a new feature"]
    assert (clone / "b.txt").exists()  # actually pulled


def test_pulls_multiple_commits_changelog_newest_first(isolated_notice, repo_pair):
    seed, clone = repo_pair
    _push_new_commit(seed, "first")
    (seed / "c.txt").write_text("2")
    _git(["add", "c.txt"], seed)
    _git(["commit", "-m", "second"], seed)
    _git(["push", "origin", "master"], seed)

    result = updater.check_and_apply()

    # git log's default order (newest first) — also the more natural read
    # for a changelog ("most recent change at the top").
    assert result.changelog == ["- second", "- first"]


def test_writes_notice_file(isolated_notice, repo_pair):
    seed, clone = repo_pair
    _push_new_commit(seed)

    updater.check_and_apply()

    notice = json.loads(updater.NOTICE_FILE.read_text())
    assert notice["changelog"] == ["- a change"]
    assert notice["old"] != notice["new"]


def test_skips_when_working_tree_dirty(isolated_notice, repo_pair):
    seed, clone = repo_pair
    _push_new_commit(seed)
    (clone / "a.txt").write_text("locally modified, uncommitted")

    result = updater.check_and_apply()

    assert result is None
    assert not updater.NOTICE_FILE.exists()
    # the actual safety guarantee: content is untouched
    assert (clone / "a.txt").read_text() == "locally modified, uncommitted"
    assert not (clone / "b.txt").exists()


def test_skips_when_diverged_never_force_merges(isolated_notice, repo_pair):
    seed, clone = repo_pair
    _push_new_commit(seed, "upstream change")

    # A local commit that was never pushed — HEAD is no longer an
    # ancestor of the (new) upstream, so this isn't a fast-forward.
    (clone / "local.txt").write_text("local work")
    _git(["add", "local.txt"], clone)
    _git(["commit", "-m", "local unpushed commit"], clone)

    result = updater.check_and_apply()

    assert result is None
    assert not updater.NOTICE_FILE.exists()
    assert (clone / "local.txt").exists()  # local commit untouched
    assert not (clone / "b.txt").exists()  # upstream change NOT force-merged in


def test_not_a_git_repo_returns_none(isolated_notice, tmp_path, monkeypatch):
    plain_dir = tmp_path / "not_a_repo"
    plain_dir.mkdir()
    monkeypatch.setattr(updater, "_repo_root", lambda: plain_dir)
    assert updater.check_and_apply() is None


def test_read_and_clear_notice_deletes_file(isolated_notice, tmp_path):
    updater.NOTICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    updater.NOTICE_FILE.write_text(json.dumps({"old": "a", "new": "b", "changelog": ["- x"]}))

    data = updater.read_and_clear_notice()

    assert data == {"old": "a", "new": "b", "changelog": ["- x"]}
    assert not updater.NOTICE_FILE.exists()
    assert updater.read_and_clear_notice() is None  # gone on second read


def test_read_and_clear_notice_missing_file_returns_none(isolated_notice):
    assert updater.read_and_clear_notice() is None
