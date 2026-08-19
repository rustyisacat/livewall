# PyInstaller spec for the Windows build. Must be run ON Windows (PyInstaller
# does not cross-compile) — see .github/workflows/windows-build.yml, which
# runs this on a windows-latest GitHub Actions runner. To build locally on a
# real Windows machine instead:
#
#   uv sync
#   uv run pyinstaller packaging/windows/livewall.spec
#
# Produces dist/LiveWall/LiveWall.exe (onedir build — simpler to debug and to
# verify mpv.exe actually landed next to the app than a single-file onefile
# build; switch to onefile later once this has been validated on real
# Windows if a single distributable file is preferred).

import shutil
from pathlib import Path

block_cipher = None

ROOT = Path(SPECPATH).resolve().parent.parent  # repo root
DATA_DIR = ROOT / "data"

# mpv.exe is expected to be dropped in packaging/windows/vendor/mpv.exe by
# the CI workflow (or manually, for a local build) before running
# PyInstaller — not committed to the repo itself. See the CI workflow for
# where it's fetched from. LiveWall still also checks PATH for a system
# mpv install (see WindowsMpvBackend._mpv_path()), this bundled copy is
# what makes the packaged .exe work with zero extra setup.
_mpv_source = ROOT / "packaging" / "windows" / "vendor" / "mpv.exe"
binaries = []
if _mpv_source.exists():
    binaries.append((str(_mpv_source), "."))
else:
    print(f"WARNING: {_mpv_source} not found — building without a bundled mpv.exe")

datas = [
    (str(DATA_DIR / "livewall.ico"), "."),
]

a = Analysis(
    [str(ROOT / "src" / "livewall" / "gui_qt" / "app.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "livewall.backends.caelestia_aw",
        "livewall.backends.mpvpaper",
        "livewall.backends.windows_mpv",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["textual", "textual_image"],  # Linux-only, never needed here
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LiveWall",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # GUI app — no terminal window
    icon=str(DATA_DIR / "livewall.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="LiveWall",
)
