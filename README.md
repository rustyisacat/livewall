# LiveWall

A wallpaper *library manager* for Hyprland + [Caelestia](https://github.com/caelestia-dots/caelestia),
built on top of [caelestia-aw](https://github.com/AdiAmbassador/caelestia-aw).

caelestia-aw already renders animated (mp4/webm/mkv/gif) and static wallpapers
natively inside the Caelestia shell — its own picker, thumbnailing, Material
You theming, battery/fullscreen pausing, and hardware decoding. LiveWall
doesn't duplicate any of that. It adds the things caelestia-aw has no concept
of: **tags, favorites, search, duplicate detection, a proper library
browser, and scheduled random rotation** — then applies wallpapers by calling
`caelestia wallpaper -f` under the hood.

## Requirements

- Arch Linux, Hyprland, Wayland
- [caelestia-aw](https://github.com/AdiAmbassador/caelestia-aw) installed and patched in
- `ffmpeg` / `ffprobe` (thumbnailing and metadata probing)
- `mpv` (optional, only for the standalone "preview in a window" feature)
- `zenity` (optional, only for the GUI's native "Add File" dialog)
- Python 3.12+, [`uv`](https://docs.astral.sh/uv/)

## Install

```bash
git clone <this repo> ~/Projects/livewall
cd ~/Projects/livewall
uv tool install --editable .
```

This puts `livewall`, `livewall-gui`, and `livewall-picker` on your `PATH`
(via `~/.local/bin`). Being editable, pulling new commits takes effect
immediately — no reinstall needed.

Pull in whatever's already sitting in Caelestia's own wallpapers folder
(`~/Pictures/Wallpapers`, recursive) as a starting library:

```bash
livewall sync
```

## Usage

### CLI

```
livewall list [--query Q] [--tag t1,t2] [--type image|animated] [--favorites]
livewall add FILE [--tags t1,t2] [--name NAME]
livewall import FOLDER [--no-recursive] [--tags t1,t2]
livewall sync                        # import everything under caelestia-aw's wallpapers dir
livewall remove NAME
livewall rename OLD NEW
livewall favorite NAME [--unset]
livewall tag NAME "tag1, tag2"
livewall info NAME                   # resolution, duration, size, aspect ratio, ...
livewall apply NAME [--no-smart]     # --no-smart skips Material You recolouring
livewall random [--tag t] [--favorites] [--no-smart]
livewall status                      # what caelestia-aw currently has applied
livewall preview NAME                # opens in a plain mpv window, not the desktop
livewall refresh-thumbs              # regenerate caelestia-aw's own video thumbnail cache
livewall restart-shell               # restart the Caelestia shell (see "Known caelestia-aw issues")
livewall doctor                      # health-check caelestia-aw, timers, keybind, desktop entry, library
livewall picker                      # the quick picker (see below)
livewall gui                         # the full library browser (see below)
livewall install hyprland            # add the Super+Shift+B picker keybind (opt-in, confirms first)
livewall install systemd --interval 15m|30m|1h   # scheduled random rotation (opt-in, confirms first)
livewall uninstall systemd           # stop and remove the random-rotation timer
livewall install sync-timer [--hours N]   # periodic 'livewall sync' (default 2h, opt-in, confirms first)
livewall uninstall sync-timer        # stop and remove the periodic sync timer
livewall install battery-saver [--low 15] [--high 25]   # opt-in, confirms first, see below
livewall uninstall battery-saver     # revert the battery saver patch
livewall install boot-fix            # checks ~5s after login, restarts only if actually needed, see below
livewall uninstall boot-fix          # remove the boot-time check
livewall ensure-playing              # run that same check right now (what boot-fix calls)
livewall install desktop-entry       # add LiveWall to your app launcher (opt-in, confirms first)
livewall uninstall desktop-entry     # remove it from the launcher
```

### GUI (`livewall gui`)

A Textual app: search/filter on the left, thumbnail + metadata detail pane on
the right, with quick-filter buttons for the preset categories (Cozy,
Synthwave, Anime, Space, Nature, Pixel Art, Cyberpunk — click to filter,
click again to clear; matches tags case-insensitively). The currently-applied
wallpaper is marked with `▶` in the list and in the detail pane. Keys: `/`
search, `a` apply, `f` favorite, `p` preview, `d` delete, `r` rename, `t` edit
tags, `i` import a folder, `o` add a single file via a native file-picker
dialog (`zenity`), `s` settings.

Run `livewall install desktop-entry` to make it launchable from your app
launcher (like Caelestia's own) instead of typing the command — it's a
`.desktop` entry whose `Exec=` opens it in a `foot` window, the standard
approach for terminal apps.

### Quick picker (`livewall picker`)

A rofi-style picker meant to run in a small floating terminal: type to
filter by name/tag, arrow keys to move, Enter applies instantly and closes,
Esc cancels. This is the one piece of UI caelestia-aw's own launcher doesn't
have — its picker can't filter by tag or favorite.

After `livewall install hyprland`, `Super+Shift+B` opens it in a centered
floating `foot` window.

### Settings

`livewall gui` → `s`. LiveWall only controls what it adds on top of
caelestia-aw: scheduled random interval, favorites-only/tag-filtered random,
and a Material-You-recolour opt-out. Rendering, HW decode, and
pause-on-battery/fullscreen are Caelestia's own settings — see its Nexus
settings app.

Changing "Random interval" here actually installs/removes the
`livewall-random.timer` systemd unit to match (same as
`livewall install/uninstall systemd`), not just a config value — so don't
hand-edit `random_interval` in `config.json`, it won't touch the timer.

## Architecture

No giant single file — each module owns one concern:

| Module | Responsibility |
|---|---|
| `config.py` | XDG paths, `Config` dataclass (load/save JSON) |
| `utils.py` | logging setup, hashing, formatting, format detection |
| `database.py` | `Wallpaper` dataclass + JSON-backed CRUD store |
| `thumbnail.py` | ffprobe metadata, ffmpeg thumbnail generation/caching |
| `library.py` | add/remove/rename/import/search/tag/favorite/dedupe, on top of `database.py` + `thumbnail.py` |
| `engine.py` | thin wrapper around `caelestia wallpaper -f/--extract-thumbs` and its state file — LiveWall never renders anything itself |
| `hypr.py` | opt-in Hyprland keybind/window-rule installer, with backups |
| `desktop.py` | opt-in `.desktop` entry + icon so `livewall gui` shows up in your app launcher |
| `doctor.py` | `livewall doctor` health checks across all of the above |
| `systemd.py` | opt-in systemd `--user` units: random rotation, periodic sync, boot-time pause-bug fix |
| `battery.py` | opt-in `WallpaperPauser.qml` patch for battery-percentage pause (see below) |
| `cli.py` | argparse entry point (`livewall ...`) |
| `gui.py` | Textual library browser |
| `picker.py` | Textual quick picker |

## Data locations

- `~/.config/livewall/config.json` — settings
- `~/.local/share/livewall/library.json` — wallpaper metadata (tags, favorites, hashes)
- `~/.cache/livewall/thumbnails/` — LiveWall's own thumbnail cache (independent of caelestia-aw's own `~/.cache/caelestia/videothumbs/`)

LiveWall never moves or copies your wallpaper files — the library just
stores paths into wherever they already live (by default,
`~/Pictures/Wallpapers`, same as caelestia-aw).

## Battery saver

caelestia-aw's own `WallpaperPauser` only knows "on AC or not" — no
percentage threshold — and exposes no pause/resume IPC for wallpapers at all
(`caelestia shell -s` shows `target wallpaper` has only `list/set/get`). A
static-frame swap was tried first and abandoned: static images are broken on
some caelestia-aw installs (confirmed by applying caelestia-aw's own bundled
default wallpaper and getting the same result), independent of anything
LiveWall generates — not something fixable from here.

So `livewall install battery-saver` instead makes a small, targeted patch to
caelestia-aw's own `WallpaperPauser.qml`: it adds a battery-**percentage**
check (`UPower.displayDevice.percentage`) with its own hysteresis (default
15% low / 25% high) alongside the existing AC-based rule, and reacts to
`percentageChanged` immediately — no polling. When it fires, it calls
caelestia-aw's real internal `pause()`/`resume()`, which freezes and resumes
the actual current video frame in place, exactly like its existing
pause-behind-windows feature already does, just driven by percentage instead
of AC status.

This is the one part of LiveWall that edits a caelestia-aw file instead of
just calling its CLI. On Arch/AUR installs `WallpaperPauser.qml` is normally
owned by your own user (not root), so no `sudo` is needed — but a
`caelestia-shell` package update will overwrite it, so re-run `livewall
install battery-saver` after that happens. A backup is made automatically
(`WallpaperPauser.qml.livewall.bak`) and `livewall uninstall battery-saver`
restores it.

## Known caelestia-aw issues LiveWall works around

- **Wallpapers frozen on their first frame.** caelestia-aw's `WallpaperPauser`
  (its pause-on-battery / pause-behind-windows feature) occasionally fails to
  initialize its settings backend on shell startup (`Failed to initialize
  QSettings instance` in `caelestia shell -l`), silently pinning
  "pause behind windows" to on regardless of what you've set in Nexus
  settings — which freezes all video wallpapers. Run `livewall restart-shell`
  to force a clean re-init; `livewall gui`/`livewall picker` will still
  *apply* correctly even while this is happening, they just won't visibly
  animate until the shell is restarted. This can happen on the shell's very
  *first* launch too (i.e. right after login/boot, since `caelestia shell -d`
  runs via Hyprland's own startup hook) — `livewall install boot-fix` installs
  a systemd `--user` service that runs `livewall ensure-playing` ~5s after
  every login. That command samples decode CPU on the current video briefly
  and only restarts the shell if it's genuinely not decoding — most boots
  never hit the bug, so this skips the (slow) restart entirely instead of
  paying its cost unconditionally on every login.
- **`.gif` files never animate**, independent of the above — caelestia-aw
  only plays real video containers (`.mp4`/`.webm`/`.mkv`) via QtMultimedia;
  `.gif` always renders as a static first frame (both its Python CLI's
  `is_video()` and QML's `Wallpapers.isVideo()` exclude it). If your library
  has both formats of the same wallpaper, `livewall random` and the picker
  both prefer the non-`.gif` copy automatically for this reason.
