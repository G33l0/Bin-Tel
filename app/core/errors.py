"""Application error hierarchy.

Every error raised deliberately by Bin-Tel carries a *user message* (safe to
show in a dialog) alongside optional technical *detail* (written to the log,
never rendered as a raw traceback). :class:`BinTelError` also records whether
the failing operation is worth retrying, which drives the ``Retry`` button in
:class:`app.ui.dialogs.error_dialog.ErrorDialog`.
"""

from __future__ import annotations


class BinTelError(Exception):
    """Base class for all deliberately raised Bin-Tel errors."""

    #: Short, human-readable headline shown in the error dialog.
    title: str = "Operation failed"
    #: Whether offering a "Retry" button makes sense for this failure.
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        detail: str | None = None,
        title: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        if title is not None:
            self.title = title
        if retryable is not None:
            self.retryable = retryable

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class ConfigurationError(BinTelError):
    title = "Configuration problem"


class DatabaseError(BinTelError):
    title = "Database error"


class DatabaseMissingError(DatabaseError):
    title = "Database not installed"


class DatabaseCorruptError(DatabaseError):
    title = "Database failed verification"


class SchemaVersionError(DatabaseError):
    title = "Unsupported database version"


class NetworkError(BinTelError):
    title = "Network problem"
    retryable = True


class OfflineError(NetworkError):
    title = "No internet connection"


class ManifestError(NetworkError):
    title = "Update information unavailable"


class DownloadError(NetworkError):
    title = "Download failed"


class ChecksumMismatchError(DownloadError):
    title = "Download integrity check failed"
    retryable = True


class UpdateError(BinTelError):
    title = "Update failed"
    retryable = True


class ImportError_(BinTelError):
    """Named with a trailing underscore so it never shadows the builtin."""

    title = "Import failed"


class ValidationError(BinTelError):
    title = "Invalid input"


class ExportError(BinTelError):
    title = "Export failed"


class OperationCancelled(BinTelError):
    """Raised when the user cancels a long-running operation."""

    title = "Cancelled"


def friendly_message(exc: BaseException) -> str:
    """Translate any exception into a sentence safe to show a normal user."""
    if isinstance(exc, BinTelError):
        return exc.message
    mapping: dict[type[BaseException], str] = {
        FileNotFoundError: "A required file could not be found.",
        PermissionError: "Bin-Tel does not have permission to access that location.",
        IsADirectoryError: "A file was expected but a folder was provided.",
        TimeoutError: "The operation took too long and was stopped.",
        ConnectionError: "Bin-Tel could not reach the update server.",
        OSError: "The operating system reported a problem completing the request.",
    }
    for exc_type, text in mapping.items():
        if isinstance(exc, exc_type):
            return text
    return "An unexpected problem stopped this operation from completing."


def friendly_title(exc: BaseException) -> str:
    """Headline for the error dialog."""
    return exc.title if isinstance(exc, BinTelError) else "Operation failed"


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, BinTelError):
        return exc.retryable
    return isinstance(exc, ConnectionError | TimeoutError)
