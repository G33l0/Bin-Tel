"""Application context — the composition root.

One object owns the configuration, the database manager, the repositories and
the services, and hands them to the UI. Nothing in :mod:`app.ui` constructs a
repository or opens a database itself.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import ConfigManager
from app.core.constants import APP_VERSION
from app.core.logging_config import get_logger
from app.core.paths import AppPaths, get_paths
from app.database.engine import DatabaseManager
from app.providers.http_provider import HttpProvider
from app.providers.local_provider import LocalPackageProvider
from app.providers.manager import ProviderManager
from app.repositories.bin_repository import BinRepository
from app.repositories.institution_repository import InstitutionRepository
from app.repositories.metadata_repository import MetadataRepository
from app.repositories.stats_repository import StatsRepository
from app.services.backup_service import BackupService
from app.services.bank_service import BankService
from app.services.database_service import DatabaseService
from app.services.export_service import ExportService
from app.services.lookup_service import LookupService
from app.services.stats_service import StatsService
from app.services.update_journal import UpdateJournal
from app.services.update_service import DatabaseUpdateService

logger = get_logger(__name__)


class AppContext:
    """Wires the whole application together and keeps it consistent."""

    def __init__(
        self,
        config: ConfigManager | None = None,
        paths: AppPaths | None = None,
    ) -> None:
        self.paths = paths or get_paths()
        self.config = config or ConfigManager(self.paths)
        # Re-reading here would discard overrides the caller already applied
        # (a --manifest-url flag, for instance), so only load a fresh manager.
        if not self.config.is_loaded:
            self.config.load()

        database_path = self.config.database_path()
        self.manager = DatabaseManager(database_path)

        # Repositories
        self.bins = BinRepository(self.manager)
        self.institutions = InstitutionRepository(self.manager)
        self.metadata = MetadataRepository(self.manager)
        self.stats_repository = StatsRepository(self.manager)

        # Services
        self.database = DatabaseService(self.manager, database_path)
        self.lookup = LookupService(self.bins)
        self.banks = BankService(self.institutions, self.bins)
        self.stats = StatsService(self.stats_repository, self.metadata, database_path)
        self.exports = ExportService()
        self.backups = BackupService(
            database_path,
            self.paths.backups_dir,
            keep=self.config.settings.database.max_backups,
        )
        self.providers = ProviderManager()
        self.journal = UpdateJournal(self.paths.config_dir / "update-history.json")
        self.updates = DatabaseUpdateService(
            self.manager,
            self.providers,
            self.backups,
            database_path=database_path,
            downloads_dir=self.paths.downloads_dir,
            backup_before_update=self.config.settings.database.backup_before_update,
            journal=self.journal,
        )
        self.configure_providers()

    # -- providers --------------------------------------------------------
    def configure_providers(self) -> None:
        """Rebuild the provider chain from the current settings."""
        url = self.config.settings.database.manifest_url
        self.providers.clear()
        if url.startswith("file://") or Path(url).suffix == ".json" and not url.startswith("http"):
            self.providers.register(LocalPackageProvider(url))
        else:
            self.providers.register(HttpProvider(url))
        # A package staged in the downloads folder is an acceptable fallback,
        # but its absence is normal and must not mask the primary provider's
        # diagnosis, so it is registered as optional.
        fallback = LocalPackageProvider(self.paths.downloads_dir / "database-manifest.json")
        fallback.optional = True
        self.providers.register(fallback)

    def use_local_package(self, manifest_path: Path) -> None:
        """Point the provider chain at an explicit local package."""
        self.providers.register(LocalPackageProvider(manifest_path), first=True)

    # -- database ---------------------------------------------------------
    @property
    def database_path(self) -> Path:
        return self.database.path

    @property
    def database_installed(self) -> bool:
        return self.database.is_installed

    def open_database(self) -> None:
        self.database.open()
        logger.info(
            "Application database ready",
            extra={"context": {"path": str(self.database_path), "app": APP_VERSION}},
        )

    def apply_database_path(self, path: Path) -> None:
        """Repoint every component at a new database location."""
        self.database.set_path(path)
        self.stats.set_database_path(path)
        self.backups.set_paths(path, self.paths.backups_dir)
        self.updates.set_paths(path, self.paths.downloads_dir)

    def apply_settings(self) -> None:
        """Re-apply settings that other components cache."""
        settings = self.config.settings
        self.backups.set_retention(settings.database.max_backups)
        self.updates.set_backup_before_update(settings.database.backup_before_update)
        self.configure_providers()
        new_path = self.config.database_path()
        if new_path != self.database_path:
            self.apply_database_path(new_path)

    def shutdown(self) -> None:
        try:
            self.config.save()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            logger.exception("Could not persist configuration on shutdown")
        self.database.close()
        logger.info("Application shutdown complete")
