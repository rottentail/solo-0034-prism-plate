"""Minimal dependency-free PNG reader/writer for 8-bit RGBA images.

Go's image/png emits non-interlaced 8-bit truecolour+alpha (colour type 6) for
the NRGBA canvases used by prism-plate. The reader also accepts colour type 2
(RGB) and type 6 with all five scanline filters, which is what the service and
derived contact sheets produce.
"""
from __future__ import annotations

import pathlib
import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PNGError(ValueError):
    pass


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(raw: bytes, width: int, height: int, bpp: int, stride: int) -> bytearray:
    out = bytearray(height * stride)
    pos = 0
    prior = bytearray(stride)
    for y in range(height):
        if pos >= len(raw):
            raise PNGError("truncated scanline data")
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        if len(line) != stride:
            raise PNGError("truncated scanline")
        pos += stride
        if ftype == 0:
            pass
        elif ftype == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prior[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prior[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upleft = prior[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prior[i], upleft)) & 0xFF
        else:
            raise PNGError(f"bad filter type {ftype}")
        out[y * stride:(y + 1) * stride] = line
        prior = line
    return out


def read(path_or_bytes) -> tuple[int, int, bytes]:
    """Return (width, height, rgba bytes) for an 8-bit PNG."""
    data = path_or_bytes
    if isinstance(path_or_bytes, (str, pathlib.Path)):
        with open(path_or_bytes, "rb") as fh:
            data = fh.read()
    if not data.startswith(PNG_SIGNATURE):
        raise PNGError("not a PNG")
    pos = 8
    width = height = depth = ctype = interlace = None
    idat = bytearray()
    palette = None
    trns = None
    while pos < len(data):
        if pos + 8 > len(data):
            raise PNGError("truncated chunk header")
        length, ctag = struct.unpack(">I4s", data[pos:pos + 8])
        pos += 8
        payload = data[pos:pos + length]
        if len(payload) != length:
            raise PNGError("truncated chunk")
        pos += length
        crc = struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 4
        if zlib.crc32(ctag + payload) & 0xFFFFFFFF != crc:
            raise PNGError(f"bad CRC in {ctag!r}")
        if ctag == b"IHDR":
            width, height, depth, ctype, comp, filt, interlace = struct.unpack(">IIBBBBB", payload)
            if comp != 0 or filt != 0 or interlace != 0:
                raise PNGError("unsupported compression/filter/interlace")
        elif ctag == b"IDAT":
            idat.extend(payload)
        elif ctag == b"PLTE":
            palette = payload
        elif ctag == b"tRNS":
            trns = payload
        elif ctag == b"IEND":
            break
    if width is None or not idat:
        raise PNGError("missing IHDR/IDAT")
    if depth != 8:
        raise PNGError(f"unsupported bit depth {depth}")
    if ctype == 6:
        channels, bpp = 4, 4
    elif ctype == 2:
        channels, bpp = 3, 3
    elif ctype == 0:
        channels, bpp = 1, 1
    elif ctype == 3:
        channels, bpp = 1, 1
        if palette is None:
            raise PNGError("indexed PNG without palette")
    else:
        raise PNGError(f"unsupported colour type {ctype}")
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    planes = _unfilter(raw, width, height, bpp, stride)
    rgba = bytearray(width * height * 4)
    if ctype == 6:
        rgba[:] = planes
    elif ctype == 2:
        for i in range(width * height):
            rgba[i * 4:i * 4 + 4] = planes[i * 3:i * 3 + 3] + b"\xff"
    elif ctype == 0:
        for i in range(width * height):
            v = planes[i]
            rgba[i * 4:i * 4 + 4] = bytes((v, v, v, 255))
    else:  # indexed
        for i in range(width * height):
            idx = planes[i]
            r, g, b = palette[idx * 3:idx * 3 + 3]
            a = trns[idx] if trns is not None and idx < len(trns) else 255
            rgba[i * 4:i * 4 + 4] = bytes((r, g, b, a))
    return width, height, bytes(rgba)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)


def write(path, width: int, height: int, rgba: bytes) -> None:
    """Write an 8-bit RGBA PNG with filter type 0 (deterministic)."""
    if len(rgba) != width * height * 4:
        raise ValueError("rgba length mismatch")
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw.extend(rgba[y * stride:(y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    body = PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(body)
