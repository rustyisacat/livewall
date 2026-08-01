"""High-level wallpaper library: the API the CLI and GUI actually call."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from livewall import thumbnail
from livewall.database import Database, Wallpaper
from livewall.utils import (
    human_duration,
    human_size,
    is_animated,
    is_supported,
    sha256_file,
    unique_name,
)

logger = logging.getLogger(__name__)

# Same directory caelestia-aw itself scans for wallpapers (utils/paths.py:wallpapers_dir).
CAELESTIA_WALLPAPERS_DIR = Path(
    os.getenv("CAELESTIA_WALLPAPERS_DIR", Path.home() / "Pictures" / "Wallpapers")
)


class LiveWallError(Exception):
    """Base class for library errors."""


class UnsupportedFormatError(LiveWallError):
    pass


class DuplicateWallpaperError(LiveWallError):
    def __init__(self, existing: Wallpaper) -> None:
        self.existing = existing
        super().__init__(f"Already in library as '{existing.name}'")


class WallpaperNotFoundError(LiveWallError):
    pass


@dataclass
class ImportResult:
    added: list[Wallpaper] = field(default_factory=list)
    duplicates: list[Path] = field(default_factory=list)
    unsupported: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)


@dataclass
class WallpaperInfo:
    """Wallpaper record plus display-ready technical metadata, for the GUI/CLI."""

    wallpaper: Wallpaper
    metadata: thumbnail.Metadata
    thumbnail_path: Path | None
    size_bytes: int

    @property
    def size_human(self) -> str:
        return human_size(self.size_bytes)

    @property
    def duration_human(self) -> str:
        return human_duration(self.metadata.duration)


class Library:
    """Owns the wallpaper database and coordinates hashing/probing/thumbnailing."""

    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    def all(self) -> list[Wallpaper]:
        return sorted(self.db.all(), key=lambda w: w.name.lower())

    def get(self, name: str) -> Wallpaper:
        wallpaper = self.db.get(name)
        if wallpaper is None:
            raise WallpaperNotFoundError(name)
        return wallpaper

    def info(self, name: str) -> WallpaperInfo:
        wallpaper = self.get(name)
        return self._info_for(wallpaper)

    def _info_for(self, wallpaper: Wallpaper) -> WallpaperInfo:
        meta = thumbnail.probe(wallpaper.file_path)
        thumb = thumbnail.thumbnail_path(wallpaper.hash)
        size = wallpaper.file_path.stat().st_size if wallpaper.file_path.exists() else 0
        return WallpaperInfo(
            wallpaper=wallpaper,
            metadata=meta,
            thumbnail_path=thumb if thumb.exists() else None,
            size_bytes=size,
        )

    def add(self, path: Path, tags: list[str] | None = None, name: str | None = None) -> Wallpaper:
        """Add a single file to the library. Raises on unsupported format or duplicate."""
        path = path.expanduser().resolve()
        if not path.is_file():
            raise LiveWallError(f"Not a file: {path}")
        if not is_supported(path):
            raise UnsupportedFormatError(f"Unsupported format: {path.suffix}")

        existing_by_path = self.db.find_by_path(path)
        if existing_by_path:
            raise DuplicateWallpaperError(existing_by_path)

        file_hash = sha256_file(path)
        existing_by_hash = self.db.find_by_hash(file_hash)
        if existing_by_hash:
            raise DuplicateWallpaperError(existing_by_hash)

        display_name = name or path.stem
        display_name = unique_name(display_name, {w.name for w in self.db.all()})

        wallpaper = Wallpaper(
            name=display_name,
            path=str(path),
            kind="animated" if is_animated(path) else "image",
            hash=file_hash,
            tags=sorted(set(tags or [])),
        )

        meta = thumbnail.probe(path)
        thumbnail.generate(path, file_hash, meta)

        self.db.add(wallpaper)
        self.db.save()
        logger.info("Added wallpaper '%s' (%s)", wallpaper.name, path)
        return wallpaper

    def import_folder(
        self, folder: Path, recursive: bool = True, tags: list[str] | None = None
    ) -> ImportResult:
        folder = folder.expanduser().resolve()
        result = ImportResult()
        pattern = "**/*" if recursive else "*"
        for candidate in sorted(folder.glob(pattern)):
            if not candidate.is_file():
                continue
            if not is_supported(candidate):
                result.unsupported.append(candidate)
                continue
            try:
                wallpaper = self.add(candidate, tags=tags)
                result.added.append(wallpaper)
            except DuplicateWallpaperError:
                result.duplicates.append(candidate)
            except LiveWallError as exc:
                result.errors.append((candidate, str(exc)))
        return result

    def sync_from_wallpapers_dir(self) -> ImportResult:
        """Import everything already under caelestia-aw's own wallpapers directory."""
        return self.import_folder(CAELESTIA_WALLPAPERS_DIR, recursive=True)

    def remove(self, name: str) -> Wallpaper:
        wallpaper = self.db.remove(name)
        if wallpaper is None:
            raise WallpaperNotFoundError(name)
        thumbnail.remove_thumbnail(wallpaper.hash)
        self.db.save()
        logger.info("Removed wallpaper '%s'", name)
        return wallpaper

    def rename(self, old_name: str, new_name: str) -> Wallpaper:
        if self.db.get(old_name) is None:
            raise WallpaperNotFoundError(old_name)
        if new_name in {w.name for w in self.db.all()} and new_name != old_name:
            raise LiveWallError(f"'{new_name}' is already in use")
        wallpaper = self.db.rename(old_name, new_name)
        assert wallpaper is not None
        self.db.save()
        return wallpaper

    def set_favorite(self, name: str, favorite: bool) -> Wallpaper:
        wallpaper = self.get(name)
        wallpaper.favorite = favorite
        self.db.save()
        return wallpaper

    def toggle_favorite(self, name: str) -> Wallpaper:
        wallpaper = self.get(name)
        return self.set_favorite(name, not wallpaper.favorite)

    def set_tags(self, name: str, tags: list[str]) -> Wallpaper:
        wallpaper = self.get(name)
        wallpaper.tags = sorted(set(tags))
        self.db.save()
        return wallpaper

    def search(
        self,
        query: str = "",
        tags: list[str] | None = None,
        kind: str | None = None,
        favorites_only: bool = False,
    ) -> list[Wallpaper]:
        results = self.all()
        if query:
            q = query.lower()
            results = [w for w in results if q in w.name.lower() or any(q in t.lower() for t in w.tags)]
        if tags:
            wanted = set(tags)
            results = [w for w in results if wanted.issubset(set(w.tags))]
        if kind:
            results = [w for w in results if w.kind == kind]
        if favorites_only:
            results = [w for w in results if w.favorite]
        return results
