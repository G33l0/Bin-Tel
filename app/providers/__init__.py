"""Database distribution providers.

Bin-Tel does not depend on any particular third-party BIN website. A provider
answers two questions — *what is the latest package?* and *give me that file* —
and :class:`~app.providers.manager.ProviderManager` tries the configured
providers in order.
"""

from app.providers.base import BaseProvider, DownloadProgress, ProviderStatus
from app.providers.http_provider import HttpProvider
from app.providers.local_provider import LocalPackageProvider
from app.providers.manager import ProviderManager
from app.providers.manifest import DatabaseManifest, ManifestParseError

__all__ = [
    "BaseProvider",
    "DatabaseManifest",
    "DownloadProgress",
    "HttpProvider",
    "LocalPackageProvider",
    "ManifestParseError",
    "ProviderManager",
    "ProviderStatus",
]
