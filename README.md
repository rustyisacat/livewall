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
livewall picker                      # the quick picker (see below)
livewall gui                         # the full library browser (see below)
livewall install hyprland            # add the Super+Shift+B picker keybind (opt-in, confirms first)
livewall install systemd --interval 15m|30m|1h   # scheduled random rotation (opt-in, confirms first)
```

### GUI (`livewall gui`)

A Textual app: search/filter on the left, thumbnail + metadata detail pane on
the right. Keys: `/` search, `a` apply, `f` favorite, `p` preview, `d` delete,
`r` rename, `t` edit tags, `i` import a folder, `s` settings.

### Quick picker (`livewall picker`)

A rofi-style picker meant to run in a small floating terminal: type to
filter by name/tag, arrow keys to move, Enter applies instantly and closes,
Esc cancels. This is the one piece of UI caelestia-aw's own launcher doesn't
have — its picker can't filter by tag or favorite.

After `livewall install hyprland`, `Super+Shift+B` opens it in a centered
floating `foot` window.

### Settings

`livewall gui` → `s`, or edit `~/.config/livewall/config.json` directly.
LiveWall only controls what it adds on top of caelestia-aw: scheduled random
interval, favorites-only/tag-filtered random, and a Material-You-recolour
opt-out. Rendering, HW decode, and pause-on-battery/fullscreen are Caelestia's
own settings — see its Nexus settings app.

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
| `systemd.py` | opt-in systemd `--user` timer for random rotation |
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
