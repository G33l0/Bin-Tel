"""HTTPS provider — the default Bin-Tel distribution channel.

Downloads stream to a temporary file with resume support, so a large package
never sits in memory and a dropped connection does not restart from zero.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from app.core.constants import (
    DOWNLOAD_CHUNK_SIZE,
    DOWNLOAD_TIMEOUT,
    MANIFEST_TIMEOUT,
    USER_AGENT,
)
from app.core.errors import DownloadError, ManifestError, OfflineError, OperationCancelled
from app.core.logging_config import get_logger
from app.providers.base import (
    BaseProvider,
    CancelCheck,
    DownloadProgress,
    ProgressCallback,
    ProviderStatus,
)
from app.providers.manifest import DatabaseManifest

logger = get_logger(__name__)


class HttpProvider(BaseProvider):
    """Fetches a manifest and package over HTTP(S)."""

    name = "https"
    label = "Bin-Tel distribution server"

    def __init__(
        self,
        manifest_url: str,
        *,
        enabled: bool = True,
        verify: bool = True,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(enabled=enabled)
        self.manifest_url = manifest_url
        self._verify = verify
        self._headers = {"User-Agent": USER_AGENT, **(headers or {})}

    # -- manifest ---------------------------------------------------------
    def fetch_manifest(self) -> DatabaseManifest:
        logger.info(
            "Fetching database manifest", extra={"context": {"host": _host(self.manifest_url)}}
        )
        try:
            with httpx.Client(
                timeout=MANIFEST_TIMEOUT,
                follow_redirects=True,
                verify=self._verify,
                headers=self._headers,
            ) as client:
                response = client.get(self.manifest_url)
                response.raise_for_status()
                manifest = DatabaseManifest.parse(response.text)
        except httpx.TransportError as exc:
            self.status = ProviderStatus.OFFLINE
            self.last_error = str(exc)
            raise OfflineError(
                "Bin-Tel could not reach the update server. Your existing database "
                "is unaffected and lookups continue to work offline.",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        except httpx.HTTPStatusError as exc:
            self.status = ProviderStatus.ERROR
            self.last_error = str(exc)
            raise ManifestError(
                f"The update server responded with an error ({exc.response.status_code}).",
                detail=str(exc),
            ) from exc

        self.status = ProviderStatus.ONLINE
        self.last_error = None
        # A relative download_url is resolved against the manifest location.
        if manifest.download_url and not urlparse(manifest.download_url).scheme:
            manifest = manifest.model_copy(
                update={"download_url": urljoin(self.manifest_url, manifest.download_url)}
            )
        return manifest

    # -- package ----------------------------------------------------------
    def download(
        self,
        manifest: DatabaseManifest,
        destination: Path,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        url = manifest.download_url or urljoin(self.manifest_url, f"bintel-{manifest.version}.sqlite")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")

        resume_from = partial.stat().st_size if partial.exists() else 0
        headers = dict(self._headers)
        if resume_from:
            headers["Range"] = f"bytes={resume_from}-"

        state = DownloadProgress(received=resume_from, total=manifest.database_size)
        logger.info(
            "Downloading database package",
            extra={
                "context": {
                    "host": _host(url),
                    "version": manifest.version,
                    "resume_from": resume_from,
                }
            },
        )

        try:
            with httpx.Client(
                timeout=httpx.Timeout(DOWNLOAD_TIMEOUT, read=DOWNLOAD_TIMEOUT),
                follow_redirects=True,
                verify=self._verify,
                headers=headers,
            ) as client:
                with client.stream("GET", url) as response:
                    if resume_from and response.status_code == 200:
                        # The server ignored the Range header; start over.
                        resume_from = 0
                        state = DownloadProgress(received=0, total=manifest.database_size)
                        partial.unlink(missing_ok=True)
                    response.raise_for_status()

                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit():
                        state.total = resume_from + int(declared)
                    elif manifest.database_size:
                        state.total = manifest.database_size

                    mode = "ab" if resume_from else "wb"
                    with partial.open(mode) as handle:
                        for chunk in response.iter_bytes(DOWNLOAD_CHUNK_SIZE):
                            if cancelled is not None and cancelled():
                                raise OperationCancelled("The download was cancelled.")
                            handle.write(chunk)
                            state.advance(len(chunk))
                            if progress is not None:
                                progress(state)
        except OperationCancelled:
            logger.info("Download cancelled by the user")
            raise
        except httpx.TransportError as exc:
            self.status = ProviderStatus.OFFLINE
            raise OfflineError(
                "The connection to the update server was lost. Bin-Tel will resume "
                "from where it stopped when you try again.",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        except httpx.HTTPStatusError as exc:
            self.status = ProviderStatus.ERROR
            raise DownloadError(
                f"The database package could not be downloaded ({exc.response.status_code}).",
                detail=str(exc),
            ) from exc
        except OSError as exc:
            raise DownloadError(
                "Bin-Tel could not write the downloaded file. Check the available disk space.",
                detail=str(exc),
            ) from exc

        partial.replace(destination)
        self.status = ProviderStatus.ONLINE
        logger.info(
            "Download complete",
            extra={"context": {"bytes": destination.stat().st_size, "version": manifest.version}},
        )
        return destination

    def is_available(self) -> bool:
        if not self.enabled:
            return False
        try:
            with httpx.Client(timeout=5.0, verify=self._verify, headers=self._headers) as client:
                response = client.head(self.manifest_url, follow_redirects=True)
            self.status = (
                ProviderStatus.ONLINE if response.status_code < 500 else ProviderStatus.ERROR
            )
        except httpx.HTTPError:
            self.status = ProviderStatus.OFFLINE
        return self.status is ProviderStatus.ONLINE


def _host(url: str) -> str:
    """Host only — never log a full URL that might carry a token."""
    try:
        return urlparse(url).netloc or "unknown"
    except ValueError:  # pragma: no cover - malformed URL
        return "unknown"
