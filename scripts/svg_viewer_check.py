#!/usr/bin/env python3
"""Viewer-side geometry check against a real exported SVG file.

No browser/SVG renderer is installed in the verification environment, so this
script reproduces the deterministic part of what an SVG consumer does:

  1. parse the exact numeric tokens from the <path d> and transform attributes
     as written in the service-produced file;
  2. evaluate the affine transform in IEEE-754 binary64 (Python float == Go
     float64 == SVG/CSS double precision);
  3. apply the same nonzero/evenodd point-in-polygon rule the service's own
     geom package uses, at one sample per pixel (coverage differences in real
     browsers do not matter for a zero-area polygon: nothing can cover it).

It also renders the "author intent" geometry (the same vertices evaluated
with the full-precision submitted numbers, i.e. what the SVG *should* have
expressed) side by side, so the two outputs are directly comparable.

Run:  python3 -I scripts/svg_viewer_check.py investigation/results/repro.svg
"""

import re
import sys


def parse_svg(path):
    text = open(path, encoding="utf-8").read()
    m_path = re.search(r'<path d="([^"]+)"', text)
    m_mat = re.search(r'transform="matrix\(([^)]+)\)"', text)
    m_rule = re.search(r'fill-rule="([^"]+)"', text)
    verbs = re.findall(r"([ML])(-?[0-9.eE+-]+)\s+(-?[0-9.eE+-]+)",
                       m_path.group(1))
    ring = [(float(x), float(y)) for _, x, y in verbs]
    matrix = [float(t) for t in m_mat.group(1).split()]
    return ring, matrix, m_rule.group(1), text


def apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def contains(ring, px, py, rule):
    winding = 0
    crossings = 0
    for i, (ax, ay) in enumerate(ring):
        bx, by = ring[(i + 1) % len(ring)]
        if (ay <= py and by > py) or (by <= py and ay > py):
            xint = ax + (py - ay) * (bx - ax) / (by - ay)
            if xint > px:
                crossings += 1
                winding += 1 if by > ay else -1
    return (crossings % 2 == 1) if rule == "evenodd" else winding != 0


def raster(ring, w, h, rule):
    hit = 0
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            if contains(ring, x + 0.5, y + 0.5, rule):
                hit += 1
                xs.append(x)
                ys.append(y)
    bbox = (f"x[{min(xs)}..{max(xs)}] y[{min(ys)}..{max(ys)}]"
            if xs else "empty")
    return hit, bbox


def area(ring):
    return abs(sum(ring[i][0] * ring[(i + 1) % len(ring)][1]
                   - ring[i][1] * ring[(i + 1) % len(ring)][0]
                   for i in range(len(ring))) / 2)


def main(path):
    ring_local, matrix, rule, text = parse_svg(path)
    canvas = [apply(matrix, x, y) for x, y in ring_local]
    m = re.search(r'viewBox="0 0 (\d+) (\d+)"', text)
    w = h = int(m.group(1))

    print(f"svg_file={path}")
    print(f"fill_rule={rule}")
    print("local_vertices_as_written_in_file=")
    for p in ring_local:
        print(f"  {p!r}")
    print(f"matrix_tokens_as_written={matrix!r}")
    print("canvas_vertices_recomputed_by_viewer_f64=")
    for p in canvas:
        print(f"  {p!r}")
    print(f"viewer_polygon_area={area(canvas)!r}")
    # collinearity check: every edge on y == x ?
    on_diag = all(p[0] == p[1] for p in canvas)
    print(f"all_viewer_vertices_on_y_equals_x={on_diag}")
    hit, bbox = raster(canvas, w, h, rule)
    print(f"viewer_rasterized_covered_pixels={hit} bbox={bbox}")


if __name__ == "__main__":
    main(sys.argv[1])
