"""Minimal deterministic PNG reader (8-bit gray/RGB/RGBA/RGBA-palette).

The production encoder always writes 8-bit NRGBA (color type 6), but the reader
also accepts the other common 8-bit layouts so colleagues can point it at any
flat PNG export. Standard library only: zlib + struct.
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
        raise ValueError(f"{path}: not a PNG")
    pos, width, height, bit_depth, color_type, interlace = 8, 0, 0, 0, 0, 0
    idat = b""
    palette, trns = [], b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
            if bit_depth != 8 or interlace != 0:
                raise ValueError("only non-interlaced 8-bit PNGs are supported")
        elif ctype == b"PLTE":
            palette = [tuple(chunk[i:i + 3]) for i in range(0, len(chunk), 3)]
        elif ctype == b"tRNS":
            trns = chunk
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    raw = zlib.decompress(idat)
    stride = width * channels
    rows, prev, off = [], bytearray(stride), 0
    for _ in range(height):
        ftype = raw[off]
        line = bytearray(raw[off + 1:off + 1 + stride])
        off += 1 + stride
        for i, x in enumerate(line):
            left = line[i - channels] if i >= channels else 0
            up = prev[i]
            ul = prev[i - channels] if i >= channels else 0
            if ftype == 0:
                pass
            elif ftype == 1:
                x = (x + left) & 255
            elif ftype == 2:
                x = (x + up) & 255
            elif ftype == 3:
                x = (x + ((left + up) >> 1)) & 255
            elif ftype == 4:
                x = (x + _paeth(left, up, ul)) & 255
            line[i] = x
        rows.append(bytes(line))
        prev = line

    def px(i, j):
        row = rows[j]
        k = i * channels
        if color_type == 6:
            return tuple(row[k:k + 4])
        if color_type == 2:
            return (row[k], row[k + 1], row[k + 2], 255)
        if color_type == 0:
            v = row[k]
            return (v, v, v, 255)
        idx = row[k]
        r, g, b = palette[idx]
        a = trns[idx] if idx < len(trns) else 255
        return (r, g, b, a)

    return width, height, px
