#!/usr/bin/env python3
"""Export per-pixel continuous reference data for the quant-probe draft.

For a curated set of pixels covering every regime (opaque interior, veil
band, screen/multiply glaze, fractional edge, cubic blade, nested stack),
this exports the full numeric lineage:

  * dense-grid continuous estimate S0 (premul linear, 8x8);
  * production-grid float S1 (premul linear, 2x2);
  * transfer-only S2 (premul linear after float sRGB round-trip);
  * 8-bit codes S3 from the independent reference;
  * codes P from the actual production PNG;
  * both candidates flattened over the three declared backdrops, with
    sRGB Euclidean and CIEDE2000 distances.

Outputs CSV (human/grep friendly) and JSONL (machine friendly) into
../data/.  Values are full binary64 repr, never pre-rounded.
"""

import csv
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
PACK = HERE.parent
sys.path.insert(0, str(PACK))

from reference import composite as comp
from reference import metrics as met
from reference import pngio
from reference import raster

DATA = PACK / "data"
PNG_DIR = PACK / "png"
STEM = "quant-probe"

# (x, y, regime label) — coordinates chosen from the draft geometry.
PROBES = [
    (4, 4, "veil band over dark bg, alpha ramp interior"),
    (128, 4, "veil band over light panel boundary"),
    (200, 30, "veil band over light panel"),
    (40, 90, "screen glaze interior (masked)"),
    (4, 90, "dark backdrop outside mask"),
    (180, 90, "multiply glaze over light panel"),
    (60, 150, "fractional edge band quarter-offset"),
    (150, 150, "fractional edge band third-offset"),
    (60, 180, "cubic blade interior"),
    (210, 160, "nested stack overlap"),
    (250, 190, "light panel corner, opaque"),
    (2, 2, "dark backdrop corner, opaque"),
]
BACKDROPS = {
    "dark": (0.04, 0.05, 0.08),
    "mid": (0.5, 0.5, 0.5),
    "light": (0.93, 0.94, 0.95),
}


def s2_px(px):
    if px.a <= 0.0:
        return comp.Premul(0.0, 0.0, 0.0, 0.0)
    return comp.Premul.from_straight(px.unpremul())


def main():
    doc = json.loads((PACK / "sources" / (STEM + ".json")).read_text())[
        "document"]
    s0 = raster.render_float(doc, 8)
    s1 = raster.render_float(doc, 2)
    s3_codes = raster.encode_nrgba8(s1)
    decoded = pngio.read_png(str(PNG_DIR / (STEM + ".png")))
    prod = ([[px + (255,) for px in r] for r in decoded["pixels"]]
            if decoded["color_type"] == 2 else decoded["pixels"])

    rows = []
    for x, y, label in PROBES:
        p0, p1 = s0[y][x], s1[y][x]
        p2 = s2_px(p1)
        c3, cp = s3_codes[y][x], prod[y][x]

        def flatten(px, bd):
            return comp.flatten_over_backdrop(
                px, comp.Straight(*bd, 1.0))

        def codes_to_premul(codes):
            if codes[3] == 0:
                return comp.Premul(0.0, 0.0, 0.0, 0.0)
            return comp.Premul.from_straight(comp.Straight(
                codes[0] / 255.0, codes[1] / 255.0,
                codes[2] / 255.0, codes[3] / 255.0))

        flat = {}
        for name, bd in BACKDROPS.items():
            f2 = flatten(p2, bd)
            f3 = flatten(codes_to_premul(c3), bd)
            flat[name] = {
                "S2_flat": [f2.r, f2.g, f2.b],
                "S3_flat": [f3.r, f3.g, f3.b],
                "srgb_euclid": (
                    sum((f2.__dict__[k] - f3.__dict__[k]) ** 2
                        for k in ("r", "g", "b"))) ** 0.5,
                "deltaE00": met.delta_e00(
                    (f2.r, f2.g, f2.b), (f3.r, f3.g, f3.b)),
            }

        rows.append({
            "x": x, "y": y, "regime": label,
            "S0_dense8_premul_linear": [p0.r, p0.g, p0.b, p0.a],
            "S1_grid2_premul_linear": [p1.r, p1.g, p1.b, p1.a],
            "S2_transfer_premul_linear": [p2.r, p2.g, p2.b, p2.a],
            "S3_independent_codes": list(c3),
            "P_production_png_codes": list(cp),
            "code_match": list(c3) == list(cp),
            "flattened": flat,
        })

    (DATA / "pixel-reference.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")

    with (DATA / "pixel-reference.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "x", "y", "regime",
            "S0_r", "S0_g", "S0_b", "S0_a",
            "S1_r", "S1_g", "S1_b", "S1_a",
            "S2_r", "S2_g", "S2_b", "S2_a",
            "S3_r8", "S3_g8", "S3_b8", "S3_a8",
            "P_r8", "P_g8", "P_b8", "P_a8", "code_match",
            "flat_dark_srgb_euclid", "flat_dark_dE00",
            "flat_mid_srgb_euclid", "flat_mid_dE00",
            "flat_light_srgb_euclid", "flat_light_dE00",
        ])
        for r in rows:
            writer.writerow([
                r["x"], r["y"], r["regime"],
                *[f"{v:.17g}" for v in r["S0_dense8_premul_linear"]],
                *[f"{v:.17g}" for v in r["S1_grid2_premul_linear"]],
                *[f"{v:.17g}" for v in r["S2_transfer_premul_linear"]],
                *r["S3_independent_codes"], *r["P_production_png_codes"],
                r["code_match"],
                f"{r['flattened']['dark']['srgb_euclid']:.17g}",
                f"{r['flattened']['dark']['deltaE00']:.17g}",
                f"{r['flattened']['mid']['srgb_euclid']:.17g}",
                f"{r['flattened']['mid']['deltaE00']:.17g}",
                f"{r['flattened']['light']['srgb_euclid']:.17g}",
                f"{r['flattened']['light']['deltaE00']:.17g}",
            ])
    print(f"exported {len(rows)} probe pixels to data/pixel-reference.*")


if __name__ == "__main__":
    main()
