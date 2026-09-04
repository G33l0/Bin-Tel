#!/usr/bin/env python3
"""Build a distributable installer for the current platform.

One command, from a checkout to something a person can install:

    python scripts/build_installer.py

It runs the PyInstaller bundle first, then wraps it in whatever the platform
expects:

======== ================================================================
Windows  ``dist/installer/Bin-Tel-Setup-<version>.exe`` via Inno Setup
macOS    ``dist/installer/Bin-Tel-<version>.dmg`` via hdiutil
Linux    ``dist/installer/bin-tel-<version>-linux-x86_64.tar.gz`` with an
         ``install.sh`` that puts the application in ``~/.local`` and
         registers a desktop entry
======== ================================================================

PyInstaller does not cross-compile: each installer has to be built on the
platform it targets. Run this on Windows for the ``.exe``, on macOS for the
``.dmg``. Nothing here pretends otherwise — asking for a Windows installer on
Linux reports that plainly rather than producing something that will not run.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.core.constants import APP_ID, APP_NAME, APP_SLUG, APP_VERSION  # noqa: E402

DIST = ROOT / "dist"
BUNDLE = DIST / APP_NAME
INSTALLER_DIR = DIST / "installer"
PACKAGING = ROOT / "packaging"


class BuildFailed(RuntimeError):
    """A step failed; no installer is produced."""


# ---------------------------------------------------------------------------
# The application bundle
# ---------------------------------------------------------------------------


def build_bundle(*, skip: bool = False) -> Path:
    """Run the platform's PyInstaller build, unless one is already present."""
    if skip and BUNDLE.exists():
        print(f"Reusing the existing bundle at {BUNDLE}")
        return BUNDLE

    script = {
        "Windows": "build_windows.py",
        "Darwin": "build_macos.py",
        "Linux": "build_linux.py",
    }.get(platform.system())
    if script is None:
        raise BuildFailed(f"No build script for {platform.system()}.")

    print(f"[1/2] Building the application bundle ({script})")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script)], cwd=str(ROOT), check=False
    )
    if result.returncode != 0:
        raise BuildFailed("The PyInstaller build failed; see the output above.")
    if not BUNDLE.exists():
        raise BuildFailed(f"The build reported success but {BUNDLE} is missing.")
    return BUNDLE


# ---------------------------------------------------------------------------
# Windows — Inno Setup
# ---------------------------------------------------------------------------


def build_windows_installer() -> Path:
    script = PACKAGING / "windows" / "bintel.iss"
    if not script.exists():
        raise BuildFailed(f"Missing the installer script at {script}.")

    compiler = shutil.which("iscc") or shutil.which("ISCC")
    if compiler is None:
        # Windows environment variables are case-insensitive, and these are
        # their canonical spellings; ruff's SIM112 assumes POSIX casing.
        for candidate in (
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),  # noqa: SIM112
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")),  # noqa: SIM112
        ):
            probe = candidate / "Inno Setup 6" / "ISCC.exe"
            if probe.exists():
                compiler = str(probe)
                break
    if compiler is None:
        raise BuildFailed(
            "Inno Setup is not installed, or ISCC.exe is not on PATH.\n"
            "Install it from https://jrsoftware.org/isdl.php, then run this again."
        )

    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    print("[2/2] Compiling the Windows installer (Inno Setup)")
    # The version is passed in rather than duplicated in the script, so the
    # file Inno writes and the file this function claims to have produced can
    # never be two different names.
    result = subprocess.run(
        [compiler, f"/DAppVersion={APP_VERSION}", str(script)],
        cwd=str(ROOT),
        check=False,
    )
    if result.returncode != 0:
        raise BuildFailed("Inno Setup reported an error; see the output above.")
    artefact = INSTALLER_DIR / f"Bin-Tel-Setup-{APP_VERSION}.exe"
    if not artefact.exists():
        raise BuildFailed(
            f"Inno Setup finished but {artefact.name} is not there. "
            "Check OutputBaseFilename in packaging/windows/bintel.iss."
        )
    return artefact


# ---------------------------------------------------------------------------
# macOS — a disk image
# ---------------------------------------------------------------------------


def build_macos_installer() -> Path:
    app_bundle = DIST / f"{APP_NAME}.app"
    if not app_bundle.exists():
        raise BuildFailed(
            f"Expected {app_bundle}. Run scripts/build_macos.py first — a .dmg "
            "wraps the .app, so the .app has to exist."
        )
    if shutil.which("hdiutil") is None:
        raise BuildFailed("hdiutil was not found. This step only runs on macOS.")

    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    image = INSTALLER_DIR / f"{APP_NAME}-{APP_VERSION}.dmg"
    image.unlink(missing_ok=True)

    staging = DIST / ".dmg-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copytree(app_bundle, staging / app_bundle.name, symlinks=True)
    # The customary drag-to-install target.
    (staging / "Applications").symlink_to("/Applications")

    print("[2/2] Building the macOS disk image (hdiutil)")
    result = subprocess.run(
        [
            "hdiutil", "create",
            "-volname", f"{APP_NAME} {APP_VERSION}",
            "-srcfolder", str(staging),
            "-ov", "-format", "UDZO",
            str(image),
        ],
        check=False,
    )
    shutil.rmtree(staging, ignore_errors=True)
    if result.returncode != 0:
        raise BuildFailed("hdiutil reported an error; see the output above.")
    return image


