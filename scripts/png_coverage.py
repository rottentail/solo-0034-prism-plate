#!/usr/bin/env python3
"""Decode the service-produced PNG with the stdlib only and summarize the
coverage of the blue quad.  No third-party imaging library is available in
the verification environment, so the PNG chunks/IDAT are parsed directly.

Run:  python3 -I scripts/png_coverage.py investigation/results/repro.png
"""

import struct
import sys
import zlib


def read_png(path):
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos = 8
    width = height = bit = color = None
    idat = b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, bit, color, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", chunk)
            assert bit == 8 and interlace == 0
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break
        pos += 12 + length
    channels = {2: 3, 6: 4}[color]
    raw = zlib.decompress(idat)
    stride = width * channels
    rows = []
    prev = bytearray(stride)
    i = 0
    for _ in range(height):
        f = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
            elif f != 0:
                raise AssertionError(f"unknown filter {f}")
        rows.append(bytes(line))
        prev = line
    return width, height, channels, rows


def main(path):
    w, h, ch, rows = read_png(path)
    opaque = 0
    partial = 0
    colors = {}
    xs, ys = [], []
    for y, line in enumerate(rows):
        for x in range(w):
            off = x * ch
            if ch == 4:
                r, g, b, a = line[off:off + 4]
            else:
                r, g, b = line[off:off + 3]
                a = 255
            if a > 0:
                opaque += 1 if a == 255 else 0
                partial += 1 if 0 < a < 255 else 0
                colors[(r, g, b, a)] = colors.get((r, g, b, a), 0) + 1
                xs.append(x)
                ys.append(y)
    print(f"png={path}")
    print(f"size={w}x{h} channels={ch}")
    print(f"fully_opaque_pixels={opaque} partial_alpha_pixels={partial}")
    if xs:
        print(f"coverage_bbox=x[{min(xs)}..{max(xs)}] y[{min(ys)}..{max(ys)}]")
    top = sorted(colors.items(), key=lambda kv: -kv[1])[:8]
    print("top_rgba_values=")
    for rgba, n in top:
        print(f"  rgba{rgba} count={n}")


if __name__ == "__main__":
    main(sys.argv[1])
