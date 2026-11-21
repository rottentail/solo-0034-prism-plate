"""Deterministic 16-bit-per-sample PNG writer (colour type 6, bit depth 16).

Used only to produce a control artifact that keeps the same scene and
compositing but removes the 8-bit lattice: if a pixel difference shrinks to
zero in 16-bit output, it was caused by 8-bit encoding, not by design or
synthesis.  Standard library only (struct, zlib).
"""

import struct
import zlib


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def write_rgba16_png(path: str, width: int, height: int,
                     pixels: list) -> None:
    """pixels: rows of (r16, g16, b16, a16) tuples."""
    ihdr = struct.pack(">IIBBBBB", width, height, 16, 6, 0, 0, 0)
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter type 0 (none)
        for r16, g16, b16, a16 in row:
            raw.extend(struct.pack(">HHHH", r16, g16, b16, a16))
    blob = (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(blob)


def encode16(value: float) -> int:
    """Round a [0,1] value to the 16-bit lattice, half away from zero."""
    if value <= 0.0:
        return 0
    if value >= 1.0:
        return 65535
    return int(value * 65535.0 + 0.5)
