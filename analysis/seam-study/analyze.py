#!/usr/bin/env python3
"""Independent geometry and pixel evidence for analysis/seam-study.

Three layers of evidence, none of which trusts the service's own bookkeeping:

1. `lattice_model()`: an exact closed-form model of when the 2x2 sampler can
   see the inner contour. The hole is 0.3 px wide in x and the sampler's x
   positions are ... , k+0.25, k+0.75, ... (spacing 0.5 px), so the hole is
   visible only when a sample column falls strictly inside it.
2. `render_png_pixels()`: a pure-Python mirror of internal/raster + internal/paint
   for this one-shape scene. `--verify` re-derives every byte of the committed
   PNGs from the scene JSON, so those files are checked rather than trusted.
3. `hole_pixels()`: classifies pixels in a decoded PNG as "hole pixels" (all
   four samples inside the outer silhouette, at least one inside the inner
   contour), which separates the hole from ordinary outer-edge antialiasing.

Usage:
  python3 analyze.py                 # closed-form + per-sample-offset report
  python3 analyze.py --verify        # additionally compare PNGs byte-for-byte
  python3 detect_hole.py PNG         # per-pixel hole classification for one PNG
"""
import argparse
import hashlib
import json
import math
import pathlib
import struct
import sys
import zlib

HERE = pathlib.Path(__file__).resolve().parent
SAMPLES = (0.0, 0.25, 0.5)

# ------------------------------------------------------------------ geometry ---
INNER = ((22.1, 12.0), (22.4, 12.0), (22.4, 36.0), (22.1, 36.0))
INNER_WIDTH = 0.3
INNER_HEIGHT = 24.0  # 12..36


def cubic(p0, p1, p2, p3, steps=24):
    """Same fixed subdivision as internal/geom.Flatten (24 segments per cubic)."""
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        out.append((
            u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
        ))
    return out


def outer_ring():
    """M(6,24) C[(6,6),(42,6),(42,24)] C[(42,42),(6,42),(6,24)] Z, flattened."""
    ring = [(6.0, 24.0)]
    ring += cubic((6, 24), (6, 6), (42, 6), (42, 24))
    ring += cubic((42, 24), (42, 42), (6, 42), (6, 24))
    return ring


def rings(dx):
    return [
        [(x + dx, y) for x, y in outer_ring()],
        [(x + dx, y) for x, y in INNER],
    ]


def contains(path, p, rule="evenodd"):
    """Same half-open crossing test as geom.Path.Contains."""
    crossings = 0
    winding = 0
    for ring in path:
        for i, a in enumerate(ring):
            b = ring[(i + 1) % len(ring)]
            if (a[1] <= p[1] and b[1] > p[1]) or (b[1] <= p[1] and a[1] > p[1]):
                x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                if x > p[0]:
                    crossings += 1
                    winding += 1 if b[1] > a[1] else -1
    return (crossings % 2 == 1) if rule == "evenodd" else (winding != 0)


def samples_for_pixel(x, y):
    """The raster package's four subpixel positions for pixel (x, y)."""
    return [(x + (sx + 0.5) / 2, y + (sy + 0.5) / 2)
            for sy in (0, 1) for sx in (0, 1)]


# ----------------------------------------------------------------- lattice ---
def lattice_model(dx):
    """Exact model: which sample columns land inside the inner hole?

    The hole is the rectangle with local-coordinate x in [22.1, 22.4) and
    y in [12, 36). The half-open x interval is not an approximation: it is what
    geom.Path.Contains produces. Its crossing test counts an edge only when
    the edge x is strictly greater than the sample x, so a sample exactly on
    the left edge (x = 22.1) is inside while one exactly on the right edge
    (x = 22.4) is outside.

    Sample x coordinates are k+0.25 and k+0.75. With dx in [0, 1] only the
    columns 22.25, 22.75 and 23.25 can fall inside 22.1+dx .. 22.4+dx.
    """
    hit = []
    for col in (22.25, 22.75, 23.25):
        q = col - dx  # the raster package maps the sample back through the inverse transform
        if 22.1 <= q < 22.4:
            hit.append(col)
    return hit


def visible(dx):
    return bool(lattice_model(dx))


