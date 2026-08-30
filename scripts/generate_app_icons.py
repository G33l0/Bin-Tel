#!/usr/bin/env python3
"""Rasterise the Bin-Tel brand mark into platform application icons.

Produces, under ``assets/icons/app``:

* ``bintel-<size>.png`` for Linux desktop entries and the Qt window icon,
* ``bintel.ico``  — multi-resolution Windows icon (PNG-compressed entries),
* ``bintel.icns`` — macOS icon bundle.

Both container formats are written directly, so no platform tooling
(``iconutil``, ImageMagick) is required and the script runs on any OS.

    python scripts/generate_app_icons.py
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PNG_SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
#: At or below this size the simplified mark is used instead of the full one.
SMALL_SIZE_LIMIT = 32
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
#: macOS ICNS entries: (OSType, pixel size). ``ic0x`` types carry PNG data.
ICNS_ENTRIES = (
    (b"icp4", 16),
    (b"icp5", 32),
    (b"ic07", 128),
    (b"ic08", 256),
    (b"ic09", 512),
    (b"ic10", 1024),
)


def _render(source: str, size: int) -> bytes:
    """Render the SVG to PNG bytes at *size* using Qt's SVG renderer."""
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def write_ico(pngs: dict[int, bytes], destination: Path) -> Path:
    """Write a PNG-compressed multi-resolution .ico file."""
    entries = [(size, pngs[size]) for size in ICO_SIZES if size in pngs]
    header = struct.pack("<HHH", 0, 1, len(entries))
    directory = b""
    offset = len(header) + 16 * len(entries)
    payload = b""
    for size, data in entries:
        dimension = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset
        )
        payload += data
        offset += len(data)
    destination.write_bytes(header + directory + payload)
    return destination


def write_icns(pngs: dict[int, bytes], destination: Path) -> Path:
    """Write a macOS .icns bundle from PNG entries."""
    chunks = b""
    for ostype, size in ICNS_ENTRIES:
        data = pngs.get(size)
        if data is None:
            continue
        chunks += ostype + struct.pack(">I", len(data) + 8) + data
    destination.write_bytes(b"icns" + struct.pack(">I", len(chunks) + 8) + chunks)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Bin-Tel application icons.")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "assets" / "branding" / "bintel-mark.svg",
    )
    parser.add_argument(
        "--small-source",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "assets"
        / "branding"
        / "bintel-mark-small.svg",
        help="simplified mark used at %d px and below" % SMALL_SIZE_LIMIT,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "assets" / "icons" / "app",
    )
    args = parser.parse_args(argv)

    if not args.source.exists():
        print(f"error: brand mark not found at {args.source}", file=sys.stderr)
        return 1

    # A QGuiApplication is required before any QImage/QPainter work.
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QGuiApplication

    application = QGuiApplication.instance() or QGuiApplication([])

    source = args.source.read_text(encoding="utf-8")
    # Below 32 px the orbital arcs turn to noise, so a simplified mark with
    # thicker rows is used instead. Same identity, legible silhouette.
    small_source = (
        args.small_source.read_text(encoding="utf-8")
        if args.small_source.exists()
        else source
    )
    args.output.mkdir(parents=True, exist_ok=True)

    pngs: dict[int, bytes] = {}
    for size in PNG_SIZES:
        data = _render(small_source if size <= SMALL_SIZE_LIMIT else source, size)
        pngs[size] = data
        path = args.output / f"bintel-{size}.png"
        path.write_bytes(data)
        print(f"  {path.relative_to(args.output.parents[2])}  ({len(data):,} bytes)")

    ico = write_ico(pngs, args.output / "bintel.ico")
    icns = write_icns(pngs, args.output / "bintel.icns")
    print(f"  {ico.name}   ({ico.stat().st_size:,} bytes)")
    print(f"  {icns.name}  ({icns.stat().st_size:,} bytes)")
    del application
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
