#!/usr/bin/env python3
"""按源稿里的观察点坐标读取 PNG 的实际 RGBA8 值，只依赖 Python 标准库。

用法:
  python3 manual/tools/probe.py manual/sources/c01-shape-shift.json \
      manual/outputs/png/c01-shape-shift.png

PNG 由 internal/raster 输出：非交错、8 位、色型 2(RGB) 或 6(RGBA)。
观察点坐标是画布像素整数坐标，与 outputs/manifest.json 里的探针同一约定。
"""
import json, struct, sys, zlib


def read_png(path):
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 文件: " + path)
    pos, width, height, channels = 8, 0, 0, 0
    idat = b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", body[:10])
            if bit_depth != 8:
                raise ValueError("只支持 8 位 PNG")
            channels = {2: 3, 6: 4}.get(color_type)
            if channels is None:
                raise ValueError("只支持色型 2/6，收到 %d" % color_type)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * channels
    rows, prev, i = [], bytearray(stride), 0
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
                raise ValueError("未知 PNG 滤波类型 %d" % f)
        rows.append(line)
        prev = line
    return width, height, channels, rows


def pixel(png, x, y):
    width, height, channels, rows = png
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError("坐标 (%d,%d) 超出画布 %dx%d" % (x, y, width, height))
    off = x * channels
    px = rows[y][off:off + channels]
    return tuple(px) if channels == 4 else (px[0], px[1], px[2], 255)


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    case = json.load(open(sys.argv[1], encoding="utf-8"))
    png = read_png(sys.argv[2])
    print("# %s" % case["title"])
    print("%-9s %-22s %s" % ("观察点", "RGBA8 实测", "源稿预期"))
    for probe in case["probes"]:
        x, y = probe["point"]["x"], probe["point"]["y"]
        rgba = pixel(png, x, y)
        print("(%2d,%2d)   %-22s %s" % (x, y, str(rgba), probe["expect"]))


if __name__ == "__main__":
    main()
