"""Covers _fix_console_encoding(): real bug found via a genuine Windows
test session — `doctor` prints unicode checkmarks (see doctor.py's ✓/✗),
and on a console stuck on the legacy cp1252 codepage (Windows' default,
not UTF-8), writing them raised UnicodeEncodeError, which a frozen build
surfaced as an "Unhandled exception in script" crash for what should have
just been a status report."""

from __future__ import annotations

from livewall import cli


class _StreamWithReconfigure:
    def __init__(self):
        self.calls = []

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)


class _StreamWithoutReconfigure:
    pass


class _StreamThatRaises:
    def reconfigure(self, **kwargs):
        raise OSError("not a real console")


def test_reconfigures_stdout_and_stderr_to_utf8(monkeypatch):
    out, err = _StreamWithReconfigure(), _StreamWithReconfigure()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", err)

    cli._fix_console_encoding()

    assert out.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert err.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_skips_streams_without_reconfigure(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdout", _StreamWithoutReconfigure())
    monkeypatch.setattr(cli.sys, "stderr", _StreamWithoutReconfigure())

    cli._fix_console_encoding()  # must not raise


def test_swallows_reconfigure_failure(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdout", _StreamThatRaises())
    monkeypatch.setattr(cli.sys, "stderr", _StreamThatRaises())

    cli._fix_console_encoding()  # must not raise
