"""User configuration: typed, validated, and persisted as JSON.

Settings are modelled with Pydantic so that a hand-edited or partially
corrupted ``settings.json`` can never crash startup — unknown keys are dropped
and invalid values fall back to their defaults. Persistence is atomic (write to
a temporary file, then replace) so an interrupted save cannot truncate the file.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.constants import DEFAULT_MANIFEST_URL
from app.core.paths import AppPaths, get_paths


class UpdateFrequency(StrEnum):
    """How often Bin-Tel checks the distribution server for a new database."""

    NEVER = "never"
    DAILY = "daily"
    EVERY_3_DAYS = "every_3_days"
    WEEKLY = "weekly"
    EVERY_2_WEEKS = "every_2_weeks"
    MONTHLY = "monthly"

    @property
    def label(self) -> str:
        return {
            UpdateFrequency.NEVER: "Never",
            UpdateFrequency.DAILY: "Daily",
            UpdateFrequency.EVERY_3_DAYS: "Every 3 days",
            UpdateFrequency.WEEKLY: "Weekly",
            UpdateFrequency.EVERY_2_WEEKS: "Every 2 weeks",
            UpdateFrequency.MONTHLY: "Monthly",
        }[self]

    @property
    def days(self) -> int | None:
        """Interval in days, or ``None`` when automatic checking is disabled."""
        return {
            UpdateFrequency.NEVER: None,
            UpdateFrequency.DAILY: 1,
            UpdateFrequency.EVERY_3_DAYS: 3,
            UpdateFrequency.WEEKLY: 7,
            UpdateFrequency.EVERY_2_WEEKS: 14,
            UpdateFrequency.MONTHLY: 30,
        }[self]


class LookupMode(StrEnum):
    BIN = "bin"
    BANK = "bank"

    @property
    def label(self) -> str:
        return "BIN Lookup" if self is LookupMode.BIN else "Bank Lookup"


class SidebarBehavior(StrEnum):
    EXPANDED = "expanded"
    COLLAPSED = "collapsed"
    REMEMBER = "remember"

    @property
    def label(self) -> str:
        return {
            SidebarBehavior.EXPANDED: "Always expanded",
            SidebarBehavior.COLLAPSED: "Always collapsed",
            SidebarBehavior.REMEMBER: "Remember last state",
        }[self]


class SearchBehavior(StrEnum):
    AS_YOU_TYPE = "as_you_type"
    ON_ENTER = "on_enter"

    @property
    def label(self) -> str:
        return "Search as you type" if self is SearchBehavior.AS_YOU_TYPE else "Search on Enter"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class _Section(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True, use_enum_values=False)


class GeneralSettings(_Section):
    start_with_system: bool = False
    minimize_to_tray: bool = False
    confirm_before_closing: bool = False
    remember_window_size: bool = True
    remember_window_position: bool = True


class DatabaseSettings(_Section):
    #: Empty means "use the platform default location".
    database_directory: str = ""
    manifest_url: str = DEFAULT_MANIFEST_URL
    automatic_updates: bool = True
    update_frequency: UpdateFrequency = UpdateFrequency.WEEKLY
    download_automatically: bool = False
    install_automatically: bool = False
    backup_before_update: bool = True
    max_backups: int = Field(default=3, ge=1, le=20)

    @field_validator("manifest_url")
    @classmethod
    def _validate_manifest_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return DEFAULT_MANIFEST_URL
        allowed = ("https://", "http://", "file://")
        if not value.startswith(allowed):
            raise ValueError("Manifest URL must start with https://, http:// or file://")
        return value


class AppearanceSettings(_Section):
    theme: str = "midnight"
    ui_scale: int = Field(default=100, ge=75, le=200)
    compact_mode: bool = False
    sidebar_behavior: SidebarBehavior = SidebarBehavior.REMEMBER


class SearchSettings(_Section):
    default_lookup: LookupMode = LookupMode.BIN
    behavior: SearchBehavior = SearchBehavior.ON_ENTER
    results_per_page: int = Field(default=50, ge=10, le=500)
    search_delay_ms: int = Field(default=250, ge=0, le=2000)
    max_history: int = Field(default=25, ge=0, le=200)


class AdvancedSettings(_Section):
    log_level: LogLevel = LogLevel.INFO
    log_retention_days: int = Field(default=14, ge=1, le=365)
    verify_on_startup: bool = True
    query_timeout_seconds: int = Field(default=30, ge=1, le=300)


class Settings(BaseModel):
    """The complete, persisted user configuration."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    version: int = 1
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    appearance: AppearanceSettings = Field(default_factory=AppearanceSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    advanced: AdvancedSettings = Field(default_factory=AdvancedSettings)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=False)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Self:
        """Build settings from an untrusted mapping, healing what it can.

        A bad *value* only resets that value; a bad *section* only resets that
        section. The user never loses their whole configuration because one
        field went out of range.
        """
        try:
            return cls.model_validate(data)
        except ValidationError:
            pass
        healed = cls()
        section_types: dict[str, type[_Section]] = {
            "general": GeneralSettings,
            "database": DatabaseSettings,
            "appearance": AppearanceSettings,
            "search": SearchSettings,
            "advanced": AdvancedSettings,
        }
        for name, section_cls in section_types.items():
            raw = data.get(name)
            if not isinstance(raw, dict):
                continue
            try:
                setattr(healed, name, section_cls.model_validate(raw))
                continue
            except ValidationError:
                pass
            # Fall back to field-by-field recovery.
            section = section_cls()
            for key, value in raw.items():
                if key not in section_cls.model_fields:
                    continue
                try:
                    setattr(section, key, value)
                except (ValidationError, ValueError):
                    continue
            setattr(healed, name, section)
        return healed


