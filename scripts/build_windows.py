#!/usr/bin/env python3
"""Package Bin-Tel for Windows.

    python scripts/build_windows.py            # dist/Bin-Tel/Bin-Tel.exe
    python scripts/build_windows.py --onefile  # dist/Bin-Tel.exe
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_common import ICON_DIR, BuildTarget, add_common_arguments, run_build  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = add_common_arguments(
        argparse.ArgumentParser(description="Build the Windows application.")
    )
    args = parser.parse_args(argv)

    if not sys.platform.startswith("win"):
        print(
            "note: PyInstaller does not cross-compile. Run this on Windows to produce "
            "a working Bin-Tel.exe; the command below is still shown for reference.\n",
            file=sys.stderr,
        )
        args.dry_run = True

    target = BuildTarget(
        platform="Windows",
        icon=ICON_DIR / "bintel.ico",
        one_file=args.onefile,
        windowed=not args.console,
        # Version metadata shown in the Windows file properties dialog.
        extra_args=["--version-file", str(_write_version_file())] if sys.platform.startswith("win") else [],
    )
    return run_build(target, clean=not args.no_clean, dry_run=args.dry_run)


def _write_version_file() -> Path:
    """Write a PyInstaller version resource so the .exe has proper metadata."""
    from build_common import BUILD_DIR

    from app.core.constants import APP_NAME, APP_VERSION, COPYRIGHT, ORG_NAME

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    path = BUILD_DIR / "version_info.txt"
    parts = APP_VERSION.split(".")
    while len(parts) < 4:
        parts.append("0")
    numeric = ", ".join(parts[:4])
    path.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numeric}), prodvers=({numeric}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', '{ORG_NAME}'),
        StringStruct('FileDescription', '{APP_NAME} — BIN/IIN intelligence'),
        StringStruct('FileVersion', '{APP_VERSION}'),
        StringStruct('InternalName', '{APP_NAME}'),
        StringStruct('LegalCopyright', '{COPYRIGHT}'),
        StringStruct('OriginalFilename', '{APP_NAME}.exe'),
        StringStruct('ProductName', '{APP_NAME}'),
        StringStruct('ProductVersion', '{APP_VERSION}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    raise SystemExit(main())
