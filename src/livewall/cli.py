"""Command-line entry point: ``livewall <command> ...``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from livewall import rotation
from livewall.backends import BackendApplyError, BackendUnavailableError, WallpaperBackend, get_backend
from livewall.backends.caelestia_aw import CaelestiaAwBackend
from livewall.config import Config
from livewall.database import Wallpaper
from livewall.library import (
    DuplicateWallpaperError,
    ImportResult,
    Library,
    LiveWallError,
    UnsupportedFormatError,
    WallpaperNotFoundError,
)
from livewall.preview import MpvNotAvailableError, preview as mpv_preview
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


def cmd_refresh_thumbs(args: argparse.Namespace, backend: WallpaperBackend) -> int:
    if not backend.supports_thumbnail_refresh:
        print(f"Error: refresh-thumbs is not supported by the '{backend.name}' backend", file=sys.stderr)
        return 1
    try:
        backend.refresh_thumbnail_cache()
    except (BackendUnavailableError, BackendApplyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Refreshed the '{backend.name}' backend's thumbnail cache.")
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


def _apply_wallpaper(wallpaper: Wallpaper, no_smart: bool, backend: WallpaperBackend) -> int:
    try:
        backend.set_wallpaper(wallpaper.file_path, no_smart=no_smart)
    except BackendUnavailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, BackendApplyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Applied '{wallpaper.name}'")
    return 0


def cmd_apply(args: argparse.Namespace, lib: Library, config: Config, backend: WallpaperBackend) -> int:
    try:
        wallpaper = lib.get(args.name)
    except WallpaperNotFoundError:
        print(f"No such wallpaper: '{args.name}'", file=sys.stderr)
        return 1
    return _apply_wallpaper(wallpaper, args.no_smart or config.no_smart_colours, backend)


def cmd_random(args: argparse.Namespace, lib: Library, config: Config, backend: WallpaperBackend) -> int:
    # Precedence: an explicit --tag always wins; otherwise a matching
    # time-of-day rule; otherwise the static random_tags fallback.
    tags = (
        _split_tags(args.tag)
        or rotation.tags_for_time_rules(config.random_time_rules)
        or config.random_tags
        or None
    )
    favorites_only = args.favorites or config.random_favorites_only
    wallpaper = rotation.pick_wallpaper(
        lib, tags=tags, favorites_only=favorites_only, current=backend.current_path()
    )
    if wallpaper is None:
        print("No wallpapers match.", file=sys.stderr)
        return 1
    return _apply_wallpaper(wallpaper, args.no_smart or config.no_smart_colours, backend)


def cmd_status(args: argparse.Namespace, lib: Library, backend: WallpaperBackend) -> int:
    current = backend.current_path()
    if current is None:
        print(f"No wallpaper is currently applied (or the '{backend.name}' backend's state is unreadable).")
        return 0
    print(f"Current: {current}")
    for wallpaper in lib.all():
        if wallpaper.file_path == current:
            print(f"  In library as '{wallpaper.name}' (tags: {', '.join(wallpaper.tags) or 'none'})")
            break
    else:
        print("  (not in the LiveWall library)")
    return 0


def cmd_restart_shell(args: argparse.Namespace, backend: WallpaperBackend) -> int:
    if not backend.supports_restart:
        print(f"Error: restart-shell is not supported by the '{backend.name}' backend", file=sys.stderr)
        return 1
    try:
        backend.restart_render_service()
    except BackendUnavailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("Restarted the Caelestia shell.")
    return 0


def cmd_doctor(args: argparse.Namespace, backend: WallpaperBackend) -> int:
    from livewall import doctor

    checks = doctor.run(backend)
    print(doctor.format_report(checks))
    return 0 if all(c.ok for c in checks) else 1


def cmd_ensure_playing(args: argparse.Namespace, backend: WallpaperBackend) -> int:
    if not backend.supports_boot_fix:
        print(f"Nothing to do — the '{backend.name}' backend has no boot-fix concept.")
        return 0
    print(backend.ensure_playing())
    return 0


def cmd_restore(args: argparse.Namespace, backend: WallpaperBackend) -> int:
    if backend.restores_on_login:
        print(f"Nothing to do — the '{backend.name}' backend restores its own wallpaper on login.")
        return 0

    path = backend.last_applied_path()
    if path is None:
        print("No previously applied wallpaper to restore.")
        return 0
    if not path.exists():
        print(f"Error: last wallpaper file is missing: {path}", file=sys.stderr)
        return 1

    try:
        backend.set_wallpaper(path)
    except (BackendUnavailableError, BackendApplyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Restored wallpaper: {path}")
    return 0


def cmd_power_check(args: argparse.Namespace, config: Config, backend: WallpaperBackend) -> int:
    from livewall import power_saver

    print(power_saver.check(backend, config.battery_saver_low, config.battery_saver_high))
    return 0


def cmd_preview(args: argparse.Namespace, lib: Library) -> int:
    try:
        wallpaper = lib.get(args.name)
    except WallpaperNotFoundError:
        print(f"No such wallpaper: '{args.name}'", file=sys.stderr)
        return 1
    try:
        mpv_preview(wallpaper.file_path, blocking=True)
    except MpvNotAvailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_picker(args: argparse.Namespace) -> int:
    if sys.platform == "win32":
        from livewall.gui_qt.quick_picker import QuickPicker
        from PySide6.QtWidgets import QApplication

        app = QApplication(sys.argv)
        picker = QuickPicker()
        picker.show()
        return app.exec()

    from livewall.picker import run as run_picker

    run_picker()
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    if sys.platform == "win32":
        from livewall.gui_qt.app import run as run_gui_qt

        run_gui_qt()
        return 0

    from livewall.gui import run as run_gui

    run_gui()
    return 0


def cmd_install_desktop_entry(args: argparse.Namespace) -> int:
    from livewall import desktop

    if desktop.is_installed():
        print("Already installed. Reinstalling to pick up any changes.")

    print("This will write:")
    print(f"\n  {desktop.DESKTOP_FILE}\n{desktop.render_desktop_file()}")
    print(f"  {desktop.ICON_DEST}  (icon)")
    reply = input("Install now? [y/N] ").strip().lower()
    if reply != "y":
        print("Skipped.")
        return 1

    try:
        desktop.install()
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("Installed. LiveWall should now show up in your app launcher.")
    return 0


def cmd_uninstall_desktop_entry(args: argparse.Namespace) -> int:
    from livewall import desktop

    if not desktop.is_installed():
        print("The desktop entry is not installed.")
        return 0

    desktop.uninstall()
    print("Removed the LiveWall desktop entry.")
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


def cmd_install_battery_saver(args: argparse.Namespace, config: Config, backend: WallpaperBackend) -> int:
    low = args.low if args.low is not None else config.battery_saver_low
    high = args.high if args.high is not None else config.battery_saver_high
    if low >= high:
        print(f"Error: low ({low}) must be less than high ({high})", file=sys.stderr)
        return 1

    if isinstance(backend, CaelestiaAwBackend):
        from livewall import battery

        if not battery.is_available():
            print(f"Error: {battery.WALLPAPER_PAUSER_FILE} not found", file=sys.stderr)
            return 1

        print(
            f"This will patch {battery.WALLPAPER_PAUSER_FILE} (backed up first, to "
            f"{battery.WALLPAPER_PAUSER_FILE.with_suffix('.qml.livewall.bak')}) so caelestia-aw "
            f"pauses the live wallpaper frame at <= {low}% battery and resumes it at >= {high}%, "
            f"purely by percentage — independent of AC status."
        )
        reply = input("Apply this patch now? [y/N] ").strip().lower()
        if reply != "y":
            print("Skipped.")
            return 1

        battery.patch(low, high)
        config.battery_saver_low = low
        config.battery_saver_high = high
        config.save()

        backend.restart_render_service()
        print("Patched and restarted the Caelestia shell.")
        return 0

    if backend.supports_pause and backend.supports_resume:
        from livewall import power_saver, systemd

        if not power_saver.is_available():
            print("Error: no battery detected on this machine", file=sys.stderr)
            return 1

        print(
            f"This will install a systemd --user timer that checks battery percentage every "
            f"{systemd.POWER_SAVER_INTERVAL_SECONDS}s and pauses the live wallpaper (freezing "
            f"the current frame via the '{backend.name}' backend's own pause/resume, not a "
            f"patch) at <= {low}% battery, resuming at >= {high}%."
        )
        reply = input("Install and enable it now? [y/N] ").strip().lower()
        if reply != "y":
            print("Skipped.")
            return 1

        config.battery_saver_low = low
        config.battery_saver_high = high
        config.save()
        systemd.install_power_saver()
        print(f"Installed and enabled {systemd.POWER_SAVER_TIMER_NAME}.")
        return 0

    print(f"Error: the '{backend.name}' backend doesn't support battery-saving", file=sys.stderr)
    return 1


def cmd_uninstall_battery_saver(args: argparse.Namespace, backend: WallpaperBackend) -> int:
    from livewall import battery, systemd

    did_something = False

    if battery.is_available() and battery.is_patched():
        if battery.unpatch():
            if backend.supports_restart:
                backend.restart_render_service()
            print("Reverted the patch and restarted the Caelestia shell.")
            did_something = True
        else:
            print("No backup found to restore from.", file=sys.stderr)
            return 1

    if systemd.is_power_saver_installed():
        systemd.uninstall_power_saver()
        print(f"Stopped and removed {systemd.POWER_SAVER_TIMER_NAME}.")
        did_something = True

    if not did_something:
        print("The battery saver is not installed.")
    return 0


def cmd_install_boot_fix(args: argparse.Namespace, backend: WallpaperBackend) -> int:
    from livewall import systemd

    if not backend.supports_boot_fix:
        print(f"Error: boot-fix is not supported by the '{backend.name}' backend", file=sys.stderr)
        return 1

    print(
        "This will install a systemd --user service that runs 'livewall restart-shell' "
        f"once, {systemd.BOOT_FIX_DELAY_SECONDS}s after every login — works around a "
        "caelestia-aw bug where its pause-state can init incorrectly on the shell's first "
        "launch, silently freezing the wallpaper right after boot:"
    )
    print(f"\n  {systemd.BOOT_FIX_SERVICE_FILE}\n{systemd.render_boot_fix_service()}")
    reply = input("Install and enable it now? [y/N] ").strip().lower()
    if reply != "y":
        print("Skipped.")
        return 1

    systemd.install_boot_fix()
    print(f"Installed and enabled {systemd.BOOT_FIX_SERVICE_NAME}.")
    return 0


def cmd_uninstall_boot_fix(args: argparse.Namespace) -> int:
    from livewall import systemd

    if not systemd.is_boot_fix_installed():
        print("The boot fix is not installed.")
        return 0

    systemd.uninstall_boot_fix()
    print(f"Stopped and removed {systemd.BOOT_FIX_SERVICE_NAME}.")
    return 0


def cmd_install_restore_on_boot(args: argparse.Namespace) -> int:
    from livewall import systemd

    print(
        "This will install a systemd --user service that re-applies your last "
        f"wallpaper {systemd.RESTORE_DELAY_SECONDS}s after every login — only does "
        "anything under backends that don't already restore themselves (mpvpaper; "
        "caelestia-aw already handles this on its own, so it's a harmless no-op there):"
    )
    print(f"\n  {systemd.RESTORE_SERVICE_FILE}\n{systemd.render_restore_service()}")
    reply = input("Install and enable it now? [y/N] ").strip().lower()
    if reply != "y":
        print("Skipped.")
        return 1

    systemd.install_restore_service()
    print(f"Installed and enabled {systemd.RESTORE_SERVICE_NAME}.")
    return 0


def cmd_uninstall_restore_on_boot(args: argparse.Namespace) -> int:
    from livewall import systemd

    if not systemd.is_restore_installed():
        print("The restore-on-boot service is not installed.")
        return 0

    systemd.uninstall_restore_service()
    print(f"Stopped and removed {systemd.RESTORE_SERVICE_NAME}.")
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
    sub.add_parser("doctor", help="Health-check caelestia-aw, timers, keybind, and the library")
    sub.add_parser(
        "ensure-playing",
        help="Restart the shell only if the current video isn't actually decoding (used by boot-fix)",
    )
    sub.add_parser(
        "power-check",
        help="One hysteresis check-and-act cycle for battery-based pause/resume (used by the power-saver timer)",
    )
    sub.add_parser(
        "restore",
        help="Re-apply the last wallpaper (no-op under backends that already restore themselves; used by restore-on-boot)",
    )

    p_preview = sub.add_parser("preview", help="Preview a wallpaper in a normal mpv window")
    p_preview.add_argument("name")

    sub.add_parser("picker", help="Open the quick wallpaper picker")
    sub.add_parser("gui", help="Open the LiveWall library browser")

    p_install = sub.add_parser("install", help="Install optional integrations")
    install_sub = p_install.add_subparsers(dest="install_target", required=True)
    install_sub.add_parser("hyprland", help="Add the Super+Shift+B picker keybind")
    install_sub.add_parser("desktop-entry", help="Add LiveWall to your app launcher")
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
        help="Pause the live wallpaper frame at low battery, resume once recovered (by %%, not AC status)",
    )
    p_install_battery.add_argument("--low", type=int, help="Pause at or below this %% (default: 15)")
    p_install_battery.add_argument("--high", type=int, help="Resume at or above this %% (default: 25)")
    install_sub.add_parser(
        "boot-fix",
        help="Auto-restart the shell shortly after login to work around a caelestia-aw pause-state bug",
    )
    install_sub.add_parser(
        "restore-on-boot",
        help="Re-apply your last wallpaper on login (needed for mpvpaper; harmless no-op under caelestia-aw)",
    )

    p_uninstall = sub.add_parser("uninstall", help="Remove optional integrations")
    uninstall_sub = p_uninstall.add_subparsers(dest="uninstall_target", required=True)
    uninstall_sub.add_parser("systemd", help="Stop and remove the random-rotation timer")
    uninstall_sub.add_parser("sync-timer", help="Stop and remove the periodic sync timer")
    uninstall_sub.add_parser("battery-saver", help="Revert the battery saver (QML patch or timer, whichever is installed)")
    uninstall_sub.add_parser("boot-fix", help="Remove the boot-time auto-restart")
    uninstall_sub.add_parser("restore-on-boot", help="Remove the login wallpaper-restore service")
    uninstall_sub.add_parser("desktop-entry", help="Remove LiveWall from your app launcher")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    # picker/gui construct their own backend internally (non-fatally, in
    # picker's case — a bad config must never stop the Super+Shift+B window
    # from opening), so they're dispatched before this CLI process commits to
    # a backend of its own.
    if args.command == "picker":
        return cmd_picker(args)
    if args.command == "gui":
        return cmd_gui(args)
    if args.command == "install" and args.install_target == "hyprland":
        return cmd_install_hyprland(args)
    if args.command == "install" and args.install_target == "desktop-entry":
        return cmd_install_desktop_entry(args)
    if args.command == "uninstall" and args.uninstall_target == "desktop-entry":
        return cmd_uninstall_desktop_entry(args)
    if args.command == "install" and args.install_target == "sync-timer":
        return cmd_install_sync_timer(args)
    if args.command == "uninstall" and args.uninstall_target == "sync-timer":
        return cmd_uninstall_sync_timer(args)
    if args.command == "uninstall" and args.uninstall_target == "boot-fix":
        return cmd_uninstall_boot_fix(args)
    if args.command == "install" and args.install_target == "restore-on-boot":
        return cmd_install_restore_on_boot(args)
    if args.command == "uninstall" and args.uninstall_target == "restore-on-boot":
        return cmd_uninstall_restore_on_boot(args)

    lib = Library()
    config = Config.load()
    try:
        backend = get_backend(config.backend)
    except BackendUnavailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.command == "refresh-thumbs":
        return cmd_refresh_thumbs(args, backend)
    if args.command == "restart-shell":
        return cmd_restart_shell(args, backend)
    if args.command == "doctor":
        return cmd_doctor(args, backend)
    if args.command == "ensure-playing":
        return cmd_ensure_playing(args, backend)
    if args.command == "power-check":
        return cmd_power_check(args, config, backend)
    if args.command == "restore":
        return cmd_restore(args, backend)
    if args.command == "install" and args.install_target == "boot-fix":
        return cmd_install_boot_fix(args, backend)
    if args.command == "install" and args.install_target == "systemd":
        return cmd_install_systemd(args, config)
    if args.command == "uninstall" and args.uninstall_target == "systemd":
        return cmd_uninstall_systemd(args, config)
    if args.command == "install" and args.install_target == "battery-saver":
        return cmd_install_battery_saver(args, config, backend)
    if args.command == "uninstall" and args.uninstall_target == "battery-saver":
        return cmd_uninstall_battery_saver(args, backend)

    dispatch_with_backend = {
        "apply": lambda a, l: cmd_apply(a, l, config, backend),
        "random": lambda a, l: cmd_random(a, l, config, backend),
        "status": lambda a, l: cmd_status(a, l, backend),
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
        "preview": cmd_preview,
    }

    try:
        if args.command in dispatch_with_backend:
            return dispatch_with_backend[args.command](args, lib)
        if args.command in dispatch_lib_only:
            return dispatch_lib_only[args.command](args, lib)
    except LiveWallError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
