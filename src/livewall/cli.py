"""Command-line entry point: ``livewall <command> ...``."""

from __future__ import annotations

import argparse
import logging
import random as random_module
import sys
from pathlib import Path

from livewall import engine
from livewall.config import Config
from livewall.database import Wallpaper
from livewall.engine import ApplyError, CaelestiaNotAvailableError
from livewall.library import (
    DuplicateWallpaperError,
    ImportResult,
    Library,
    LiveWallError,
    UnsupportedFormatError,
    WallpaperNotFoundError,
    prefer_non_gif,
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


def _print_import_result(result: ImportResult) -> None:
    print(f"Added: {len(result.added)}")
    print(f"Skipped duplicates: {len(result.duplicates)}")
    print(f"Skipped unsupported: {len(result.unsupported)}")
    if result.errors:
        print(f"Errors: {len(result.errors)}")
        for path, message in result.errors:
            print(f"  {path}: {message}", file=sys.stderr)


def cmd_import(args: argparse.Namespace, lib: Library) -> int:
    result = lib.import_folder(
        Path(args.folder), recursive=not args.no_recursive, tags=_split_tags(args.tags)
    )
    _print_import_result(result)
    return 0


def cmd_sync(args: argparse.Namespace, lib: Library) -> int:
    from livewall.library import CAELESTIA_WALLPAPERS_DIR

    print(f"Syncing from {CAELESTIA_WALLPAPERS_DIR} ...")
    result = lib.sync_from_wallpapers_dir()
    _print_import_result(result)
    return 0


def cmd_refresh_thumbs(args: argparse.Namespace) -> int:
    try:
        engine.refresh_thumbnails()
    except (CaelestiaNotAvailableError, ApplyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("Refreshed caelestia-aw's video thumbnail cache.")
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


def _apply_wallpaper(wallpaper: Wallpaper, no_smart: bool) -> int:
    try:
        engine.apply(wallpaper, no_smart=no_smart)
    except CaelestiaNotAvailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, ApplyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Applied '{wallpaper.name}'")
    return 0


def cmd_apply(args: argparse.Namespace, lib: Library, config: Config) -> int:
    try:
        wallpaper = lib.get(args.name)
    except WallpaperNotFoundError:
        print(f"No such wallpaper: '{args.name}'", file=sys.stderr)
        return 1
    return _apply_wallpaper(wallpaper, args.no_smart or config.no_smart_colours)


def cmd_random(args: argparse.Namespace, lib: Library, config: Config) -> int:
    tags = _split_tags(args.tag) or config.random_tags or None
    favorites_only = args.favorites or config.random_favorites_only
    candidates = prefer_non_gif(lib.search(tags=tags, favorites_only=favorites_only))
    if not candidates:
        print("No wallpapers match.", file=sys.stderr)
        return 1

    current = engine.current_path()
    if current is not None and len(candidates) > 1:
        candidates = [w for w in candidates if w.file_path != current] or candidates

    wallpaper = random_module.choice(candidates)
    return _apply_wallpaper(wallpaper, args.no_smart or config.no_smart_colours)


def cmd_status(args: argparse.Namespace, lib: Library) -> int:
    current = engine.current_path()
    if current is None:
        print("No wallpaper is currently applied (or caelestia's state file is unreadable).")
        return 0
    print(f"Current: {current}")
    for wallpaper in lib.all():
        if wallpaper.file_path == current:
            print(f"  In library as '{wallpaper.name}' (tags: {', '.join(wallpaper.tags) or 'none'})")
            break
    else:
        print("  (not in the LiveWall library)")
    return 0


def cmd_restart_shell(args: argparse.Namespace) -> int:
    try:
        engine.restart_shell()
    except CaelestiaNotAvailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("Restarted the Caelestia shell.")
    return 0


def cmd_preview(args: argparse.Namespace, lib: Library) -> int:
    try:
        wallpaper = lib.get(args.name)
    except WallpaperNotFoundError:
        print(f"No such wallpaper: '{args.name}'", file=sys.stderr)
        return 1
    try:
        engine.preview(wallpaper.file_path, blocking=True)
    except CaelestiaNotAvailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_picker(args: argparse.Namespace) -> int:
    from livewall.picker import run as run_picker

    run_picker()
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from livewall.gui import run as run_gui

    run_gui()
    return 0


def cmd_install_hyprland(args: argparse.Namespace) -> int:
    from livewall import hypr

    if hypr.is_installed():
        if hypr.needs_repair():
            print(
                "Already installed, but with a bare 'livewall' command that Hyprland's own "
                "PATH can't find (it doesn't source your shell rc files). Fixing it to use "
                f"an absolute path:\n\n  {hypr.KEYBINDS_FILE}\n    {hypr.PICKER_EXEC_CMD}\n"
            )
            hypr.repair()
            print("Fixed. Run 'hyprctl reload' (or restart Hyprland) to apply.")
            return 0
        print("Hyprland integration is already installed.")
        return 0

    print("This will edit the following files (each gets a one-time .livewall.bak copy):")
    for path, snippet in hypr.snippets().items():
        print(f"\n  {path}\n    {snippet}")
    print()
    reply = input("Apply these changes? [y/N] ").strip().lower()
    if reply != "y":
        print("Skipped.")
        return 1

    result = hypr.install()
    for path in result.changed:
        print(f"Updated {path}")
    for path in result.already_installed:
        print(f"Already present in {path}, left unchanged")
    for path in result.missing_anchor:
        print(f"Could not find the expected anchor text in {path} — skipped", file=sys.stderr)
    if result.missing_anchor:
        return 1
    print("\nReload Hyprland config (hyprctl reload) or restart to pick up the new keybind.")
    return 0


def cmd_install_systemd(args: argparse.Namespace, config: Config) -> int:
    from livewall import systemd

    interval = args.interval or config.random_interval
    if interval == "off":
        print(
            "No random interval configured. Pass one explicitly, e.g.:\n"
            "  livewall install systemd --interval 30m",
            file=sys.stderr,
        )
        return 1

    print(f"This will install a systemd --user timer that runs 'livewall random' every {interval}:")
    print(f"\n  {systemd.SERVICE_FILE}\n{systemd.render_service()}")
    print(f"  {systemd.TIMER_FILE}\n{systemd.render_timer(interval)}")
    reply = input("Install and enable it now? [y/N] ").strip().lower()
    if reply != "y":
        print("Skipped.")
        return 1

    if interval != config.random_interval:
        config.random_interval = interval
        config.save()

    systemd.install(interval)
    print(f"Installed and started {systemd.TIMER_NAME}.")
    return 0


def cmd_uninstall_systemd(args: argparse.Namespace, config: Config) -> int:
    from livewall import systemd

    if not systemd.is_installed():
        print("No systemd random-rotation timer is installed.")
        return 0

    systemd.uninstall()
    if config.random_interval != "off":
        config.random_interval = "off"
        config.save()
    print(f"Stopped and removed {systemd.TIMER_NAME}.")
    return 0


def cmd_install_sync_timer(args: argparse.Namespace) -> int:
    from livewall import systemd

    hours = args.hours
    print(f"This will install a systemd --user timer that runs 'livewall sync' every {hours}h:")
    print(f"\n  {systemd.SYNC_SERVICE_FILE}\n{systemd.render_sync_service()}")
    print(f"  {systemd.SYNC_TIMER_FILE}\n{systemd.render_sync_timer(hours)}")
    reply = input("Install and enable it now? [y/N] ").strip().lower()
    if reply != "y":
        print("Skipped.")
        return 1

    systemd.install_sync(hours)
    print(f"Installed and started {systemd.SYNC_TIMER_NAME}.")
    return 0


def cmd_uninstall_sync_timer(args: argparse.Namespace) -> int:
    from livewall import systemd

    if not systemd.is_sync_installed():
        print("No sync timer is installed.")
        return 0

    systemd.uninstall_sync()
    print(f"Stopped and removed {systemd.SYNC_TIMER_NAME}.")
    return 0


def cmd_battery_check(args: argparse.Namespace, config: Config) -> int:
    from livewall import battery

    result = battery.check(low=config.battery_saver_low, high=config.battery_saver_high)
    print(result)
    return 0


def cmd_install_battery_saver(args: argparse.Namespace, config: Config) -> int:
    from livewall import systemd

    low = args.low if args.low is not None else config.battery_saver_low
    high = args.high if args.high is not None else config.battery_saver_high
    if low >= high:
        print(f"Error: low ({low}) must be less than high ({high})", file=sys.stderr)
        return 1

    print(
        f"This will install a systemd --user timer that checks battery level every "
        f"{args.check_seconds}s, switching to a static frame at <= {low}% and back to "
        f"video at >= {high}%:"
    )
    print(f"\n  {systemd.BATTERY_SERVICE_FILE}\n{systemd.render_battery_service()}")
    print(f"  {systemd.BATTERY_TIMER_FILE}\n{systemd.render_battery_timer(args.check_seconds)}")
    reply = input("Install and enable it now? [y/N] ").strip().lower()
    if reply != "y":
        print("Skipped.")
        return 1

    config.battery_saver_low = low
    config.battery_saver_high = high
    config.save()
    systemd.install_battery_saver(args.check_seconds)
    print(f"Installed and started {systemd.BATTERY_TIMER_NAME}.")
    return 0


def cmd_uninstall_battery_saver(args: argparse.Namespace) -> int:
    from livewall import systemd

    if not systemd.is_battery_saver_installed():
        print("No battery saver timer is installed.")
        return 0

    systemd.uninstall_battery_saver()
    print(f"Stopped and removed {systemd.BATTERY_TIMER_NAME}.")
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

    sub.add_parser("sync", help="Import everything under caelestia-aw's wallpapers directory")

    sub.add_parser("refresh-thumbs", help="Refresh caelestia-aw's own video thumbnail cache")

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

    p_apply = sub.add_parser("apply", help="Apply a wallpaper via caelestia-aw")
    p_apply.add_argument("name")
    p_apply.add_argument("--no-smart", action="store_true", help="Skip Material You recolouring")

    p_random = sub.add_parser("random", help="Apply a random wallpaper")
    p_random.add_argument("--tag")
    p_random.add_argument("--favorites", action="store_true")
    p_random.add_argument("--no-smart", action="store_true")

    sub.add_parser("status", help="Show what caelestia-aw currently has applied")
    sub.add_parser(
        "restart-shell",
        help="Restart the Caelestia shell (fixes a caelestia-aw pause-state init bug)",
    )

    p_preview = sub.add_parser("preview", help="Preview a wallpaper in a normal mpv window")
    p_preview.add_argument("name")

    sub.add_parser("picker", help="Open the quick wallpaper picker")
    sub.add_parser("gui", help="Open the LiveWall library browser")

    p_install = sub.add_parser("install", help="Install optional integrations")
    install_sub = p_install.add_subparsers(dest="install_target", required=True)
    install_sub.add_parser("hyprland", help="Add the Super+Shift+B picker keybind")
    p_install_systemd = install_sub.add_parser(
        "systemd", help="Install a timer for scheduled random rotation"
    )
    p_install_systemd.add_argument("--interval", choices=["15m", "30m", "1h"])
    p_install_sync = install_sub.add_parser(
        "sync-timer", help="Install a timer that periodically runs 'livewall sync'"
    )
    p_install_sync.add_argument("--hours", type=float, default=2.0, help="Interval in hours (default: 2)")
    p_install_battery = install_sub.add_parser(
        "battery-saver",
        help="Switch to a static frame at low battery, resume video once recovered",
    )
    p_install_battery.add_argument("--low", type=int, help="Switch to static at or below this %% (default: 15)")
    p_install_battery.add_argument("--high", type=int, help="Resume video at or above this %% (default: 25)")
    p_install_battery.add_argument(
        "--check-seconds", type=int, default=60, help="How often to check battery level (default: 60)"
    )

    p_uninstall = sub.add_parser("uninstall", help="Remove optional integrations")
    uninstall_sub = p_uninstall.add_subparsers(dest="uninstall_target", required=True)
    uninstall_sub.add_parser("systemd", help="Stop and remove the random-rotation timer")
    uninstall_sub.add_parser("sync-timer", help="Stop and remove the periodic sync timer")
    uninstall_sub.add_parser("battery-saver", help="Stop and remove the battery saver timer")

    sub.add_parser("battery-check", help="Run one battery-saver check now")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if args.command == "picker":
        return cmd_picker(args)
    if args.command == "gui":
        return cmd_gui(args)
    if args.command == "refresh-thumbs":
        return cmd_refresh_thumbs(args)
    if args.command == "restart-shell":
        return cmd_restart_shell(args)
    if args.command == "install" and args.install_target == "hyprland":
        return cmd_install_hyprland(args)
    if args.command == "install" and args.install_target == "sync-timer":
        return cmd_install_sync_timer(args)
    if args.command == "uninstall" and args.uninstall_target == "sync-timer":
        return cmd_uninstall_sync_timer(args)

    lib = Library()
    config = Config.load()

    if args.command == "install" and args.install_target == "systemd":
        return cmd_install_systemd(args, config)
    if args.command == "uninstall" and args.uninstall_target == "systemd":
        return cmd_uninstall_systemd(args, config)
    if args.command == "install" and args.install_target == "battery-saver":
        return cmd_install_battery_saver(args, config)
    if args.command == "uninstall" and args.uninstall_target == "battery-saver":
        return cmd_uninstall_battery_saver(args)
    if args.command == "battery-check":
        return cmd_battery_check(args, config)

    dispatch_with_config = {
        "apply": cmd_apply,
        "random": cmd_random,
    }
    dispatch_lib_only = {
        "list": cmd_list,
        "add": cmd_add,
        "import": cmd_import,
        "sync": cmd_sync,
        "remove": cmd_remove,
        "rename": cmd_rename,
        "favorite": cmd_favorite,
        "tag": cmd_tag,
        "info": cmd_info,
        "status": cmd_status,
        "preview": cmd_preview,
    }

    try:
        if args.command in dispatch_with_config:
            return dispatch_with_config[args.command](args, lib, config)
        if args.command in dispatch_lib_only:
            return dispatch_lib_only[args.command](args, lib)
    except LiveWallError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
