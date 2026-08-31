#!/usr/bin/env python3
"""Package Bin-Tel for macOS.

Produces ``dist/Bin-Tel.app``. Pass ``--dmg`` to wrap it in a disk image
(requires ``hdiutil``, which ships with macOS).

    python scripts/build_macos.py
    python scripts/build_macos.py --dmg
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_common import (  # noqa: E402
    DIST_DIR,
    ICON_DIR,
    BuildTarget,
    add_common_arguments,
    run_build,
)


def main(argv: list[str] | None = None) -> int:
    parser = add_common_arguments(
        argparse.ArgumentParser(description="Build the macOS application bundle.")
    )
    parser.add_argument("--dmg", action="store_true", help="also produce a .dmg disk image")
    args = parser.parse_args(argv)

    if sys.platform != "darwin":
        print(
            "note: PyInstaller does not cross-compile. Run this on macOS to produce a "
            "working .app; the command below is still shown for reference.\n",
            file=sys.stderr,
        )
        args.dry_run = True

    from app.core.constants import APP_ID

    target = BuildTarget(
        platform="macOS",
        icon=ICON_DIR / "bintel.icns",
        one_file=False,  # .app bundles are always directory builds
        windowed=not args.console,
        bundle_identifier=APP_ID,
    )
    code = run_build(target, clean=not args.no_clean, dry_run=args.dry_run)
    if code != 0 or args.dry_run:
        return code

    if args.dmg:
        code = _make_dmg()
    return code


def _make_dmg() -> int:
    from app.core.constants import APP_NAME, APP_VERSION

    app_bundle = DIST_DIR / f"{APP_NAME}.app"
    if not app_bundle.exists():
        print(f"error: {app_bundle} was not produced.", file=sys.stderr)
        return 1
    dmg = DIST_DIR / f"{APP_NAME}-{APP_VERSION}.dmg"
    dmg.unlink(missing_ok=True)
    print(f"Creating {dmg.name}…")
    result = subprocess.run(
        [
            "hdiutil", "create",
            "-volname", f"{APP_NAME} {APP_VERSION}",
            "-srcfolder", str(app_bundle),
            "-ov", "-format", "UDZO",
            str(dmg),
        ],
        check=False,
    )
    if result.returncode == 0:
        print(f"Disk image written to {dmg}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
