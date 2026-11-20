#!/usr/bin/env python3
"""Generate the seam plate at several horizontal offsets.

The plate is the one described in analysis/seam-study/README.md:

  48x48 canvas, one normal layer with opacity 1,
  a single evenodd shape painted with an opaque blue solid brush.
  Outer contour: M(6,24) C[(6,6),(42,6),(42,24)] C[(42,42),(6,42),(6,24)] Z
  Inner contour: M(22.1,12) L(22.4,12) L(22.4,36) L(22.1,36) Z
  Shape transform: identity, translated horizontally by the requested offset.

For each offset two plates are written:

  seam-<i>        the plate as reported
  seam-nohole-<i> the same plate with the inner contour removed

The second set is a control: it shows what the PNG would look like if the hole
really had vanished from the geometry. Comparing a with-hole render against its
no-hole control at the same offset separates "hole geometry changed" from
"hole missed by the sampler".

Usage: python3 gen_scenes.py OUTDIR
Writes OUTDIR/offset-<dx>.json and OUTDIR/nohole-<dx>.json.
"""
import json
import sys
import pathlib

OFFSETS = (0.0, 0.25, 0.5)


def op(kind, *points):
    return {"kind": kind, "points": [{"x": x, "y": y} for x, y in points]}


def shape_ops(with_hole):
    ops = [
        op("M", (6, 24)),
        op("C", (6, 6), (42, 6), (42, 24)),
        op("C", (42, 42), (6, 42), (6, 24)),
        op("Z"),
    ]
    if with_hole:
        ops += [
            op("M", (22.1, 12)),
            op("L", (22.4, 12)),
            op("L", (22.4, 36)),
            op("L", (22.1, 36)),
            op("Z"),
        ]
    return ops


def document(dx, with_hole):
    return {
        "width": 48,
        "height": 48,
        "background": {"r": 0, "g": 0, "b": 0, "a": 0},
        "layers": [
            {
                "id": "seam-layer",
                "opacity": 1,
                "blend": "normal",
                "shapes": [
                    {
                        "id": "seam-shape",
                        "fill_rule": "evenodd",
                        "path": shape_ops(with_hole),
                        "brush": {"kind": "solid", "color": {"r": 0, "g": 0, "b": 1, "a": 1}},
                        "transform": [1, 0, 0, 1, dx, 0],
                    }
                ],
            }
        ],
    }


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    out = pathlib.Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for i, dx in enumerate(OFFSETS):
        for prefix, with_hole in (("seam", True), ("seam-nohole", False)):
            plate = {
                "id": f"{prefix}-{i}",
                "title": f"{'seam' if with_hole else 'no-hole control'} offset {dx}px",
                "document": document(dx, with_hole),
            }
            name = f"offset-{dx:g}.json" if with_hole else f"nohole-{dx:g}.json"
            (out / name).write_text(json.dumps(plate, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
