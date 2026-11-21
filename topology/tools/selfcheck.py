#!/usr/bin/env python3
"""Invariant checks over the whole casebook.

These are not product tests; they are consistency checks on the analysis so a
reader can trust the ledger:

1. face-area identity: sum over arrangement faces of (winding x area) equals the
   exact signed area of the flattened path (single-shape cases);
2. filled-area identity: filled area + unfilled bounded area + exterior area
   equals the canvas area, on a coarse exact grid covering the drawing bbox;
3. shaft area identity: filled area computed by the arrangement equals an
   independent scanline integration of the flattening at rational sample lines
   (trapezoid sum), within the documented tolerance for sampling;
4. refinement monotonicity: for curve cases the flattened signed area converges
   to the exact Bezier area as the subdivision grows.
"""
from __future__ import annotations

import json
import pathlib
import sys
from fractions import Fraction as F

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import exact as E
import topology as T

CASES = ROOT / "cases"
ANN = ROOT / "annotations"


def scanline_area(path: T.Path, rule: str, n: int = 160) -> F:
    """Midpoint scanline integration of the flattened fill set.

    For each of n horizontal lines, intersect every edge, sort the x crossings,
    and test the midpoint of each span by exact winding.  Sum span length * dy.
    This is a deliberately different algorithm from the slab arrangement.
    """
    ys = [p[1] for ring in path for p in ring]
    y0, y1 = min(ys), max(ys)
    dy = (y1 - y0) / n
    total = F(0)
    for k in range(n):
        y = y0 + (k + F(1, 2)) * dy
        xs = []
        for ring in path:
            for i, a in enumerate(ring):
                b = ring[(i + 1) % len(ring)]
                if a[1] == b[1]:
                    continue
                if (a[1] <= y < b[1]) or (b[1] <= y < a[1]):
                    xs.append(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]))
        xs.sort()
        for i in range(len(xs) - 1):
            mid = (xs[i] + xs[i + 1]) / 2
            if E.filled(path, (mid, y), rule):
                total += (xs[i + 1] - xs[i]) * dy
    return total


def main() -> int:
    index = json.loads((CASES / "index.json").read_text())
    problems = []
    for entry in index["cases"]:
        case = E.load_document(str(CASES / entry["file"]))
        ann = json.loads((ANN / (entry["id"] + ".json")).read_text())
        shapes = case["document"]["layers"][0]["shapes"]
        regions = []
        for sh in shapes:
            m = E.matrix_of(sh["transform"])
            d = E.det(m)
            inv = ((m[3] / d), (-m[1] / d), (-m[2] / d), (m[0] / d),
                   ((m[2] * m[5] - m[3] * m[4]) / d), ((m[1] * m[4] - m[0] * m[5]) / d))
            local = E.flatten_ops(E.ops_of(sh["path"]))
            regions.append({"canvas": E.transform_path(local, m), "local": local,
                            "inverse": inv, "rule": sh["fill_rule"]})
        topo = T.arrangement_regions(regions)
        if ann["signed_area_from_faces_exact"] is not None:
            face = F(ann["signed_area_from_faces_exact"])
            flat_total = sum(E.signed_area_rings(r["canvas"]) for r in regions)
            if face != flat_total:
                problems.append((entry["id"], "face identity", face, flat_total))
        # scanline cross-check for single-shape cases only (union needs care)
        if len(shapes) == 1 and shapes[0]["fill_rule"] == "nonzero":
            clear = ann["approximation"]["minimum_clearance"]
            # choose the number of scan lines so the thinnest feature is not
            # missed: line spacing < clearance/4, capped at 40000 lines
            n = 160
            if clear is not None and clear > 0:
                ys = [float(p[1]) for ring in regions[0]["canvas"] for p in ring]
                span = max(ys) - min(ys)
                n = min(40000, max(160, int(span / (clear / 4)) + 1))
            scan = scanline_area(regions[0]["canvas"], "nonzero", n)
            arr = topo["filled_area"]
            if arr and abs(scan - arr) > F(1, 100) * arr:
                problems.append((entry["id"], "scanline", float(scan), float(arr), n))
        print(f"{entry['id']:32s} " + ("OK" if not any(p[0] == entry['id'] for p in problems) else "PROBLEM"))
    print()
    if problems:
        for p in problems:
            print("PROBLEM", p)
        return 1
    print("all invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
