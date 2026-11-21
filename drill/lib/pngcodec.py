"""Pure-stdlib PNG codec used by the drill builder and verifier.

Only 8-bit, non-interlaced RGB/RGBA PNGs are accepted.  The service emits RGB8;
derived copies in the reception kit are emitted as RGB8 or RGBA8 by this module.
"""

import binascii
import hashlib
import struct
import zlib


def decode(data):
    """Return (width, height, rgba_bytes) for an 8-bit RGB/RGBA PNG."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    idat = bytearray()
    width = height = color_type = bit_depth = None
    interlace = 0
    while pos < len(data):
        if pos + 8 > len(data):
            raise ValueError("truncated chunk header")
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        crc = data[pos + 8 + length:pos + 12 + length]
        if len(payload) != length or len(crc) != 4:
            raise ValueError("truncated chunk")
        if binascii.crc32(ctype + payload) & 0xFFFFFFFF != struct.unpack(">I", crc)[0]:
            raise ValueError("bad chunk CRC")
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", payload)
        elif ctype == b"IDAT":
            idat += payload
        elif ctype == b"IEND":
            break
    if width is None:
        raise ValueError("missing IHDR")
    if bit_depth != 8 or interlace != 0 or color_type not in (2, 6):
        raise ValueError("unsupported PNG encoding: depth=%r type=%r interlace=%r"
                         % (bit_depth, color_type, interlace))
    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    if len(raw) != height * (stride + 1):
        raise ValueError("unexpected IDAT size")
    out = bytearray()
    prev = bytearray(stride)
    cursor = 0
    for _ in range(height):
        method = raw[cursor]
        cursor += 1
        line = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        if method == 0:
            pass
        elif method == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x - channels]) & 0xFF
        elif method == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif method == 3:
            for x in range(stride):
                left = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif method == 4:
            for x in range(stride):
                left = line[x - channels] if x >= channels else 0
                up = prev[x]
                upleft = prev[x - channels] if x >= channels else 0
                p = left + up - upleft
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else upleft)
                line[x] = (line[x] + pred) & 0xFF
        else:
            raise ValueError("unknown PNG filter %d" % method)
        out += line
        prev = line
    if channels == 4:
        rgba = bytes(out)
    else:
        rgba = bytearray()
        for i in range(0, len(out), 3):
            rgba += out[i:i + 3]
            rgba.append(255)
        rgba = bytes(rgba)
    return width, height, rgba


def encode(width, height, rgba):
    """Encode RGBA bytes as an 8-bit PNG, choosing RGB when fully opaque."""
    if len(rgba) != width * height * 4:
        raise ValueError("pixel buffer size mismatch")
    opaque = all(rgba[i] == 255 for i in range(3, len(rgba), 4))
    if opaque:
        color_type = 2
        rows = bytearray()
        for y in range(height):
            rows.append(0)
            base = y * width * 4
            for x in range(width):
                rows += rgba[base + x * 4:base + x * 4 + 3]
    else:
        color_type = 6
        rows = bytearray()
        for y in range(height):
            rows.append(0)
            rows += rgba[y * width * 4:(y + 1) * width * 4]

    def chunk(kind, payload):
        body = kind + payload
        return (struct.pack(">I", len(payload)) + body +
                struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def pixel_digest(width, height, rgba):
    h = hashlib.sha256()
    h.update(b"RGBA8\x00")
    h.update(struct.pack(">II", width, height))
    h.update(rgba)
    return h.hexdigest()


def thumbnail(width, height, rgba, factor):
    if width % factor or height % factor:
        raise ValueError("thumbnail factor must divide both dimensions")
    tw, th = width // factor, height // factor
    area = factor * factor
    out = bytearray()
    for ty in range(th):
        for tx in range(tw):
            for channel in range(4):
                total = 0
                for dy in range(factor):
                    row = (ty * factor + dy) * width
                    for dx in range(factor):
                        total += rgba[(row + tx * factor + dx) * 4 + channel]
                out.append((total + area // 2) // area)
    return tw, th, bytes(out)


def resize_bilinear(width, height, rgba, new_width, new_height):
    out = bytearray(new_width * new_height * 4)
    for y in range(new_height):
        sy = (y + 0.5) * height / new_height - 0.5
        y0 = max(0, min(height - 1, int(sy)))
        y1 = max(0, min(height - 1, y0 + 1))
        fy = max(0.0, min(1.0, sy - y0))
        for x in range(new_width):
            sx = (x + 0.5) * width / new_width - 0.5
            x0 = max(0, min(width - 1, int(sx)))
            x1 = max(0, min(width - 1, x0 + 1))
            fx = max(0.0, min(1.0, sx - x0))
            for c in range(4):
                p00 = rgba[(y0 * width + x0) * 4 + c]
                p10 = rgba[(y0 * width + x1) * 4 + c]
                p01 = rgba[(y1 * width + x0) * 4 + c]
                p11 = rgba[(y1 * width + x1) * 4 + c]
                top = p00 + (p10 - p00) * fx
                bottom = p01 + (p11 - p01) * fx
                out[(y * new_width + x) * 4 + c] = int(round(top + (bottom - top) * fy))
    return bytes(out)


def compare(a, b):
    """Return a reproducible difference report for two (w,h,rgba) images."""
    wa, ha, pa = a
    wb, hb, pb = b
    if (wa, ha) != (wb, hb):
        return {"same_dimensions": False, "diff_pixels": None, "bbox": None,
                "max_channel_delta": None, "pairs": []}
    diffs = []
    for i in range(0, len(pa), 4):
        if pa[i:i + 4] != pb[i:i + 4]:
            diffs.append((i // 4 % wa, i // 4 // wa, pa[i:i + 4], pb[i:i + 4]))
    bbox = None
    if diffs:
        bbox = [min(d[0] for d in diffs), min(d[1] for d in diffs),
                max(d[0] for d in diffs), max(d[1] for d in diffs)]
    return {
        "same_dimensions": True,
        "diff_pixels": len(diffs),
        "bbox": bbox,
        "max_channel_delta": max((max(abs(x - y) for x, y in zip(d[2], d[3]))
                                  for d in diffs), default=0),
        "pairs": diffs[:64],
    }
