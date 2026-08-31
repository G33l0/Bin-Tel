"""Decompression of downloaded database packages.

A published package may be shipped raw or compressed. Decompression streams
through a fixed buffer, so a multi-gigabyte database never has to fit in
memory, and the caller can report progress and cancel part-way through.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.errors import DownloadError, OperationCancelled
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_CHUNK = 1 << 20  # 1 MiB

#: Compression identifiers a manifest may declare.
SUPPORTED = ("none", "raw", "gzip", "gz", "xz", "lzma", "bz2", "bzip2", "zip", "zstd")

#: Aliases normalised to a canonical name.
_ALIASES = {
    "": "none",
    "raw": "none",
    "gz": "gzip",
    "lzma": "xz",
    "bzip2": "bz2",
}


class _Opener(Protocol):
    def __call__(self, path: Path) -> BinaryIO: ...  # pragma: no cover - typing only


def normalise(compression: str | None) -> str:
    name = (compression or "none").strip().lower()
    return _ALIASES.get(name, name)


def is_supported(compression: str | None) -> bool:
    name = normalise(compression)
    if name == "zstd":
        # Not in the standard library; only claim support when a codec exists.
        return _zstd_decompressor() is not None
    return name in {"none", "gzip", "xz", "bz2", "zip"}


def _zstd_decompressor():
    """Return a zstd module if one is installed, else ``None``."""
    try:  # Python 3.14+ ships compression.zstd
        from compression import zstd  # type: ignore[import-not-found]

        return zstd
    except ImportError:
        pass
    try:  # pragma: no cover - optional third-party
        import zstandard  # type: ignore[import-not-found]

        return zstandard
    except ImportError:
        return None


def suffix_for(compression: str | None) -> str:
    return {
        "none": "",
        "gzip": ".gz",
        "xz": ".xz",
        "bz2": ".bz2",
        "zip": ".zip",
        "zstd": ".zst",
    }.get(normalise(compression), "")


def decompress(
    source: Path,
    destination: Path,
    compression: str | None,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Expand *source* into *destination*.

    ``none`` is not a special case for the caller: the file is still produced
    at *destination*, so the update pipeline has one code path either way.
    """
    name = normalise(compression)
    if not is_supported(name):
        raise DownloadError(
            f"This database package uses {name!r} compression, which this version of "
            "Bin-Tel cannot expand. Updating the application will add support.",
            detail=f"Unsupported compression: {compression!r}",
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    if name == "none":
        if source != destination:
            source.replace(destination)
        return destination

    compressed_size = source.stat().st_size
    logger.info(
        "Expanding database package",
        extra={"context": {"compression": name, "compressed_bytes": compressed_size}},
    )

    staging = destination.with_suffix(destination.suffix + ".expanding")
    try:
        with _open_compressed(source, name) as reader, staging.open("wb") as writer:
            written = 0
            while chunk := reader.read(_CHUNK):
                if cancelled is not None and cancelled():
                    raise OperationCancelled("Extraction was cancelled.")
                writer.write(chunk)
                written += len(chunk)
                if progress is not None:
                    # The expanded size is unknown up front, so progress is
                    # reported against the compressed input as a proxy.
                    progress(min(written, compressed_size * 4), compressed_size * 4)
        staging.replace(destination)
    except OperationCancelled:
        staging.unlink(missing_ok=True)
        raise
    except (OSError, EOFError, lzma.LZMAError, zipfile.BadZipFile) as exc:
        staging.unlink(missing_ok=True)
        raise DownloadError(
            "The downloaded database package could not be expanded. It is most likely "
            "incomplete or corrupted.",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    source.unlink(missing_ok=True)
    logger.info(
        "Package expanded",
        extra={"context": {"expanded_bytes": destination.stat().st_size}},
    )
    return destination


def _open_compressed(source: Path, name: str) -> BinaryIO:
    if name == "gzip":
        return gzip.open(source, "rb")  # type: ignore[return-value]
    if name == "xz":
        return lzma.open(source, "rb")  # type: ignore[return-value]
    if name == "bz2":
        return bz2.open(source, "rb")  # type: ignore[return-value]
    if name == "zip":
        archive = zipfile.ZipFile(source)
        members = [item for item in archive.namelist() if not item.endswith("/")]
        if not members:
            archive.close()
            raise DownloadError("The database archive is empty.")
        # Prefer an obvious SQLite member when the archive holds several files.
        chosen = next(
            (item for item in members if item.lower().endswith((".sqlite", ".sqlite3", ".db"))),
            members[0],
        )
        return archive.open(chosen)  # type: ignore[return-value]
    if name == "zstd":  # pragma: no cover - depends on an optional codec
        module = _zstd_decompressor()
        if module is None:
            raise DownloadError("This build cannot expand zstd packages.")
        if hasattr(module, "ZstdFile"):
            return module.ZstdFile(source, "rb")  # type: ignore[no-any-return]
        return module.open(source, "rb")  # type: ignore[no-any-return]
    raise DownloadError(f"Unsupported compression: {name}")  # pragma: no cover


def compress(source: Path, destination: Path, compression: str | None) -> Path:
    """Compress *source* into *destination* — used by the release pipeline."""
    name = normalise(compression)
    if name == "none":
        if source != destination:
            import shutil

            shutil.copy2(source, destination)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    openers: dict[str, _Opener] = {
        "gzip": lambda path: gzip.open(path, "wb", compresslevel=9),  # type: ignore[return-value]
        "xz": lambda path: lzma.open(path, "wb", preset=6),  # type: ignore[return-value]
        "bz2": lambda path: bz2.open(path, "wb", compresslevel=9),  # type: ignore[return-value]
    }
    opener = openers.get(name)
    if opener is None:
        raise DownloadError(f"Cannot produce {name!r} packages.")
    with source.open("rb") as reader, opener(destination) as writer:
        while chunk := reader.read(_CHUNK):
            writer.write(chunk)
    return destination
