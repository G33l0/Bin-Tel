"""Application context — the composition root.

One object owns the configuration, both databases, the repositories and every
service, and hands them to the UI. Nothing in :mod:`app.ui` constructs a
repository, opens a database or decides an entitlement for itself.

Two databases, deliberately:

* the **intelligence database**, which is downloaded, replaced wholesale on
  every update, and is entirely read-only to the application;
* the **user-data store**, which holds everything the person created and is
  never touched by an update.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from app.core.config import ConfigManager, LicenseServiceMode
from app.core.constants import (
    APP_VERSION,
    LICENSE_API_ENV_VAR,
    TELEMETRY_API_ENV_VAR,
)
from app.core.logging_config import get_logger, log_event
from app.core.paths import AppPaths, get_paths
from app.database.engine import DatabaseManager
from app.database.user_store import USER_DATABASE_FILENAME, UserDataStore
from app.licensing.activation import (
    ActivationService,
    HttpLicenseClient,
    LicenseClient,
    LocalLicenseServer,
)
from app.licensing.devices import DeviceManager
from app.licensing.entitlements import EntitlementService
from app.licensing.license_manager import LicenseManager
from app.licensing.plans import PlanCatalogue
from app.licensing.signing import b64decode
from app.providers.http_provider import HttpProvider
from app.providers.local_provider import LocalPackageProvider
from app.providers.manager import ProviderManager
from app.repositories.bin_repository import BinRepository
from app.repositories.institution_repository import InstitutionRepository
from app.repositories.metadata_repository import MetadataRepository
from app.repositories.search_repository import SearchRepository
from app.repositories.stats_repository import StatsRepository
from app.services.analytics_service import AnalyticsService
from app.services.backup_service import BackupService
from app.services.bank_service import BankService
from app.services.change_detection import ChangeDetectionService
from app.services.database_service import DatabaseService
from app.services.export_service import ExportService
from app.services.health_service import DatabaseHealthService
from app.services.lookup_service import LookupService
from app.services.portfolio_service import PortfolioService
from app.services.quality_service import DataQualityService
from app.services.report_service import ReportService
from app.services.search_service import SearchService
from app.services.stats_service import StatsService
from app.services.update_journal import UpdateJournal
from app.services.update_service import DatabaseUpdateService, UpdateOutcome
from app.services.watchlist_service import WatchlistService
from app.services.workspace_service import WorkspaceService
from app.telemetry.events import Counter, Event
from app.telemetry.service import TelemetryService

logger = get_logger(__name__)

#: Public key the shipped build verifies hosted licences against.
#:
#: Replaced at release time with the licensing service's real key. It is a
#: *public* key by design: the client only ever verifies, never signs.
BUNDLED_LICENSE_PUBLIC_KEY = ""


class AppContext:
    """Wires the whole application together and keeps it consistent."""

    def __init__(
        self,
        config: ConfigManager | None = None,
        paths: AppPaths | None = None,
    ) -> None:
        #: When this context was built — the reference point for startup time.
        self.launched_at = time.monotonic()
        self.paths = paths or get_paths()
        self.config = config or ConfigManager(self.paths)
        # Re-reading here would discard overrides the caller already applied
        # (a --manifest-url flag, for instance), so only load a fresh manager.
        if not self.config.is_loaded:
            self.config.load()

        database_path = self.config.database_path()

        # -- databases ----------------------------------------------------
        self.manager = DatabaseManager(database_path)
        self.user_store = UserDataStore(self.paths.data_dir / USER_DATABASE_FILENAME)
        self.user_store.open()

        # -- repositories --------------------------------------------------
        self.bins = BinRepository(self.manager)
        self.institutions = InstitutionRepository(self.manager)
        self.metadata = MetadataRepository(self.manager)
        self.stats_repository = StatsRepository(self.manager)
        self.search_repository = SearchRepository(self.manager)

        # -- licensing -----------------------------------------------------
        self.plans = PlanCatalogue.load(self._plans_config_path())
        self.devices = DeviceManager(self.user_store)
        self.activation = ActivationService(self._license_client(), self.devices)
        self.licenses = LicenseManager(self.user_store, self.activation, self.devices)
        self.entitlements = EntitlementService(self.licenses, self.plans)
        self.licenses.set_change_listener(self._on_license_changed)

        # -- telemetry -----------------------------------------------------
        privacy = self.config.settings.privacy
        self.telemetry = TelemetryService(
            self.user_store,
            endpoint=os.environ.get(TELEMETRY_API_ENV_VAR) or privacy.telemetry_url,
            enabled=privacy.telemetry_enabled,
        )
        self.telemetry.set_context(plan=self.entitlements.plan.value)

        # -- core services --------------------------------------------------
        self.database = DatabaseService(self.manager, database_path)
        self.lookup = LookupService(self.bins)
        self.portfolios = PortfolioService(self.manager, self.bins)
        self.quality = DataQualityService(self.manager)
        self.banks = BankService(self.institutions, self.bins, self.portfolios)
        self.stats = StatsService(self.stats_repository, self.metadata, database_path)
        self.exports = ExportService()
        self.health = DatabaseHealthService(self.manager)
        self.analytics = AnalyticsService(self.manager)
        self.workspace = WorkspaceService(self.user_store)
        self.search = SearchService(
            self.search_repository, self.workspace, self.entitlements
        )
        self.reports = ReportService(self.config.reports_path())
        self.change_detection = ChangeDetectionService(database_path)
        self.watchlists = WatchlistService(self.user_store, self.change_detection)

        self.backups = BackupService(
            database_path,
            self.config.backups_path(),
            keep=self.config.settings.database.max_backups,
        )

        # -- distribution ----------------------------------------------------
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
            on_installed=self._on_database_installed,
        )
        self.configure_providers()

        log_event(
            logger,
            "Application context ready",
            version=APP_VERSION,
            plan=self.entitlements.plan.value,
            telemetry=self.telemetry.enabled,
        )

    # -- configuration paths ------------------------------------------------
    def _plans_config_path(self) -> Path:
        """``config/plans.json`` beside the application, when present."""
        from app.core.paths import bundle_root

        return bundle_root() / "config" / "plans.json"

    # -- providers ----------------------------------------------------------
    def configure_providers(self) -> None:
        """Rebuild the provider chain from the current settings."""
        url = self.config.settings.database.manifest_url
        self.providers.clear()
        if url.startswith("file://") or (
            Path(url).suffix == ".json" and not url.startswith("http")
        ):
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

    # -- licensing ----------------------------------------------------------
    def _license_client(self) -> LicenseClient:
        """Build the licensing client the settings ask for."""
        settings = self.config.settings.license
        if settings.service_mode is LicenseServiceMode.DEVELOPMENT:
            return LocalLicenseServer(self.paths.config_dir / "licensing")
        key = self._verifying_key(settings.verifying_key)
        url = os.environ.get(LICENSE_API_ENV_VAR) or settings.api_url
        return HttpLicenseClient(url, key)

    @staticmethod
    def _verifying_key(configured: str) -> bytes:
        raw = (configured or BUNDLED_LICENSE_PUBLIC_KEY).strip()
        if not raw:
            return b""
        try:
            key = b64decode(raw)
        except (ValueError, TypeError):
            logger.warning("The configured licence verifying key is not valid base64")
            return b""
        if len(key) != 32:
            logger.warning("The configured licence verifying key is not an Ed25519 key")
            return b""
        return key

    def reconfigure_licensing(self) -> None:
        """Rebuild the licensing client after a settings change."""
        self.activation.set_client(self._license_client())
        self.licenses.load()
        self.entitlements.notify()

    def _on_license_changed(self, snapshot) -> None:
        """Keep everything that depends on the plan in step."""
        self.telemetry.set_context(plan=snapshot.plan.value)
        self.entitlements.notify()

    # -- database -----------------------------------------------------------
    @property
    def database_path(self) -> Path:
        return self.database.path

    @property
    def database_installed(self) -> bool:
        return self.database.is_installed

    def open_database(self) -> None:
        self.database.open()
        self.analytics.invalidate()
        version = self.database_version()
        self.telemetry.set_context(database_version=version)
        log_event(
            logger,
            "Application database ready",
            path=str(self.database_path),
            version=version,
        )

    def database_version(self) -> str | None:
        if not self.manager.is_open:
            return None
        try:
            return self.metadata.version()
        except Exception:  # noqa: BLE001 - a metadata read must not break startup
            return None

    def _on_database_installed(self, outcome: UpdateOutcome) -> None:
        """Runs on the worker thread once a new database is active."""
        self.analytics.invalidate()
        self.telemetry.set_context(database_version=outcome.version)
        self.telemetry.record(
            Event.DATABASE_UPDATED,
            {
                "from_version": outcome.previous_version or "",
                "to_version": outcome.version or "",
                "bytes_downloaded": outcome.bytes_downloaded,
                "migrated": outcome.migrated,
                "used_delta": outcome.used_delta,
            },
        )
        if not self.config.settings.watchlists.scan_after_update:
            return
        try:
            alerts = self.watchlists.scan_for_changes(
                from_version=outcome.previous_version, to_version=outcome.version
            )
        except Exception:  # noqa: BLE001 - never fail an update over a scan
            logger.exception("Change detection after the update did not complete")
            return
        if alerts:
            self.telemetry.increment(Counter.WATCHLIST_EVENT_COUNT, len(alerts))
            self.telemetry.record(
                Event.WATCHLIST_ALERT_RAISED,
                {"change_type": alerts[0].change_type.value, "count_bucket": _bucket(len(alerts))},
            )

    def apply_database_path(self, path: Path) -> None:
        """Repoint every component at a new database location."""
        self.database.set_path(path)
        self.stats.set_database_path(path)
        self.backups.set_paths(path, self.config.backups_path())
        self.updates.set_paths(path, self.paths.downloads_dir)
        self.change_detection.set_database_path(path)
        self.analytics.invalidate()

    def apply_settings(self) -> None:
        """Re-apply settings that other components cache."""
        settings = self.config.settings
        self.backups.set_retention(settings.database.max_backups)
        self.backups.set_paths(self.database_path, self.config.backups_path())
        self.updates.set_backup_before_update(settings.database.backup_before_update)
        self.reports.set_exports_dir(self.config.reports_path())
        self.telemetry.set_endpoint(
            os.environ.get(TELEMETRY_API_ENV_VAR) or settings.privacy.telemetry_url
        )
        self.telemetry.set_enabled(settings.privacy.telemetry_enabled)
        self.configure_providers()
        new_path = self.config.database_path()
        if new_path != self.database_path:
            self.apply_database_path(new_path)

    # -- lifecycle ----------------------------------------------------------
    def start_session(self, *, first_run: bool, startup_ms: float) -> None:
        """Record the start of a session and do light housekeeping."""
        self.telemetry.increment(Counter.SESSION_COUNT)
        self.telemetry.record(
            Event.APP_STARTED,
            {
                "first_run": first_run,
                "startup_ms": int(startup_ms),
                "theme": self.config.settings.appearance.theme,
            },
        )
        try:
            self.workspace.prune(
                keep_events_days=self.config.settings.watchlists.keep_events_days
            )
        except Exception:  # noqa: BLE001 - housekeeping must not break startup
            logger.debug("Workspace housekeeping did not complete", exc_info=True)

    def shutdown(self, *, session_seconds: float | None = None) -> None:
        try:
            self.telemetry.record(
                Event.APP_CLOSED,
                {"session_seconds": int(session_seconds)} if session_seconds else None,
            )
            self.telemetry.flush()
        except Exception:  # noqa: BLE001 - telemetry must never block shutdown
            logger.debug("Telemetry flush on shutdown did not complete", exc_info=True)
        try:
            self.config.save()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            logger.exception("Could not persist configuration on shutdown")
        self.database.close()
        self.user_store.close()
        logger.info("Application shutdown complete")


def _bucket(value: int) -> str:
    from app.telemetry.events import bucket

    return bucket(value)