class AppState(BaseModel):
    """Runtime state that is remembered but is not a user *preference*."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    first_run_completed: bool = False
    window_geometry: str = ""
    window_state: str = ""
    sidebar_collapsed: bool = False
    last_update_check: datetime | None = None
    last_update_installed: datetime | None = None
    last_verification: datetime | None = None
    last_known_remote_version: str = ""
    search_history: list[str] = Field(default_factory=list)
    active_page: str = "dashboard"

    def record_search(self, term: str, limit: int) -> None:
        term = term.strip()
        if not term or limit <= 0:
            return
        history = [item for item in self.search_history if item.lower() != term.lower()]
        history.insert(0, term)
        self.search_history = history[:limit]


def _atomic_write(path: Path, text: str) -> None:
    """Write *text* to *path* without ever leaving a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class ConfigManager:
    """Loads, mutates and persists :class:`Settings` and :class:`AppState`."""

    def __init__(self, paths: AppPaths | None = None) -> None:
        self._paths = paths or get_paths()
        self._settings = Settings()
        self._state = AppState()
        self._loaded = False

    # -- properties -------------------------------------------------------
    @property
    def paths(self) -> AppPaths:
        return self._paths

    @property
    def is_loaded(self) -> bool:
        """True once :meth:`load` has read from disk."""
        return self._loaded

    @property
    def settings(self) -> Settings:
        if not self._loaded:
            self.load()
        return self._settings

    @property
    def state(self) -> AppState:
        if not self._loaded:
            self.load()
        return self._state

    # -- persistence ------------------------------------------------------
    def load(self) -> Settings:
        self._settings = self._read_model(self._paths.settings_file, Settings)
        self._state = self._read_model(self._paths.state_file, AppState)
        self._loaded = True
        return self._settings

    def _read_model(self, path: Path, model: type[BaseModel]) -> Any:
        if not path.exists():
            return model()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return model()
        if not isinstance(raw, dict):
            return model()
        if model is Settings:
            return Settings.from_mapping(raw)
        try:
            return model.model_validate(raw)
        except ValidationError:
            return model()

    def save(self) -> None:
        self.save_settings()
        self.save_state()

    def save_settings(self) -> None:
        _atomic_write(self._paths.settings_file, self._settings.to_json())

    def save_state(self) -> None:
        payload = json.dumps(self._state.model_dump(mode="json"), indent=2)
        _atomic_write(self._paths.state_file, payload)

    def reset_settings(self) -> Settings:
        """Restore factory defaults (leaves :class:`AppState` untouched)."""
        self._settings = Settings()
        self.save_settings()
        return self._settings

    # -- derived values ---------------------------------------------------
    def database_path(self) -> Path:
        """Active database file, honouring a user-chosen database directory."""
        configured = self.settings.database.database_directory.strip()
        if configured:
            directory = Path(configured).expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            return directory / self._paths.database_file.name
        return self._paths.database_file

    def mark_update_checked(self, remote_version: str | None = None) -> None:
        self.state.last_update_check = datetime.now(UTC)
        if remote_version:
            self.state.last_known_remote_version = remote_version
        self.save_state()

    def next_update_due(self) -> datetime | None:
        """When the next automatic check is scheduled, if any."""
        db = self.settings.database
        if not db.automatic_updates:
            return None
        days = db.update_frequency.days
        if days is None:
            return None
        last = self.state.last_update_check
        if last is None:
            return datetime.now(UTC)
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        from datetime import timedelta

        return last + timedelta(days=days)

    def is_update_check_due(self, now: datetime | None = None) -> bool:
        due = self.next_update_due()
        if due is None:
            return False
        return (now or datetime.now(UTC)) >= due
