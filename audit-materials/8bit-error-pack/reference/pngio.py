"""Minimal decoder for the 8-bit non-interlaced PNG files under audit.

Only the chunks emitted by Go's image/png encoder for NRGBA images are
accepted: IHDR, IDAT (zlib), IEND and ancillary tEXt/gAMA/cHRM/sRGB/iCCP,
which are skipped.  Colour types 2 (RGB) and 6 (RGBA) are supported so that
both opaque and alpha-bearing outputs can be read.  Implementation uses
only the Python standard library (struct, zlib).
"""

import struct
import zlib


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def read_png(path: str) -> dict:
    with open(path, "rb") as fh:
        blob = fh.read()
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos = 8
    width = height = bit_depth = color_type = None
    idat = bytearray()
    while pos < len(blob):
        (length,) = struct.unpack(">I", blob[pos:pos + 4])
        kind = blob[pos + 4:pos + 8]
        data = blob[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, comp, filt, interlace = \
                struct.unpack(">IIBBBBB", data)
            if bit_depth not in (8, 16) or interlace != 0:
                raise ValueError("only 8/16-bit non-interlaced PNG supported")
            if color_type not in (2, 6):
                raise ValueError("only RGB/RGBA colour types are supported")
        elif kind == b"IDAT":
            idat.extend(data)
        elif kind == b"IEND":
            break
    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(bytes(idat))
    sample_bytes = bit_depth // 8
    stride = width * channels * sample_bytes
    rows = []
    prev = bytearray(stride)
    cursor = 0
    for _ in range(height):
        ftype = raw[cursor]
        cursor += 1
        line = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        bpp = channels * sample_bytes
        for i in range(stride):
            left = line[i - bpp] if i >= bpp else 0
            up = prev[i]
            up_left = prev[i - bpp] if i >= bpp else 0
            if ftype == 1:
                line[i] = (line[i] + left) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + up) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif ftype == 4:
                line[i] = (line[i] + _paeth(left, up, up_left)) & 0xFF
            elif ftype != 0:
                raise ValueError(f"unsupported filter {ftype}")
        rows.append(bytes(line))
        prev = line
    pixels = []
    for y in range(height):
        row = []
        for x in range(width):
            base = x * channels * sample_bytes
            sample = []
            for k in range(channels):
                off = base + k * sample_bytes
                if sample_bytes == 2:
                    sample.append(struct.unpack(
                        ">H", rows[y][off:off + 2])[0])
                else:
                    sample.append(rows[y][off])
            row.append(tuple(sample))
        pixels.append(row)
    return {"width": width, "height": height,
            "bit_depth": bit_depth,
            "color_type": color_type, "pixels": pixels}
