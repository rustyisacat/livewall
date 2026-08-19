"""Wallpaper-selection logic for `livewall random` and the rotation
timer/task. Kept separate from cli.py so it's directly unit-testable
without going through argparse.

Two things on top of a plain random.choice() over the filtered pool:

- Repeat avoidance: excludes not just the currently-applied wallpaper
  (which cli.py always did) but a short rolling history of recently-applied
  names, so a small library or a narrow tag/favorites filter doesn't fall
  into an A-B-A-B cycle — only excluding the single current pick still
  allowed that. History is kept per *target* ("ALL" for the ordinary
  mirrored case, or a specific monitor name — see backends/mpvpaper.py's
  per-monitor support) so two monitors doing independent `random` picks
  don't suppress each other's candidates.
- Time-of-day rules: config.random_time_rules lets tags be picked
  automatically based on the hour (e.g. cozy in the morning, cyberpunk at
  night) instead of a single static tag filter.

Both degrade gracefully to the full candidate pool if the filtered-down
one would be empty — matches the project's existing pattern (see the
current-wallpaper exclusion this replaces) of never picking *nothing* just
to honor a preference.
"""

from __future__ import annotations

import json
import logging
import random as random_module
from datetime import datetime
from pathlib import Path

from livewall.config import CACHE_DIR
from livewall.database import Wallpaper
from livewall.library import Library, prefer_non_gif

logger = logging.getLogger(__name__)

HISTORY_FILE = CACHE_DIR / "random_history.json"
HISTORY_SIZE = 5
ALL_TARGET = "ALL"


def _read_all_history() -> dict[str, list[str]]:
    try:
        raw = json.loads(HISTORY_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(raw, list):
        # Pre-per-monitor format (a flat list, not keyed by target) — read
        # as an implicit ALL entry, no rewrite needed.
        return {ALL_TARGET: raw}
    return raw


def _read_history(target: str) -> list[str]:
    return _read_all_history().get(target, [])


def _record_applied(target: str, name: str) -> None:
    all_history = _read_all_history()
    history = all_history.get(target, [])
    history.append(name)
    all_history[target] = history[-HISTORY_SIZE:]
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(all_history))


def tags_for_time_rules(rules: list[dict], now: datetime | None = None) -> list[str] | None:
    """The first matching rule's tags, or None if no rule matches (or none
    are configured at all).

    Each rule is ``{"start": hour, "end": hour, "tags": [...]}`` in local
    time, half-open on ``end`` (an 06:00-12:00 rule matches hour 11 but not
    12). ``start > end`` wraps past midnight, e.g. start=22 end=6 for
    "10pm to 6am".
    """
    if not rules:
        return None
    hour = (now or datetime.now()).hour
    for rule in rules:
        start, end = rule["start"], rule["end"]
        matches = (start <= hour < end) if start <= end else (hour >= start or hour < end)
        if matches:
            return list(rule.get("tags") or [])
    return None


def pick_wallpaper(
    library: Library,
    *,
    tags: list[str] | None,
    favorites_only: bool,
    current: Path | None,
    target: str = ALL_TARGET,
) -> Wallpaper | None:
    """A random candidate matching ``tags``/``favorites_only``, preferring
    one that isn't currently applied and isn't in ``target``'s recent
    -history window, with a real animated file preferred over a same-name
    .gif duplicate. Returns None if nothing matches at all. Records the
    pick into ``target``'s history as a side effect (so the *next* call
    for that target knows to avoid it) — callers only get one pick per
    call by design, there's no separate "preview a pick" mode.
    """
    candidates = prefer_non_gif(library.search(tags=tags, favorites_only=favorites_only))
    if not candidates:
        return None

    if current is not None and len(candidates) > 1:
        candidates = [w for w in candidates if w.file_path != current] or candidates

    history = set(_read_history(target))
    avoiding_history = [w for w in candidates if w.name not in history]
    if avoiding_history:
        candidates = avoiding_history

    wallpaper = random_module.choice(candidates)
    _record_applied(target, wallpaper.name)
    return wallpaper
