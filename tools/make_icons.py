#!/usr/bin/env python3
"""Generate the home screen icons in web/icons/ (standard library only).

    python3 tools/make_icons.py

Draws a glowing amber orb on the app's dark background, matching the brand dot in the
header. "maskable" icons keep the orb inside the safe zone so Android can crop them to
any shape.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "web" / "icons"
BG = (10, 11, 16)
ORB = (255, 154, 77)
HIGHLIGHT = (255, 236, 214)


def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def render(size: int, orb_scale: float, rounded: bool) -> bytes:
    c = size / 2
    r_orb = size * orb_scale / 2
    corner = size * 0.22 if rounded else 0
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            alpha = 1.0
            if corner:  # rounded square, anti-aliased edge
                dx = max(corner - px, 0, px - (size - corner))
                dy = max(corner - py, 0, py - (size - corner))
                if dx and dy:
                    alpha = max(0.0, min(1.0, corner - math.hypot(dx, dy) + 0.5))
            d = math.hypot(px - c, py - c)
            col = BG
            glow = max(0.0, 1 - d / (r_orb * 2.1)) ** 2.2          # soft light around the orb
            col = _mix(col, ORB, glow * 0.55)
            if d < r_orb + 1:
                edge = max(0.0, min(1.0, r_orb - d + 0.5))           # anti-aliased orb edge
                hx, hy = c - r_orb * 0.32, c - r_orb * 0.34           # highlight, top left
                h = max(0.0, 1 - math.hypot(px - hx, py - hy) / (r_orb * 0.75)) ** 1.6
                body = _mix(ORB, HIGHLIGHT, h * 0.75)
                shade = 1 - 0.18 * max(0.0, (py - c) / r_orb)        # a little darker at the bottom
                body = tuple(v * shade for v in body)
                col = _mix(col, body, edge)
            row += bytes(int(max(0, min(255, round(v)))) for v in col) + bytes([int(round(alpha * 255))])
        rows.append(bytes(row))
    return _png(size, size, rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    targets = {
        "icon-192.png": (192, 0.46, True),
        "icon-512.png": (512, 0.46, True),
        "maskable-512.png": (512, 0.38, False),   # full bleed, orb inside the safe zone
        "apple-touch-icon.png": (180, 0.46, False),  # iOS rounds the corners itself
    }
    for name, (size, scale, rounded) in targets.items():
        (OUT / name).write_bytes(render(size, scale, rounded))
        print(f"wrote web/icons/{name} ({size}x{size})")


if __name__ == "__main__":
    main()
