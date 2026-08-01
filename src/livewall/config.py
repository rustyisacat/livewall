"""Filesystem layout and persistent user settings for LiveWall."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".config" / "livewall"
DATA_DIR = Path.home() / ".local" / "share" / "livewall"
CACHE_DIR = Path.home() / ".cache" / "livewall"
THUMBNAIL_DIR = CACHE_DIR / "thumbnails"

CONFIG_FILE = CONFIG_DIR / "config.json"
LIBRARY_FILE = DATA_DIR / "library.json"
LOG_FILE = CACHE_DIR / "livewall.log"

RandomInterval = Literal["off", "15m", "30m", "1h"]

RANDOM_INTERVAL_SECONDS: dict[str, int] = {
    "off": 0,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
}


def ensure_dirs() -> None:
    """Create every LiveWall directory if it doesn't already exist."""
    for path in (CONFIG_DIR, DATA_DIR, CACHE_DIR, THUMBNAIL_DIR):
        path.mkdir(parents=True, exist_ok=True)


@dataclass
class Config:
    """Persistent user settings, stored as JSON at ``config.json``.

    Rendering, theming, HW decode, and restore-on-login are all owned by
    caelestia-aw itself — this only holds settings for what LiveWall adds
    on top (scheduled random rotation, its own colour-extraction opt-out).
    """

    random_interval: RandomInterval = "off"
    random_favorites_only: bool = False
    random_tags: list[str] = field(default_factory=list)
    no_smart_colours: bool = False
    battery_saver_low: int = 15
    battery_saver_high: int = 25

    @classmethod
    def load(cls) -> "Config":
        ensure_dirs()
        if not CONFIG_FILE.exists():
            config = cls()
            config.save()
            return config

        try:
            raw: dict[str, Any] = json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read %s (%s); using defaults", CONFIG_FILE, exc)
            return cls()

        known_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in raw.items() if k in known_fields}
        return cls(**filtered)

    def save(self) -> None:
        ensure_dirs()
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2) + "\n")
        logger.debug("Saved config to %s", CONFIG_FILE)
