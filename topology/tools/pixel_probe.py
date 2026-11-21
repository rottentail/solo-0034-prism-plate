#!/usr/bin/env python3
"""Count ink components and holes directly from the rendered PNGs.

This is deliberately naive: it decodes the PNG, thresholds against the canvas
background, and counts connected components and enclosed background regions
under both connectivity pairings.  Its only purpose is to show why a PNG that
"looks like N holes" is not evidence.  The authoritative counts are produced by
verify.py from the flattened geometry.

No image library is used; the decoder is plain zlib + PNG filters.
"""
from __future__ import annotations

import json
import pathlib
import struct
import zlib
from collections import deque

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "out"
ANN = ROOT / "annotations"


def decode_png(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    idat = b""
    w = h = bitdepth = color = None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if typ == b"IHDR":
            w, h, bitdepth, color, comp, filt, inter = struct.unpack(">IIBBBBB", chunk)
            assert bitdepth == 8 and color in (2, 6) and inter == 0
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
    raw = zlib.decompress(idat)
    bpp = 4 if color == 6 else 3
    stride = w * bpp
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ft = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        if ft == 0:
            pass
        elif ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        else:
            raise ValueError("filter")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, out, bpp


def components(mask, w, h, conn8):
    seen = [[False] * w for _ in range(h)]
    comps = []
    nb = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    if conn8:
        nb += [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    for y in range(h):
        for x in range(w):
            if not mask[y][x] or seen[y][x]:
                continue
            q = deque([(x, y)]); seen[y][x] = True; n = 0
            while q:
                cx, cy = q.popleft(); n += 1
                for dx, dy in nb:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h and mask[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True; q.append((nx, ny))
            comps.append(n)
    return comps


def probe(path: pathlib.Path, bg=(14, 17, 23), tol=10):
    w, h, px, bpp = decode_png(path.read_bytes())
    ink = [[False] * w for _ in range(h)]
    for y in range(h):
        row = y * w * bpp
        for x in range(w):
            r, g, b = px[row + x * bpp:row + x * bpp + 3]
            # background is opaque dark; ink is any sufficiently different colour
            if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > tol:
                ink[y][x] = True
    out = {}
    # complementary connectivity pairings (foreground / background)
    for name, fg8 in (("fg4_bg8", False), ("fg8_bg4", True)):
        bg8 = not fg8
        fg = components(ink, w, h, fg8)
        bgmask = [[not ink[y][x] for x in range(w)] for y in range(h)]
        seen = [[False] * w for _ in range(h)]
        nb = [(-1, 0), (1, 0), (0, -1), (0, 1)]
        if bg8:
            nb += [(-1, -1), (-1, 1), (1, -1), (1, 1)]
        holes = 0
        for y in range(h):
            for x in range(w):
                if not bgmask[y][x] or seen[y][x]:
                    continue
                q = deque([(x, y)]); seen[y][x] = True
                touches = False
                while q:
                    cx, cy = q.popleft()
                    if cx in (0, w - 1) or cy in (0, h - 1):
                        touches = True
                    for dx, dy in nb:
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h and bgmask[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True; q.append((nx, ny))
                if not touches:
                    holes += 1
        out[name] = {"components": len(fg), "holes": holes}
    out["pixels"] = [w, h]
    out["ink_pixels"] = sum(sum(1 for v in row if v) for row in ink)
    return out


def main():
    ledger = {c["id"]: c for c in json.loads((ANN / "ledger.json").read_text())["cases"]}
    index = json.loads((ROOT / "cases" / "index.json").read_text())
    results = []
    for entry in index["cases"]:
        cid = entry["id"]
        res = probe(OUT / f"{cid}.png")
        gt = ledger[cid]
        row = {
            "id": cid,
            "exact_components": gt["components_open"],
            "exact_holes": gt["holes_open"],
            "png_fg4_bg8": res["fg4_bg8"],
            "png_fg8_bg4": res["fg8_bg4"],
            "ink_pixels": res["ink_pixels"],
            "agrees_fg4": res["fg4_bg8"]["components"] == gt["components_open"]
                          and res["fg4_bg8"]["holes"] == gt["holes_open"],
            "agrees_fg8": res["fg8_bg4"]["components"] == gt["components_open"]
                          and res["fg8_bg4"]["holes"] == gt["holes_open"],
        }
        row["naive_conflict"] = row["png_fg4_bg8"] != row["png_fg8_bg4"]
        results.append(row)
        print(f"{cid:30s} exact C/H {gt['components_open']}/{gt['holes_open']}  "
              f"png 4/8 {row['png_fg4_bg8']['components']}/{row['png_fg4_bg8']['holes']}  "
              f"png 8/4 {row['png_fg8_bg4']['components']}/{row['png_fg8_bg4']['holes']}")
    (ANN / "pixel_probe.json").write_text(
        json.dumps({"cases": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", ANN / "pixel_probe.json")


if __name__ == "__main__":
    main()
