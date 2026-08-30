"""Cross-platform filesystem locations.

Every path the application touches is resolved through :class:`AppPaths`. No
module hard-codes an OS-specific path; the correct per-user application-data
directory is derived at runtime for Windows, macOS and Linux, and a *portable*
mode keeps everything beside the executable instead.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.constants import (
    APP_NAME,
    APP_SLUG,
    DATA_DIR_ENV_VAR,
    DATABASE_FILENAME,
    PORTABLE_ENV_VAR,
    PORTABLE_MARKER,
)


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """Directory that holds read-only resources (assets, config defaults)."""
    if is_frozen():
        # PyInstaller extracts data files to ``sys._MEIPASS``.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def executable_dir() -> Path:
    """Directory containing the running executable (or the project root)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _truthy(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"} if value else False


def portable_mode_enabled() -> bool:
    """Portable mode: keep configuration and database next to the executable."""
    if _truthy(os.environ.get(PORTABLE_ENV_VAR)):
        return True
    try:
        return (executable_dir() / PORTABLE_MARKER).exists()
    except OSError:  # pragma: no cover - unreadable directory
        return False


def _windows_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / APP_NAME


def _macos_data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / APP_NAME


def _linux_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / APP_SLUG


def _linux_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_SLUG


def _linux_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / APP_SLUG


def default_data_dir() -> Path:
    """The per-user application-data directory for the current platform."""
    override = os.environ.get(DATA_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser()
    if portable_mode_enabled():
        return executable_dir() / "bintel-data"
    if sys.platform.startswith("win"):
        return _windows_data_dir()
    if sys.platform == "darwin":
        return _macos_data_dir()
    return _linux_data_dir()


def default_config_dir() -> Path:
    """Per-user configuration directory (separate from data on Linux)."""
    override = os.environ.get(DATA_DIR_ENV_VAR)
    if override or portable_mode_enabled():
        return default_data_dir() / "config"
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return default_data_dir() / "config"
    return _linux_config_dir()


def default_cache_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV_VAR)
    if override or portable_mode_enabled():
        return default_data_dir() / "cache"
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return default_data_dir() / "cache"
    return _linux_cache_dir()


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Resolved set of directories and files used by Bin-Tel."""

    data_dir: Path
    config_dir: Path
    cache_dir: Path

    # -- derived locations ------------------------------------------------
    @property
    def database_dir(self) -> Path:
        return self.data_dir / "database"

    @property
    def database_file(self) -> Path:
        return self.database_dir / DATABASE_FILENAME

    @property
    def downloads_dir(self) -> Path:
        return self.data_dir / "downloads"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "bintel.log"

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def state_file(self) -> Path:
        """Non-preference runtime state (window geometry, last update check)."""
        return self.config_dir / "state.json"

    # -- assets (read-only, shipped with the application) ------------------
    @property
    def assets_dir(self) -> Path:
        return bundle_root() / "assets"

    @property
    def branding_dir(self) -> Path:
        return self.assets_dir / "branding"

    @property
    def icons_dir(self) -> Path:
        return self.assets_dir / "icons"

    @property
    def themes_dir(self) -> Path:
        return self.assets_dir / "themes"

    def ensure(self) -> AppPaths:
        """Create every writable directory. Safe to call repeatedly."""
        for directory in (
            self.data_dir,
            self.config_dir,
            self.cache_dir,
            self.database_dir,
            self.downloads_dir,
            self.backups_dir,
            self.exports_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def with_data_dir(self, data_dir: Path) -> AppPaths:
        """Return a copy rooted at *data_dir* (used by the settings page)."""
        data_dir = Path(data_dir).expanduser()
        return AppPaths(data_dir=data_dir, config_dir=self.config_dir, cache_dir=self.cache_dir)


@lru_cache(maxsize=1)
def get_paths() -> AppPaths:
    """Process-wide singleton of the resolved application paths."""
    return AppPaths(
        data_dir=default_data_dir(),
        config_dir=default_config_dir(),
        cache_dir=default_cache_dir(),
    ).ensure()


def reset_paths_cache() -> None:
    """Clear the cached singleton (used by tests that relocate the data dir)."""
    get_paths.cache_clear()


def human_size(num_bytes: float | None) -> str:
    """Format a byte count for display, e.g. ``148.2 MB``."""
    if num_bytes is None or num_bytes < 0:
        return "Unknown"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"  # pragma: no cover - unreachable


def free_space(path: Path) -> int | None:
    """Bytes available on the volume holding *path* (``None`` if unknown)."""
    import shutil

    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:  # pragma: no cover - exotic filesystems
        return None