def blind_bands():
    """Blind intervals in [0, 1], derived exactly from the half-open model.

    Column c is hit exactly when dx is in (c-22.4, c-22.1]. Intersecting the
    three hit intervals with [0, 1] and taking the complement gives
    (0.15, 0.35] and (0.65, 0.85]: 0.4 of every 1 px translation is blind.
    Endpoints are half-open because of geom.Path.Contains' strict x test; the
    table below states each endpoint's status explicitly.
    """
    hit_intervals = [(col - 22.4, col - 22.1) for col in (22.25, 22.75, 23.25)]
    hit_intervals = [(max(0.0, a), min(1.0, b)) for a, b in hit_intervals if a < 1.0 and b > 0.0]
    hit_intervals.sort()
    merged = []
    for a, b in hit_intervals:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    bands = []
    cursor = 0.0
    for a, b in merged:
        if a > cursor:
            bands.append((cursor, a))
        cursor = b
    if cursor < 1.0:
        bands.append((cursor, 1.0))
    return bands


def band_endpoints():
    """Endpoint membership for the two blind bands, from the model itself."""
    out = []
    for left, right in ((0.15, 0.35), (0.65, 0.85)):
        out.append({
            "band": [left, right],
            "left_visible": visible(left),
            "just_inside_left_visible": visible(left + 1e-6),
            "right_visible": visible(right),
            "just_left_of_right_visible": visible(right - 1e-6),
        })
    return out


# ------------------------------------------------------------------- scene ---
def scene_document(dx):
    return {
        "width": 48, "height": 48,
        "background": {"r": 0, "g": 0, "b": 0, "a": 0},
        "layers": [{
            "id": "seam-layer", "opacity": 1, "blend": "normal",
            "shapes": [{
                "id": "seam-shape", "fill_rule": "evenodd",
                "path": [
                    {"kind": "M", "points": [{"x": 6, "y": 24}]},
                    {"kind": "C", "points": [{"x": 6, "y": 6}, {"x": 42, "y": 6}, {"x": 42, "y": 24}]},
                    {"kind": "C", "points": [{"x": 42, "y": 42}, {"x": 6, "y": 42}, {"x": 6, "y": 24}]},
                    {"kind": "Z"},
                    {"kind": "M", "points": [{"x": 22.1, "y": 12}]},
                    {"kind": "L", "points": [{"x": 22.4, "y": 12}]},
                    {"kind": "L", "points": [{"x": 22.4, "y": 36}]},
                    {"kind": "L", "points": [{"x": 22.1, "y": 36}]},
                    {"kind": "Z"},
                ],
                "brush": {"kind": "solid", "color": {"r": 0, "g": 0, "b": 1, "a": 1}},
                "transform": [1, 0, 0, 1, dx, 0],
            }],
        }],
    }


