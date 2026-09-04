"""Local/offline provider.

Reads a manifest and package from the filesystem (or a ``file://`` URL). This
is what makes an air-gapped install, a mirrored package on a shared drive, and
the test-suite work without a network.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from app.core.errors import DownloadError, ManifestError, OperationCancelled
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

_COPY_CHUNK = 1 << 20  # 1 MiB

#: A Windows drive specification: ``C:`` or the legacy URL form ``C|``.
_DRIVE = re.compile(r"^[A-Za-z][:|]")


def path_from_url(value: str) -> Path:
    r"""Accept a plain path or a ``file://`` URL.

    The conversion is delegated to :func:`urllib.request.url2pathname` rather
    than done by hand, because a ``file://`` URL is not a path with a prefix on
    it. On Windows ``file:///C:/data/m.json`` parses to ``/C:/data/m.json`` —
    a leading slash in front of the drive letter — and treating that as a path
    looks for ``\C:\data`` on the current drive, which is nowhere. Every
    update source configured as a URL was unreachable on Windows because of it.

    A ``file://`` URL may also name a host, which on Windows is a UNC share:
    ``file://server/share/m.json`` means ``\\server\share\m.json``. That is
    the "mirrored package on a shared drive" this module exists to support, so
    it is reassembled rather than silently dropped. ``localhost`` names this
    machine and is not a share.
    """
    if value.startswith("file://"):
        parsed = urlparse(value)
        host = unquote(parsed.netloc)
        # `file://C:\data\m.json` has two slashes where a Windows path needs
        # three, so urlparse reads the drive — and everything after it — as the
        # host. It is a malformed URL and a very common way to write one, and
        # reading C: as a machine on the network helps nobody. A host that
        # starts with a drive letter is a path.
        if _DRIVE.match(host):
            return Path(url2pathname(f"{host}{parsed.path}"))
        path = url2pathname(parsed.path)
        if host and host.lower() != "localhost":
            return Path(f"//{host}{path}")
        return Path(path)
    return Path(value).expanduser()


class LocalPackageProvider(BaseProvider):
    """Serves a package from a directory or an explicit manifest file."""

    name = "local"
    label = "Local database package"

    def __init__(self, manifest_path: Path | str, *, enabled: bool = True) -> None:
        super().__init__(enabled=enabled)
        self.manifest_path = path_from_url(str(manifest_path))

    def fetch_manifest(self) -> DatabaseManifest:
        path = self.manifest_path
        if path.is_dir():
            path = path / "database-manifest.json"
        if not path.exists():
            self.status = ProviderStatus.ERROR
            raise ManifestError(
                "No local database package was found at that location.",
                detail=f"Missing manifest {path}",
            )
        manifest = DatabaseManifest.from_file(path)
        self.status = ProviderStatus.ONLINE
        if manifest.download_url and not urlparse(manifest.download_url).scheme:
            # Relative package path, resolved next to the manifest.
            resolved = (path.parent / manifest.download_url).resolve()
            manifest = manifest.model_copy(update={"download_url": resolved.as_uri()})
        return manifest

    def download(
        self,
        manifest: DatabaseManifest,
        destination: Path,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        source = path_from_url(manifest.download_url)
        if not source.exists():
            raise DownloadError(
                "The local database package could not be found.",
                detail=f"Missing package {source}",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        total = source.stat().st_size
        state = DownloadProgress(received=0, total=total)
        partial = destination.with_suffix(destination.suffix + ".part")
        try:
            with source.open("rb") as reader, partial.open("wb") as writer:
                while chunk := reader.read(_COPY_CHUNK):
                    if cancelled is not None and cancelled():
                        raise OperationCancelled("The import was cancelled.")
                    writer.write(chunk)
                    state.advance(len(chunk))
                    if progress is not None:
                        progress(state)
            shutil.copystat(source, partial)
            partial.replace(destination)
        except OperationCancelled:
            partial.unlink(missing_ok=True)
            raise
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise DownloadError(
                "Bin-Tel could not copy the local database package.", detail=str(exc)
            ) from exc
        logger.info(
            "Local package installed to staging",
            extra={"context": {"version": manifest.version, "bytes": total}},
        )
        return destination

    def is_available(self) -> bool:
        if not self.enabled:
            return False
        path = self.manifest_path
        if path.is_dir():
            path = path / "database-manifest.json"
        available = path.exists()
        self.status = ProviderStatus.ONLINE if available else ProviderStatus.ERROR
        return available
