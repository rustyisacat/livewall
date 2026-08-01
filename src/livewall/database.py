"""JSON-backed storage for wallpaper metadata.

This module is deliberately dumb: it knows how to load, save, and
CRUD :class:`Wallpaper` records on disk. Anything involving files on
disk (hashing, thumbnailing, duplicate policy) belongs in
:mod:`livewall.library`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from livewall.config import LIBRARY_FILE, ensure_dirs

logger = logging.getLogger(__name__)

WallpaperKind = Literal["image", "animated"]


@dataclass
class Wallpaper:
    """A single library entry."""

    name: str
    path: str
    kind: WallpaperKind
    hash: str
    tags: list[str] = field(default_factory=list)
    favorite: bool = False
    added_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def file_path(self) -> Path:
        return Path(self.path)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Wallpaper":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


class Database:
    """Load/save/CRUD for the wallpaper library JSON file."""

    def __init__(self, path: Path = LIBRARY_FILE) -> None:
        self.path = path
        self._wallpapers: dict[str, Wallpaper] = {}
        self.load()

    def load(self) -> None:
        ensure_dirs()
        self._wallpapers = {}
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read library file %s: %s", self.path, exc)
            return
        for entry in raw.get("wallpapers", []):
            wallpaper = Wallpaper.from_dict(entry)
            self._wallpapers[wallpaper.name] = wallpaper

    def save(self) -> None:
        ensure_dirs()
        payload = {"wallpapers": [w.to_dict() for w in self._wallpapers.values()]}
        tmp_path = self.path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2) + "\n")
        tmp_path.replace(self.path)
        logger.debug("Saved %d wallpapers to %s", len(self._wallpapers), self.path)

    def all(self) -> list[Wallpaper]:
        return list(self._wallpapers.values())

    def get(self, name: str) -> Wallpaper | None:
        return self._wallpapers.get(name)

    def add(self, wallpaper: Wallpaper) -> None:
        self._wallpapers[wallpaper.name] = wallpaper

    def remove(self, name: str) -> Wallpaper | None:
        return self._wallpapers.pop(name, None)

    def rename(self, old_name: str, new_name: str) -> Wallpaper | None:
        wallpaper = self._wallpapers.pop(old_name, None)
        if wallpaper is None:
            return None
        wallpaper.name = new_name
        self._wallpapers[new_name] = wallpaper
        return wallpaper

    def find_by_hash(self, file_hash: str) -> Wallpaper | None:
        for wallpaper in self._wallpapers.values():
            if wallpaper.hash == file_hash:
                return wallpaper
        return None

    def find_by_path(self, path: Path) -> Wallpaper | None:
        resolved = str(path.resolve())
        for wallpaper in self._wallpapers.values():
            if wallpaper.path == resolved:
                return wallpaper
        return None
