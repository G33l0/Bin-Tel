#!/usr/bin/env python3
"""Package Bin-Tel for Linux.

Produces ``dist/Bin-Tel/`` plus a freedesktop ``.desktop`` entry and the icon
theme files a distribution package or AppImage recipe needs.

    python scripts/build_linux.py
    python scripts/build_linux.py --onefile
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_common import (
    DIST_DIR,
    ICON_DIR,
    ROOT,
    BuildTarget,
    add_common_arguments,
    run_build,
)

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def main(argv: list[str] | None = None) -> int:
    parser = add_common_arguments(
        argparse.ArgumentParser(description="Build the Linux application.")
    )
    parser.add_argument(
        "--no-desktop-files",
        action="store_true",
        help="skip the .desktop entry and icon theme tree",
    )
    args = parser.parse_args(argv)

    if not sys.platform.startswith("linux"):
        print(
            "note: PyInstaller does not cross-compile. Run this on Linux to produce a "
            "working binary; the command below is still shown for reference.\n",
            file=sys.stderr,
        )
        args.dry_run = True

    target = BuildTarget(
        platform="Linux",
        icon=ICON_DIR / "bintel-256.png",
        one_file=args.onefile,
        windowed=not args.console,
    )
    code = run_build(target, clean=not args.no_clean, dry_run=args.dry_run)
    if code != 0:
        return code
    if not args.no_desktop_files:
        write_desktop_files()
    return 0


def write_desktop_files(destination: Path | None = None) -> Path:
    """Write the .desktop entry and hicolor icon tree under ``dist/packaging``."""
    from app.core.constants import APP_ID, APP_NAME, APP_TAGLINE

    destination = destination or (DIST_DIR / "packaging")
    applications = destination / "share" / "applications"
    applications.mkdir(parents=True, exist_ok=True)

    entry = applications / f"{APP_ID}.desktop"
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"GenericName=BIN/IIN Lookup\n"
        f"Comment={APP_TAGLINE}\n"
        f"Exec={APP_NAME} %U\n"
        f"Icon={APP_ID}\n"
        "Terminal=false\n"
        "Categories=Office;Finance;Database;Utility;\n"
        "Keywords=BIN;IIN;bank;issuer;card;finance;lookup;\n"
        "StartupWMClass=Bin-Tel\n"
        "StartupNotify=true\n",
        encoding="utf-8",
    )

    for size in ICON_SIZES:
        source = ICON_DIR / f"bintel-{size}.png"
        if not source.exists():
            continue
        target_dir = (
            destination / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target_dir / f"{APP_ID}.png")

    scalable = destination / "share" / "icons" / "hicolor" / "scalable" / "apps"
    scalable.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "assets" / "branding" / "bintel-mark.svg", scalable / f"{APP_ID}.svg")

    print(f"Desktop integration files written to {destination}")
    return destination


if __name__ == "__main__":
    raise SystemExit(main())