# ------------------------------------------------------------------ mirror ---
def _linear(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _srgb(v):
    v = max(0.0, min(1.0, v))
    return v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055


def _round(v):  # Go math.Round for non-negative values
    return int(math.floor(v + 0.5))


def render_png_pixels(dx):
    """Pure-Python mirror of internal/raster.Render for this single-shape scene.

    Returns (rows, coverage) where rows[y][x] == (r, g, b, a) exactly as the
    service would emit them.
    """
    path = rings(dx)
    rows = {}
    coverage = {}
    for y in range(48):
        row = []
        for x in range(48):
            # sum of four paint.Pixel values, divided by 4 per component
            acc = [0.0, 0.0, 0.0, 0.0]
            for p in samples_for_pixel(x, y):
                inside = contains(path, p, "evenodd")
                # dst = transparent background; src = opaque blue when inside
                src = (0.0, 0.0, _linear(1.0), 1.0) if inside else (0.0, 0.0, 0.0, 0.0)
                dst = (0.0, 0.0, 0.0, 0.0)
                sa = src[3]
                over = [src[i] + dst[i] * (1 - sa) for i in range(3)]
                over.append(sa + dst[3] * (1 - sa))
                for i in range(4):
                    acc[i] += over[i] / 4
            a = acc[3]
            if a <= 0:
                px = (0, 0, 0, 0)
            else:
                px = (
                    _round(_srgb(acc[0] / a) * 255),
                    _round(_srgb(acc[1] / a) * 255),
                    _round(_srgb(acc[2] / a) * 255),
                    _round(min(1.0, a) * 255),
                )
            row.append(px)
            coverage[(x, y)] = a
        rows[y] = row
    return rows, coverage


# -------------------------------------------------------------------- PNG ---
def read_png(path):
    return read_png_bytes(pathlib.Path(path).read_bytes())


def read_png_bytes(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, chunks = 8, []
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        chunks.append((kind, data[pos + 8:pos + 8 + n]))
        pos += 12 + n
    w, h, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    if depth != 8 or ctype != 6 or interlace != 0:
        raise ValueError("expected non-interlaced 8-bit RGBA PNG")
    raw = zlib.decompress(b"".join(b for k, b in chunks if k == b"IDAT"))
    stride = w * 4
    rows, prev, p = [], bytearray(stride), 0
    for _ in range(h):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        for i in range(stride):
            a = line[i - 4] if i >= 4 else 0
            b = prev[i]
            c = prev[i - 4] if i >= 4 else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 255
        rows.append([tuple(line[i:i + 4]) for i in range(0, stride, 4)])
        prev = line
    return (w, h), rows


# ------------------------------------------------------------------- hole ---
def hole_candidates(dx):
    """Pixels where the inner contour *can* affect coverage.

    All four 2x2 samples must be inside the outer silhouette (so the silhouette
    alone cannot explain partial alpha) and at least one sample must be inside
    the inner contour. The set depends only on geometry and the sample lattice,
    so a no-hole control at the same offset has the same candidates; only its
    alpha is not reduced. A pixel is an *observed* hole pixel when it is a
    candidate and its alpha is below 255.
    """
    outer = [rings(dx)[0]]
    inner = [rings(dx)[1]]
    found = []
    for y in range(48):
        for x in range(48):
            pts = samples_for_pixel(x, y)
            if all(contains(outer, p, "nonzero") for p in pts) and any(
                contains(inner, p, "nonzero") for p in pts
            ):
                found.append((x, y))
    return found


def hole_pixels(rows, dx):
    """Observed hole pixels: geometric candidates whose alpha is reduced."""
    candidates = set(hole_candidates(dx))
    row_items = rows.items() if isinstance(rows, dict) else enumerate(rows)
    found = []
    for y, row in row_items:
        for x, px in enumerate(row):
            if (x, y) in candidates and px[3] != 255:
                found.append({"x": x, "y": y, "a": px[3]})
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=str(HERE / "samples"))
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    regions = {}
    for dx in SAMPLES:
        reg = regions[f"{dx:g}"] = {"hole_candidate_pixels": hole_candidates(dx)}
        reg["hole_candidate_count"] = len(reg["hole_candidate_pixels"])
        reg["candidate_columns"] = sorted({x for x, _ in reg["hole_candidate_pixels"]})

    report = {
        "inner_contour": {
            "points": INNER,
            "width_px": INNER_WIDTH,
            "height_px": INNER_HEIGHT,
            "area_px2": INNER_WIDTH * INNER_HEIGHT,
            "note": "geometry is identical at every dx; only the transform translation differs",
        },
        "sampler": {
            "samples_per_pixel": 4,
            "x_positions_within_pixel": [0.25, 0.75],
            "x_spacing_px": 0.5,
            "hole_width_px": INNER_WIDTH,
            "consequence": "a 0.3 px feature is narrower than the 0.5 px sample spacing, "
                           "so some translations put no sample column inside it",
        },
        "blind_bands_dx_in_0_1": [
            {"from": round(a, 6), "to": round(b, 6), "width": round(b - a, 6)}
            for a, b in blind_bands()
        ],
        "candidate_regions": {k: {kk: vv for kk, vv in v.items() if kk != "hole_candidate_pixels"} for k, v in regions.items()},
        "offsets": {},
    }
    for dx in SAMPLES:
        rows, _cover = render_png_pixels(dx)
        entry = {
            "lattice_hit_columns": lattice_model(dx),
            "analytic_visible": visible(dx),
            "observed_hole_pixels": len(hole_pixels(rows, dx)),
            "partial_pixels_total": sum(1 for row in rows.values() for px in row if 0 < px[3] < 255),
        }
        name = f"{dx:g}"
        png = pathlib.Path(args.samples) / f"offset-{name}.png"
        if args.verify and png.exists():
            _, disk_rows = read_png(png)
            flat_disk = [px for row in disk_rows for px in row]
            flat_mirror = [rows[y][x] for y in range(48) for x in range(48)]
            entry["png_matches_mirror"] = flat_disk == flat_mirror
            entry["png_sha256"] = hashlib.sha256(png.read_bytes()).hexdigest()
            entry["observed_hole_pixels_in_png"] = len(hole_pixels(disk_rows, dx))
            control = pathlib.Path(args.samples) / f"nohole-{name}.png"
            if control.exists():
                entry["png_equals_no_hole_control"] = control.read_bytes() == png.read_bytes()
                _, control_rows = read_png(control)
                same = [row for row in control_rows]
                diff = [(x, y, disk_rows[y][x], same[y][x])
                        for y in range(48) for x in range(48) if disk_rows[y][x] != same[y][x]]
                entry["pixels_differing_from_no_hole_control"] = len(diff)
                entry["control_pixels_all_255_in_candidate_region"] = all(
                    same[y][x][3] == 255 for x, y in regions[name]["hole_candidate_pixels"]
                )
        report["offsets"][name] = entry
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    sys.exit(main())
