"""Cryptographic integrity helpers used by the download/update pipeline."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

#: Streaming read size — large enough to be fast, small enough to stay
#: responsive when the caller reports progress.
_READ_SIZE = 1 << 20  # 1 MiB

SUPPORTED_ALGORITHMS = ("sha256", "sha512", "sha384", "blake2b", "md5")


def file_checksum(
    path: Path,
    algorithm: str = "sha256",
    *,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> str:
    """Hex digest of *path*, computed in a streaming fashion.

    The whole file is never loaded into memory, so a multi-gigabyte database
    package can be verified on a modest machine. *progress* receives
    ``(bytes_done, total_bytes)``; *cancelled* is polled between chunks.
    """
    algorithm = algorithm.lower().strip()
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise ValueError(f"Unsupported checksum algorithm: {algorithm}")
    digest = hashlib.new(algorithm)
    total = path.stat().st_size
    done = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_SIZE):
            if cancelled is not None and cancelled():
                raise InterruptedError("Checksum verification cancelled")
            digest.update(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done, total)
    return digest.hexdigest()


def verify_checksum(path: Path, expected: str, algorithm: str = "sha256", **kwargs: object) -> bool:
    """Constant-time-ish comparison of a file's digest against *expected*."""
    if not expected:
        return False
    import hmac

    actual = file_checksum(path, algorithm, **kwargs)  # type: ignore[arg-type]
    return hmac.compare_digest(actual.lower(), expected.strip().lower())


def parse_checksum(value: str) -> tuple[str, str]:
    """Split ``"sha256:abcd..."`` into ``("sha256", "abcd...")``.

    A bare digest is assumed to be SHA-256, the default for Bin-Tel manifests.
    """
    value = (value or "").strip()
    if ":" in value:
        algorithm, _, digest = value.partition(":")
        return algorithm.strip().lower(), digest.strip().lower()
    return "sha256", value.lower()


def text_digest(text: str, algorithm: str = "sha256") -> str:
    return hashlib.new(algorithm, text.encode("utf-8")).hexdigest()
