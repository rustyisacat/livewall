"""Covers _create()'s error-message fix: real bug found via a genuine
Windows test session — install_restore_service() (and the other three
install_*() functions) used to call subprocess.run(check=True) directly,
whose CalledProcessError.__str__() is just "Command [...] returned
non-zero exit status 1", dropping schtasks.exe's own explanation on
stderr. Every caller in this project catches a broad `except Exception`
and logs str(exc), so the fix lives once, here, rather than at every
call site."""

from __future__ import annotations

import subprocess

import pytest

from livewall.windows import task_scheduler


def test_create_succeeds_quietly(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="", stderr="")
    )
    task_scheduler._create(["schtasks", "/Create", "/TN", "x"])  # must not raise


def test_create_failure_surfaces_stderr(monkeypatch):
    def fake_run(args, **kwargs):
        raise subprocess.CalledProcessError(
            1, args, output="", stderr="ERROR: The specified task name already exists.\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="already exists"):
        task_scheduler._create(["schtasks", "/Create", "/TN", "x"])


def test_create_failure_falls_back_to_stdout_when_stderr_empty(monkeypatch):
    def fake_run(args, **kwargs):
        raise subprocess.CalledProcessError(1, args, output="some stdout detail", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="some stdout detail"):
        task_scheduler._create(["schtasks", "/Create", "/TN", "x"])


def test_create_failure_includes_exit_code_even_with_no_output(monkeypatch):
    def fake_run(args, **kwargs):
        raise subprocess.CalledProcessError(5, args, output="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="exit 5"):
        task_scheduler._create(["schtasks", "/Create", "/TN", "x"])
