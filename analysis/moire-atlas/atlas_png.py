#!/usr/bin/env python3
"""Self-contained PNG decoder (color-type 2/6, 8-bit, filters 0..4, zlib).

Independent of the prismplate code base: used only to read the actual
service-produced PNG artifacts for measurement.
"""
import struct
import zlib


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def read_png(path):
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    width = height = bit_depth = color_type = None
    idat = bytearray()
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", chunk)
        elif ctype == b"IDAT":
            idat.extend(chunk)
        elif ctype == b"IEND":
            break
    if bit_depth != 8 or color_type not in (2, 6):
        raise ValueError(f"unsupported bit depth/type {bit_depth}/{color_type}")
    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    pixels = bytearray()
    prev = bytearray(stride)
    i = 0
    for _ in range(height):
        filt = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            if filt == 1:
                line[x] = (line[x] + a) & 255
            elif filt == 2:
                line[x] = (line[x] + b) & 255
            elif filt == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif filt == 4:
                line[x] = (line[x] + _paeth(a, b, c)) & 255
            elif filt != 0:
                raise ValueError("bad filter")
        pixels.extend(line)
        prev = line
    rows = []
    for y in range(height):
        row = []
        base = y * stride
        for x in range(width):
            o = base + x * channels
            row.append(tuple(pixels[o:o + channels]))
        rows.append(row)
    return width, height, rows


def luminance(pixel):
    """sRGB code value luminance proxy (0..255); constant across comparison."""
    r, g, b = pixel[0], pixel[1], pixel[2]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
