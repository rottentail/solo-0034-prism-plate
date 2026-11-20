#!/usr/bin/env python3
"""Closed-form report on where the seam can and cannot be sampled.

The seam study's key claim is that the inner contour never changes; only the
set of 2x2 sample points that lands inside it changes. This script prints the
sample columns that fall inside the hole as a function of the horizontal
translation, the blind intervals, and the visible/hidden classification at the
three offsets the report was checked against.

Usage: python3 lattice.py [--step 0.01]
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=float, default=0.01)
    args = ap.parse_args()

    out = {
        "inner_x_range": [22.1, 22.4],
        "sample_x_columns_considered": [22.25, 22.75, 23.25],
        "sample_spacing_px": 0.5,
        "hole_width_px": 0.3,
        "condition": "visible(dx) <=> exists sample column c with 22.1+dx < c < 22.4+dx, "
                     "c in {22.25, 22.75, 23.25}",
        "blind_bands": [
            {"from": a, "to": b} for a, b in analyze.blind_bands()
        ],
        "blind_fraction_of_period": round(sum(b - a for a, b in analyze.blind_bands()), 6),
        "offsets": {},
    }
    for dx in (0.0, 0.25, 0.5):
        out["offsets"][f"{dx:g}"] = {
            "hit_columns": analyze.lattice_model(dx),
            "visible": analyze.visible(dx),
        }
    out["sweep_step"] = args.step
    out["sweep"] = [
        {"dx": round(i * args.step, 6),
         "visible": analyze.visible(i * args.step),
         "hit_columns": analyze.lattice_model(i * args.step)}
        for i in range(int(round(1 / args.step)) + 1)
    ]
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
