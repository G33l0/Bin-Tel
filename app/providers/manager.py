"""Provider selection and fallback."""

from __future__ import annotations

from pathlib import Path

from app.core.errors import ManifestError, OfflineError
from app.core.logging_config import get_logger
from app.providers.base import BaseProvider, CancelCheck, ProgressCallback, ProviderStatus
from app.providers.manifest import DatabaseManifest, compare_versions

logger = get_logger(__name__)


class ProviderManager:
    """Holds the ordered provider chain and picks the best manifest available.

    Ordering is by registration; the first provider that answers wins, and a
    provider that is merely offline never masks one that works.
    """

    def __init__(self, providers: list[BaseProvider] | None = None) -> None:
        self._providers: list[BaseProvider] = list(providers or [])

    # -- registry ---------------------------------------------------------
    @property
    def providers(self) -> list[BaseProvider]:
        return list(self._providers)

    @property
    def enabled_providers(self) -> list[BaseProvider]:
        return [provider for provider in self._providers if provider.enabled]

    def register(self, provider: BaseProvider, *, first: bool = False) -> BaseProvider:
        if first:
            self._providers.insert(0, provider)
        else:
            self._providers.append(provider)
        return provider

    def clear(self) -> None:
        self._providers.clear()

    def get(self, name: str) -> BaseProvider | None:
        for provider in self._providers:
            if provider.name == name:
                return provider
        return None

    def replace(self, name: str, provider: BaseProvider) -> None:
        for index, existing in enumerate(self._providers):
            if existing.name == name:
                self._providers[index] = provider
                return
        self._providers.append(provider)

    # -- operations -------------------------------------------------------
    def fetch_manifest(self) -> tuple[DatabaseManifest, BaseProvider]:
        """Best manifest from the highest-priority provider that responds."""
        if not self.enabled_providers:
            raise ManifestError(
                "No database source is configured.",
                detail="ProviderManager has no enabled providers",
            )
        errors: list[str] = []
        offline_only = True
        for provider in self.enabled_providers:
            if provider.optional and not provider.is_available():
                continue
            try:
                manifest = provider.fetch_manifest()
            except OfflineError as exc:
                errors.append(f"{provider.label}: {exc.message}")
                continue
            except Exception as exc:
                offline_only = False
                errors.append(f"{provider.label}: {exc}")
                logger.warning("Provider %s failed: %s", provider.name, exc)
                continue
            logger.info(
                "Manifest retrieved",
                extra={
                    "context": {
                        "provider": provider.name,
                        "version": manifest.version,
                        "size": manifest.database_size,
                    }
                },
            )
            return manifest, provider

        detail = "; ".join(errors)
        if offline_only:
            raise OfflineError(
                "Bin-Tel could not reach any update source. Your local database is "
                "unaffected and remains fully searchable.",
                detail=detail,
            )
        raise ManifestError(
            "None of the configured database sources returned valid update information.",
            detail=detail,
        )

    def download(
        self,
        manifest: DatabaseManifest,
        destination: Path,
        *,
        provider: BaseProvider | None = None,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        chosen = provider or self._provider_for(manifest)
        return chosen.download(
            manifest, destination, progress=progress, cancelled=cancelled
        )

    def _provider_for(self, manifest: DatabaseManifest) -> BaseProvider:
        if manifest.download_url.startswith("file://"):
            local = self.get("local")
            if local is not None:
                return local
        for provider in self.enabled_providers:
            return provider
        raise ManifestError("No database source is configured.")

    def latest_of(self, manifests: list[DatabaseManifest]) -> DatabaseManifest | None:
        """Highest version among several manifests."""
        best: DatabaseManifest | None = None
        for manifest in manifests:
            if best is None or compare_versions(manifest.version, best.version) > 0:
                best = manifest
        return best

    def status_summary(self) -> dict[str, ProviderStatus]:
        return {provider.name: provider.status for provider in self._providers}
