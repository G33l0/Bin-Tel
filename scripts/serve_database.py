#!/usr/bin/env python3
"""Serve a Bin-Tel database package over HTTP for development and testing.

This is the smallest possible stand-in for the production distribution server:
it exposes ``database-manifest.json`` and the package beside it, with byte-range
support so the client's resume logic can be exercised.

    python scripts/build_sample_database.py --output dist/database
    python scripts/serve_database.py --directory dist/database
    python -m app.main --manifest-url http://127.0.0.1:8770/database-manifest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class DatabaseRequestHandler(SimpleHTTPRequestHandler):
    """Static file handler with Range support and quiet, useful logging."""

    def end_headers(self) -> None:
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_head(self):  # type: ignore[override]
        """Honour a ``Range`` request so download resumption can be tested."""
        range_header = self.headers.get("Range")
        if not range_header or not range_header.startswith("bytes="):
            return super().send_head()

        path = self.translate_path(self.path)
        file = Path(path)
        if not file.is_file():
            return super().send_head()

        size = file.stat().st_size
        raw = range_header.removeprefix("bytes=")
        start_text, _, end_text = raw.partition("-")
        try:
            start = int(start_text) if start_text else 0
            end = int(end_text) if end_text else size - 1
        except ValueError:
            return super().send_head()
        if start >= size:
            self.send_error(416, "Requested range not satisfiable")
            return None
        end = min(end, size - 1)

        handle = file.open("rb")
        handle.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        return handle

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        sys.stderr.write(f"  {self.address_string()} — {format % args}\n")


def describe(directory: Path) -> None:
    manifest = directory / "database-manifest.json"
    if not manifest.exists():
        print(
            f"warning: {manifest} does not exist. Build one with:\n"
            f"  python scripts/build_sample_database.py --output {directory}\n",
            file=sys.stderr,
        )
        return
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"warning: manifest is not valid JSON ({exc})", file=sys.stderr)
        return
    print(
        f"  serving database {data.get('version', '?')} · "
        f"{data.get('record_count', 0):,} records · "
        f"{int(data.get('database_size', 0)) / (1024 * 1024):.1f} MB"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve a Bin-Tel database package.")
    parser.add_argument("--directory", type=Path, default=Path("dist/database"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args(argv)

    directory = args.directory.resolve()
    if not directory.exists():
        print(f"error: {directory} does not exist.", file=sys.stderr)
        return 1

    handler = partial(DatabaseRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/database-manifest.json"

    print(f"Bin-Tel database server on http://{args.host}:{args.port}/")
    print(f"  directory: {directory}")
    describe(directory)
    print(f"\nPoint the application at it with:\n  python -m app.main --manifest-url {url}\n")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
