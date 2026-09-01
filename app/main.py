"""Bin-Tel application entry point.

Startup sequence::

    load configuration → initialise logging → resolve the database
    → (first run) download, verify and install → open the database
    → initialise services → load the theme → show the Dashboard

The main window is never shown before mandatory first-run initialisation has
completed successfully.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Make ``python app/main.py`` work as well as ``python -m app.main``.
if __package__ in (None, ""):  # pragma: no cover - direct-script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.constants import (
    APP_ID,
    APP_NAME,
    APP_VERSION,
    DATA_DIR_ENV_VAR,
    ORG_DOMAIN,
    ORG_NAME,
    PORTABLE_ENV_VAR,
)
from app.core.errors import BinTelError, friendly_message
from app.core.logging_config import get_logger, log_event, setup_logging
from app.core.paths import get_paths, reset_paths_cache

logger = get_logger(__name__)

#: Environment variable that overrides the configured manifest URL for one run.
MANIFEST_ENV_VAR = "BINTEL_MANIFEST_URL"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bin-tel",
        description=f"{APP_NAME} — worldwide BIN/IIN and financial-institution intelligence.",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument(
        "--data-dir", type=Path, help="override the application-data directory"
    )
    parser.add_argument(
        "--portable", action="store_true", help="store data beside the executable"
    )
    parser.add_argument("--manifest-url", help="override the database distribution endpoint")
    parser.add_argument("--theme", help="start with a specific theme")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="override the configured logging level",
    )
    parser.add_argument(
        "--reset-settings", action="store_true", help="restore default settings and exit"
    )
    parser.add_argument(
        "--no-splash", action="store_true", help="skip the splash screen"
    )
    return parser.parse_args(argv)


def configure_environment(args: argparse.Namespace) -> None:
    """Apply CLI overrides that must land before paths are first resolved."""
    if args.portable:
        os.environ[PORTABLE_ENV_VAR] = "1"
    if args.data_dir:
        os.environ[DATA_DIR_ENV_VAR] = str(Path(args.data_dir).expanduser())
    if args.portable or args.data_dir:
        reset_paths_cache()


def configure_qt() -> None:
    """High-DPI and platform settings that must precede QApplication."""
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    # Fractional display scales round to the nearest sensible layout instead of
    # producing half-pixel borders.
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")


def main(argv: list[str] | None = None) -> int:
    """Launch the Bin-Tel desktop application."""
    args = parse_args(argv)
    configure_environment(args)
    configure_qt()

    paths = get_paths()

    from app.core.config import ConfigManager

    config = ConfigManager(paths)
    config.load()

    if args.reset_settings:
        config.reset_settings()
        print(f"{APP_NAME} settings restored to their defaults.")
        return 0

    level = args.log_level or config.settings.advanced.log_level.value
    log_file = setup_logging(
        level, paths, retention_days=config.settings.advanced.log_retention_days
    )
    log_event(
        logger,
        "Application starting",
        version=APP_VERSION,
        data_dir=str(paths.data_dir),
        log_file=str(log_file),
    )

    manifest_override = args.manifest_url or os.environ.get(MANIFEST_ENV_VAR)
    if manifest_override:
        try:
            config.settings.database.manifest_url = manifest_override
        except Exception as exc:
            logger.warning("Ignoring invalid manifest URL override: %s", exc)

    from PyQt6.QtWidgets import QApplication

    application = QApplication(sys.argv[:1] + [])
    application.setApplicationName(APP_NAME)
    application.setApplicationDisplayName(APP_NAME)
    application.setApplicationVersion(APP_VERSION)
    application.setOrganizationName(ORG_NAME)
    application.setOrganizationDomain(ORG_DOMAIN)
    application.setDesktopFileName(APP_ID)

    from app.core.context import AppContext
    from app.ui.themes.icons import IconProvider
    from app.ui.themes.theme_manager import ThemeManager

    context = AppContext(config=config, paths=paths)
    IconProvider.instance()  # warm the icon cache before anything paints

    themes = ThemeManager(config, paths.themes_dir)
    themes.apply(args.theme or config.settings.appearance.theme, persist=bool(args.theme))
    application.setWindowIcon(IconProvider.instance().app_icon())

    splash = None
    if not args.no_splash:
        from app.ui.windows.splash import BinTelSplash

        splash = BinTelSplash()
        splash.show()
        splash.status("Starting…")

    exit_code = _run(application, context, themes, splash)
    log_event(logger, "Application shutdown", exit_code=exit_code)
    return exit_code


def _run(application, context, themes, splash) -> int:
    """Resolve the database, then show the appropriate window."""
    from PyQt6.QtWidgets import QDialog

    from app.ui.dialogs.error_dialog import ErrorDialog
    from app.ui.windows.first_run_window import FirstRunWindow
    from app.ui.windows.main_window import MainWindow

    def status(message: str) -> None:
        if splash is not None:
            splash.status(message)

    status("Checking the local database…")
    needs_setup = not context.database_installed

    if not needs_setup:
        try:
            context.open_database()
            if context.config.settings.advanced.verify_on_startup:
                status("Verifying the database…")
                report = context.database.verify(quick=True, record=False)
                if not report.ok:
                    logger.error(
                        "Startup verification failed",
                        extra={"context": {"errors": report.errors}},
                    )
                    context.database.close()
                    needs_setup = True
                    if splash is not None:
                        splash.close()
                    ErrorDialog(
                        "The database could not be opened",
                        "Your installed database did not pass verification. Bin-Tel "
                        "will offer to download a fresh copy.",
                        detail="; ".join(report.errors),
                    ).exec()
        except BinTelError as exc:
            logger.error("Could not open the database: %s", exc.message)
            needs_setup = True
            if splash is not None:
                splash.close()
                splash = None
            ErrorDialog(exc.title, exc.message, detail=exc.detail or "").exec()

    if needs_setup:
        if splash is not None:
            splash.close()
            splash = None
        logger.info("Entering the first-run database setup")
        first_run = FirstRunWindow(context)
        if first_run.exec() != QDialog.DialogCode.Accepted or not first_run.database_ready:
            logger.info("First-run setup was not completed; exiting")
            context.shutdown()
            return 0
        if not context.database.is_open:
            try:
                context.open_database()
            except BinTelError as exc:
                ErrorDialog(exc.title, exc.message, detail=exc.detail or "").exec()
                context.shutdown()
                return 1

    status("Loading the interface…")
    window = MainWindow(context, themes)

    if splash is not None:
        splash.finish(window)

    window.show()
    window.raise_()
    window.activateWindow()

    started_at = time.monotonic()
    context.start_session(
        first_run=needs_setup,
        startup_ms=(started_at - context.launched_at) * 1000,
    )
    application.aboutToQuit.connect(
        lambda: context.shutdown(session_seconds=time.monotonic() - started_at)
    )

    try:
        return application.exec()
    except Exception as exc:
        logger.exception("Unhandled exception in the Qt event loop")
        ErrorDialog(
            "Bin-Tel stopped unexpectedly",
            friendly_message(exc),
            detail=f"{type(exc).__name__}: {exc}",
        ).exec()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
