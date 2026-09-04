"""Shared PyInstaller packaging logic for Bin-Tel.

No database is bundled, and none is downloaded: Bin-Tel builds its database
from the BIN list the user maintains, so the installer ships branding, icons,
theme tokens and an empty BIN list *template*.

The template matters. A packaged application unpacks its data files to a
temporary directory that is deleted on exit, so the shipped copy can never be
the file the user edits. On first run the application seeds a writable copy in
the user's data directory from this template — see
:func:`app.services.bin_list.seed_bin_list`. Without the template the seed
still works, but the user loses the comment block explaining the format.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.constants import APP_NAME, APP_VERSION  # noqa: E402 - after sys.path

DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
ASSETS_DIR = ROOT / "assets"
ICON_DIR = ASSETS_DIR / "icons" / "app"

#: Data files bundled with the executable, as (source, destination) pairs.
DATA_FILES: tuple[tuple[str, str], ...] = (
    ("assets/branding", "assets/branding"),
    ("assets/icons", "assets/icons"),
    ("assets/themes", "assets/themes"),
    # The BIN list template the first run seeds a writable copy from.
    ("data/bin-list.csv", "data"),
    # The public dataset, so an install arrives able to answer something
    # rather than with an empty database. Named file by file rather than as a
    # folder: the samples beside them are placeholders for the maintainer's own
    # lists and have no business in someone else's install.
    #
    # The licence and attribution are not optional extras. The dataset is CC BY
    # 4.0, which permits redistribution *provided the attribution travels with
    # it*, and an installer is redistribution. Removing either line from this
    # list breaks the terms the file is carried under.
    ("data/bin-lists/binlist-data.csv", "data/bin-lists"),
    ("data/bin-lists/binlist-data.csv.bintel", "data/bin-lists"),
    ("data/bin-lists/binlist-data.LICENSE.txt", "data/bin-lists"),
    ("data/bin-lists/ATTRIBUTION.md", "data/bin-lists"),
)

#: Modules PyInstaller's static analysis cannot see (loaded via SQLAlchemy's
#: dialect registry and Qt's plugin system).
HIDDEN_IMPORTS: tuple[str, ...] = (
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.sql.default_comparator",
    "PyQt6.QtSvg",
    "PyQt6.QtPrintSupport",
    "pydantic.deprecated.decorator",
)

#: Large, unused dependencies that would otherwise bloat the bundle.
EXCLUDES: tuple[str, ...] = (
    "tkinter",
    "matplotlib",
    "numpy",
    "pandas",
    "scipy",
    "PIL",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtQuick",
    "PyQt6.QtQml",
    "PyQt6.Qt3DCore",
    "PyQt6.QtMultimedia",
    "PyQt6.QtBluetooth",
    "PyQt6.QtNfc",
    "PyQt6.QtPositioning",
    "PyQt6.QtSensors",
    "PyQt6.QtSerialPort",
    "PyQt6.QtDesigner",
    "PyQt6.QtHelp",
    "PyQt6.QtTest",
    "test",
    "unittest",
    "pytest",
)


@dataclass(slots=True)
class BuildTarget:
    """One platform's packaging configuration."""

    platform: str
    icon: Path | None
    one_file: bool = False
    windowed: bool = True
    bundle_identifier: str | None = None
    extra_args: list[str] = field(default_factory=list)

    @property
    def executable_name(self) -> str:
        return APP_NAME  # PyInstaller adds .exe on Windows automatically


def ensure_icons() -> None:
    """Generate the platform icons if they are missing or out of date."""
    if (ICON_DIR / "bintel.ico").exists() and (ICON_DIR / "bintel-256.png").exists():
        return
    print("Generating application icons…")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_app_icons.py")], check=True
    )


def separator() -> str:
    """PyInstaller's ``--add-data`` separator is platform-specific."""
    return ";" if sys.platform.startswith("win") else ":"


def build_command(target: BuildTarget, *, clean: bool = True) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--name",
        target.executable_name,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(BUILD_DIR),
    ]
    if clean:
        command.append("--clean")
    command.append("--onefile" if target.one_file else "--onedir")
    command.append("--windowed" if target.windowed else "--console")

    if target.icon is not None and target.icon.exists():
        command += ["--icon", str(target.icon)]
    if target.bundle_identifier:
        command += ["--osx-bundle-identifier", target.bundle_identifier]

    for source, destination in DATA_FILES:
        command += ["--add-data", f"{ROOT / source}{separator()}{destination}"]
    for module in HIDDEN_IMPORTS:
        command += ["--hidden-import", module]
    for module in EXCLUDES:
        command += ["--exclude-module", module]

    command += target.extra_args
    command.append(str(ROOT / "app" / "main.py"))
    return command


def run_build(target: BuildTarget, *, clean: bool = True, dry_run: bool = False) -> int:
    """Run PyInstaller for *target*, returning its exit code."""
    ensure_icons()
    command = build_command(target, clean=clean)

    print(f"Building {APP_NAME} {APP_VERSION} for {target.platform}")
    print("  " + " ".join(command[:6]) + " …")
    if dry_run:
        print("\nFull command:\n  " + " \\\n    ".join(command))
        return 0

    if shutil.which("pyinstaller") is None:
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            print(
                "error: PyInstaller is not installed. Run "
                "`pip install -r requirements-dev.txt` first.",
                file=sys.stderr,
            )
            return 1

    result = subprocess.run(command, cwd=str(ROOT), check=False)
    if result.returncode == 0:
        artefact = DIST_DIR / target.executable_name
        print(f"\nBuild complete: {artefact}")
    return result.returncode


def add_common_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--onefile", action="store_true", help="produce a single executable")
    parser.add_argument("--console", action="store_true", help="keep a console window")
    parser.add_argument("--no-clean", action="store_true", help="reuse the previous build cache")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the PyInstaller command and exit"
    )
    return parser