# ---------------------------------------------------------------------------
# Linux — a tarball with an installer script
# ---------------------------------------------------------------------------

INSTALL_SH = """\
#!/bin/sh
# Install Bin-Tel for the current user. No root, nothing outside ~/.local.
set -e

PREFIX="${PREFIX:-$HOME/.local}"
APPDIR="$PREFIX/lib/bin-tel"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "Installing Bin-Tel into $APPDIR"
mkdir -p "$APPDIR" "$PREFIX/bin" "$PREFIX/share/applications"
rm -rf "$APPDIR"
cp -R "$HERE/Bin-Tel" "$APPDIR"

ln -sf "$APPDIR/Bin-Tel" "$PREFIX/bin/bin-tel"

if [ -d "$HERE/packaging/share" ]; then
    cp -R "$HERE/packaging/share/." "$PREFIX/share/"
fi

# Point the desktop entry at the installed launcher.
DESKTOP="$PREFIX/share/applications/APP_ID_PLACEHOLDER.desktop"
if [ -f "$DESKTOP" ]; then
    sed -i "s|^Exec=.*|Exec=$PREFIX/bin/bin-tel %U|" "$DESKTOP"
fi

command -v update-desktop-database >/dev/null 2>&1 \\
    && update-desktop-database "$PREFIX/share/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 \\
    && gtk-update-icon-cache -f -t "$PREFIX/share/icons/hicolor" || true

echo
echo "Installed. Start it from your applications menu, or run:  bin-tel"
case ":$PATH:" in
    *":$PREFIX/bin:"*) ;;
    *) echo "Note: $PREFIX/bin is not on your PATH. Add it to use 'bin-tel'." ;;
esac
"""

UNINSTALL_SH = """\
#!/bin/sh
# Remove Bin-Tel. Your BIN list and database are left alone on purpose: the
# list is the source of truth for the database, and an uninstall that silently
# deleted it would throw away work nothing else can recreate. The last lines
# say exactly where it is, so removing it stays your decision.
set -e
PREFIX="${PREFIX:-$HOME/.local}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/APP_SLUG_PLACEHOLDER"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/APP_SLUG_PLACEHOLDER"

rm -rf "$PREFIX/lib/bin-tel"
rm -f "$PREFIX/bin/bin-tel"
rm -f "$PREFIX/share/applications/APP_ID_PLACEHOLDER.desktop"
find "$PREFIX/share/icons/hicolor" -name 'APP_ID_PLACEHOLDER.*' -delete 2>/dev/null || true

echo "Bin-Tel removed."
echo
echo "Your BIN list, database, saved searches and watchlists are still here:"
echo "  $DATA"
echo "Your settings are still here:"
echo "  $CONFIG"
echo
echo "Delete those two folders as well if you want everything gone."
"""


def build_linux_installer() -> Path:
    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    machine = platform.machine() or "x86_64"
    archive = INSTALLER_DIR / f"bin-tel-{APP_VERSION}-linux-{machine}.tar.gz"
    archive.unlink(missing_ok=True)

    staging = DIST / ".tar-staging" / f"bin-tel-{APP_VERSION}"
    if staging.parent.exists():
        shutil.rmtree(staging.parent)
    staging.mkdir(parents=True)

    shutil.copytree(BUNDLE, staging / APP_NAME, symlinks=True)
    if (DIST / "packaging").exists():
        shutil.copytree(DIST / "packaging", staging / "packaging", symlinks=True)
    for name in ("README.md", "LICENSE"):
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, staging / name)
    docs = staging / "docs"
    docs.mkdir(exist_ok=True)
    for name in ("GETTING_STARTED.md", "BIN_LIST.md"):
        source = ROOT / "docs" / name
        if source.exists():
            shutil.copy2(source, docs / name)

    for name, body in (("install.sh", INSTALL_SH), ("uninstall.sh", UNINSTALL_SH)):
        path = staging / name
        path.write_text(
            body.replace("APP_ID_PLACEHOLDER", APP_ID).replace(
                "APP_SLUG_PLACEHOLDER", APP_SLUG
            ),
            encoding="utf-8",
        )
        path.chmod(0o755)

    print("[2/2] Building the Linux archive")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staging, arcname=staging.name)
    shutil.rmtree(staging.parent, ignore_errors=True)
    return archive


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


BUILDERS = {
    "Windows": build_windows_installer,
    "Darwin": build_macos_installer,
    "Linux": build_linux_installer,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--skip-bundle",
        action="store_true",
        help="reuse dist/Bin-Tel instead of rebuilding it",
    )
    args = parser.parse_args(argv)

    system = platform.system()
    builder = BUILDERS.get(system)
    if builder is None:
        print(f"error: no installer recipe for {system}.", file=sys.stderr)
        return 2

    try:
        build_bundle(skip=args.skip_bundle)
        artefact = builder()
    except BuildFailed as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    size = artefact.stat().st_size / (1024 * 1024) if artefact.exists() else 0.0
    print(f"\nInstaller ready: {artefact}  ({size:,.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
