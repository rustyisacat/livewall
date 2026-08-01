"""Command-line entry point: ``livewall <command> ...``."""

from __future__ import annotations

import argparse
import logging
import random as random_module
import sys
from pathlib import Path

from livewall import engine as engine_mod
from livewall.config import Config
from livewall.database import Wallpaper
from livewall.engine import MpvpaperNotFoundError, WallpaperEngine
from livewall.library import (
    DuplicateWallpaperError,
    ImportResult,
    Library,
    LiveWallError,
    UnsupportedFormatError,
    WallpaperNotFoundError,
)
from livewall.utils import setup_logging

logger = logging.getLogger("livewall.cli")


def _split_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def _print_table(rows: list[list[str]], headers: list[str]) -> None:
    if not rows:
        print("(no wallpapers)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for row in rows:
        print(fmt.format(*row))


def cmd_list(args: argparse.Namespace, lib: Library) -> int:
    results = lib.search(
        query=args.query or "",
        tags=_split_tags(args.tag) or None,
        kind=args.type,
        favorites_only=args.favorites,
    )
    rows = [
        [w.name, w.kind, "*" if w.favorite else "", ", ".join(w.tags)]
        for w in results
    ]
    _print_table(rows, ["NAME", "TYPE", "FAV", "TAGS"])
    return 0


def cmd_add(args: argparse.Namespace, lib: Library) -> int:
    try:
        wallpaper = lib.add(Path(args.file), tags=_split_tags(args.tags), name=args.name)
    except DuplicateWallpaperError as exc:
        print(f"Already in library as '{exc.existing.name}'", file=sys.stderr)
        return 1
    except (UnsupportedFormatError, LiveWallError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Added '{wallpaper.name}' ({wallpaper.kind})")
    return 0


def cmd_import(args: argparse.Namespace, lib: Library) -> int:
    result: ImportResult = lib.import_folder(
        Path(args.folder), recursive=not args.no_recursive, tags=_split_tags(args.tags)
    )
    print(f"Added: {len(result.added)}")
    print(f"Skipped duplicates: {len(result.duplicates)}")
    print(f"Skipped unsupported: {len(result.unsupported)}")
    if result.errors:
        print(f"Errors: {len(result.errors)}")
        for path, message in result.errors:
            print(f"  {path}: {message}", file=sys.stderr)
    return 0


def cmd_remove(args: argparse.Namespace, lib: Library) -> int:
    try:
        lib.remove(args.name)
    except WallpaperNotFoundError:
        print(f"No such wallpaper: '{args.name}'", file=sys.stderr)
        return 1
    print(f"Removed '{args.name}'")
    return 0


def cmd_rename(args: argparse.Namespace, lib: Library) -> int:
    try:
        lib.rename(args.old_name, args.new_name)
    except LiveWallError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Renamed '{args.old_name}' -> '{args.new_name}'")
    return 0


def cmd_favorite(args: argparse.Namespace, lib: Library) -> int:
    try:
        wallpaper = lib.set_favorite(args.name, not args.unset)
    except WallpaperNotFoundError:
        print(f"No such wallpaper: '{args.name}'", file=sys.stderr)
        return 1
    print(f"{'Favorited' if wallpaper.favorite else 'Unfavorited'} '{wallpaper.name}'")
    return 0


def cmd_tag(args: argparse.Namespace, lib: Library) -> int:
    try:
        wallpaper = lib.set_tags(args.name, _split_tags(args.tags))
    except WallpaperNotFoundError:
        print(f"No such wallpaper: '{args.name}'", file=sys.stderr)
        return 1
    print(f"Tags for '{wallpaper.name}': {', '.join(wallpaper.tags) or '(none)'}")
    return 0


def cmd_info(args: argparse.Namespace, lib: Library) -> int:
    try:
        info = lib.info(args.name)
    except WallpaperNotFoundError:
        print(f"No such wallpaper: '{args.name}'", file=sys.stderr)
        return 1
    w = info.wallpaper
    print(f"Name:       {w.name}")
    print(f"Path:       {w.path}")
    print(f"Type:       {w.kind}")
    print(f"Tags:       {', '.join(w.tags) or '(none)'}")
    print(f"Favorite:   {'yes' if w.favorite else 'no'}")
    print(f"Resolution: {info.metadata.resolution}")
    print(f"Aspect:     {info.metadata.aspect_ratio or 'unknown'}")
    print(f"Duration:   {info.duration_human}")
    print(f"Animated:   {'yes' if info.metadata.animated else 'no'}")
    print(f"Size:       {info.size_human}")
    return 0


def _apply_wallpaper(wallpaper: Wallpaper, engine: WallpaperEngine, monitor: str | None) -> int:
    try:
        engine.apply(wallpaper, monitor=monitor)
    except MpvpaperNotFoundError:
        print(
            "mpvpaper is not installed. Run 'livewall install mpvpaper' first.",
            file=sys.stderr,
        )
        return 1
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Applied '{wallpaper.name}'")
    return 0


def cmd_apply(args: argparse.Namespace, lib: Library, engine: WallpaperEngine) -> int:
    try:
        wallpaper = lib.get(args.name)
    except WallpaperNotFoundError:
        print(f"No such wallpaper: '{args.name}'", file=sys.stderr)
        return 1
    return _apply_wallpaper(wallpaper, engine, args.monitor)


def cmd_random(args: argparse.Namespace, lib: Library, engine: WallpaperEngine) -> int:
    candidates = lib.search(
        tags=_split_tags(args.tag) or None,
        favorites_only=args.favorites,
    )
    candidates = [w for w in candidates if w.name != engine.state.current_wallpaper] or candidates
    if not candidates:
        print("No wallpapers in library.", file=sys.stderr)
        return 1
    wallpaper = random_module.choice(candidates)
    return _apply_wallpaper(wallpaper, engine, args.monitor)


def cmd_stop(args: argparse.Namespace, lib: Library, engine: WallpaperEngine) -> int:
    engine.stop()
    print("Stopped.")
    return 0


def cmd_status(args: argparse.Namespace, lib: Library, engine: WallpaperEngine) -> int:
    if not engine.state.processes:
        print("No wallpaper is currently applied by LiveWall.")
        return 0
    print(f"Current: {engine.state.current_wallpaper}")
    for monitor, pid in engine.state.processes.items():
        alive = "running" if engine.is_running(monitor) else "dead"
        print(f"  {monitor}: pid {pid} ({alive})")
    return 0


def cmd_preview(args: argparse.Namespace, lib: Library, engine: WallpaperEngine) -> int:
    try:
        wallpaper = lib.get(args.name)
    except WallpaperNotFoundError:
        print(f"No such wallpaper: '{args.name}'", file=sys.stderr)
        return 1
    try:
        engine.preview(wallpaper.file_path, blocking=True)
    except MpvpaperNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_install_mpvpaper(args: argparse.Namespace) -> int:
    if engine_mod.is_installed():
        print("mpvpaper is already installed.")
        return 0
    cmd = " ".join(engine_mod.install_command())
    reply = input(f"mpvpaper is not installed. Run '{cmd}' now? [y/N] ").strip().lower()
    if reply != "y":
        print("Skipped.")
        return 1
    return 0 if engine_mod.install() else 1


def cmd_picker(args: argparse.Namespace) -> int:
    from livewall.picker import run as run_picker

    run_picker()
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from livewall.gui import run as run_gui

    run_gui()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="livewall", description="Live wallpaper manager")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List wallpapers in the library")
    p_list.add_argument("--query", help="Filter by name/tag substring")
    p_list.add_argument("--tag", help="Comma-separated tags that must all match")
    p_list.add_argument("--type", choices=["image", "animated"])
    p_list.add_argument("--favorites", action="store_true")

    p_add = sub.add_parser("add", help="Add a wallpaper file to the library")
    p_add.add_argument("file")
    p_add.add_argument("--tags", help="Comma-separated tags")
    p_add.add_argument("--name", help="Override the display name")

    p_import = sub.add_parser("import", help="Import every supported file in a folder")
    p_import.add_argument("folder")
    p_import.add_argument("--no-recursive", action="store_true")
    p_import.add_argument("--tags", help="Comma-separated tags applied to every import")

    p_remove = sub.add_parser("remove", help="Remove a wallpaper from the library")
    p_remove.add_argument("name")

    p_rename = sub.add_parser("rename", help="Rename a wallpaper")
    p_rename.add_argument("old_name")
    p_rename.add_argument("new_name")

    p_fav = sub.add_parser("favorite", help="Star/unstar a wallpaper")
    p_fav.add_argument("name")
    p_fav.add_argument("--unset", action="store_true")

    p_tag = sub.add_parser("tag", help="Replace a wallpaper's tags")
    p_tag.add_argument("name")
    p_tag.add_argument("tags", help="Comma-separated tags (empty string clears)")

    p_info = sub.add_parser("info", help="Show details for a wallpaper")
    p_info.add_argument("name")

    p_apply = sub.add_parser("apply", help="Apply a wallpaper as the live background")
    p_apply.add_argument("name")
    p_apply.add_argument("--monitor")

    p_random = sub.add_parser("random", help="Apply a random wallpaper")
    p_random.add_argument("--tag")
    p_random.add_argument("--favorites", action="store_true")
    p_random.add_argument("--monitor")

    sub.add_parser("stop", help="Stop the running wallpaper")
    sub.add_parser("status", help="Show what's currently applied")

    p_preview = sub.add_parser("preview", help="Preview a wallpaper in a normal mpv window")
    p_preview.add_argument("name")

    sub.add_parser("picker", help="Open the quick wallpaper picker")
    sub.add_parser("gui", help="Open the LiveWall library browser")

    p_install = sub.add_parser("install", help="Install optional integrations")
    install_sub = p_install.add_subparsers(dest="install_target", required=True)
    install_sub.add_parser("mpvpaper", help="Install mpvpaper via pacman")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if args.command == "picker":
        return cmd_picker(args)
    if args.command == "gui":
        return cmd_gui(args)
    if args.command == "install" and args.install_target == "mpvpaper":
        return cmd_install_mpvpaper(args)

    lib = Library()
    config = Config.load()
    engine = WallpaperEngine(config)

    dispatch_with_engine = {
        "apply": cmd_apply,
        "random": cmd_random,
        "stop": cmd_stop,
        "status": cmd_status,
        "preview": cmd_preview,
    }
    dispatch_lib_only = {
        "list": cmd_list,
        "add": cmd_add,
        "import": cmd_import,
        "remove": cmd_remove,
        "rename": cmd_rename,
        "favorite": cmd_favorite,
        "tag": cmd_tag,
        "info": cmd_info,
    }

    try:
        if args.command in dispatch_with_engine:
            return dispatch_with_engine[args.command](args, lib, engine)
        if args.command in dispatch_lib_only:
            return dispatch_lib_only[args.command](args, lib)
    except LiveWallError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
