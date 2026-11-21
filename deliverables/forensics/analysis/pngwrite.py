"""Tiny stdlib-only 8-bit RGBA PNG writer (filter type 0 rows, zlib)."""
import struct
import zlib


def write_png(path, width, height, rgba_rows):
    """rgba_rows: iterable of bytes/bytearray rows, each width*4 RGBA bytes."""
    raw = bytearray()
    for row in rgba_rows:
        raw.append(0)
        raw.extend(row)
    compressed = zlib.compress(bytes(raw), 9)

    def chunk(tag, payload):
        out = struct.pack(">I", len(payload)) + tag + payload
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return out + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(data)
