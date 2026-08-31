"""Static application identity and protocol constants.

Nothing in this module may import Qt or the database layer; it is the lowest
level of the application and is safe to import from anywhere (including the
maintenance CLI and the test-suite).
"""

from __future__ import annotations

from typing import Final

APP_NAME: Final[str] = "Bin-Tel"
APP_SLUG: Final[str] = "bin-tel"
APP_ID: Final[str] = "org.bintel.desktop"
APP_VERSION: Final[str] = "1.0.0"
APP_TAGLINE: Final[str] = "Worldwide BIN/IIN & financial-institution intelligence"
ORG_NAME: Final[str] = "Bin-Tel Project"
ORG_DOMAIN: Final[str] = "bintel.org"
COPYRIGHT: Final[str] = "© 2026 Bin-Tel Project"

WEBSITE_URL: Final[str] = "https://bintel.org"
DOCS_URL: Final[str] = "https://bintel.org/docs"
SUPPORT_URL: Final[str] = "https://bintel.org/support"

#: Filename of the active SQLite database inside the data directory.
DATABASE_FILENAME: Final[str] = "bintel.sqlite"

#: Schema version this build of the application writes.
SCHEMA_VERSION: Final[int] = 1

#: Oldest database schema this build can open. A package below this needs a
#: migration to be applied before it can be activated.
MIN_SCHEMA_VERSION: Final[int] = 1

#: Newest database schema this build can open. A package above this requires a
#: newer application — it is never opened blindly.
MAX_SCHEMA_VERSION: Final[int] = 1

#: Default distribution endpoint. Deliberately a *manifest* endpoint: it is a
#: few hundred bytes, so an update check never downloads the full database.
DEFAULT_MANIFEST_URL: Final[str] = "https://dist.bintel.org/database/database-manifest.json"

#: Network timeouts (seconds).
MANIFEST_TIMEOUT: Final[float] = 15.0
DOWNLOAD_TIMEOUT: Final[float] = 60.0
DOWNLOAD_CHUNK_SIZE: Final[int] = 1 << 18  # 256 KiB

USER_AGENT: Final[str] = f"{APP_NAME}/{APP_VERSION} (+{WEBSITE_URL})"

#: Database editions a plan may be entitled to. The application never branches
#: on these in SQL; the edition is metadata the distribution service uses to
#: decide which package to serve.
DATABASE_EDITIONS: Final[tuple[str, ...]] = (
    "community",
    "professional",
    "business",
    "enterprise",
)

#: Default licensing endpoint. Overridable through settings, an environment
#: variable, or the bundled development adapter.
DEFAULT_LICENSE_API_URL: Final[str] = "https://api.bintel.org/license"
#: Default telemetry endpoint. Only ever contacted when the user opts in.
DEFAULT_TELEMETRY_URL: Final[str] = "https://telemetry.bintel.org/v1/events"

#: Environment overrides for the two hosted services, so a deployment never
#: needs a rebuilt binary to point somewhere else.
LICENSE_API_ENV_VAR: Final[str] = "BINTEL_LICENSE_API"
TELEMETRY_API_ENV_VAR: Final[str] = "BINTEL_TELEMETRY_API"
MANIFEST_URL_ENV_VAR: Final[str] = "BINTEL_MANIFEST_URL"

#: Values considered "no data". Presented to the user as ``Unknown``.
UNKNOWN_DISPLAY: Final[str] = "Unknown"

#: Environment variable that forces portable mode on.
PORTABLE_ENV_VAR: Final[str] = "BINTEL_PORTABLE"
#: Environment variable that overrides the whole data directory.
DATA_DIR_ENV_VAR: Final[str] = "BINTEL_DATA_DIR"
#: Marker file that, when placed next to the executable, enables portable mode.
PORTABLE_MARKER: Final[str] = "bintel-portable.txt"
