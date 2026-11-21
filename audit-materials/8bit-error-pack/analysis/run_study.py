#!/usr/bin/env python3
"""Stage-wise error decomposition for the 8-bit PNG audit.

Pipeline per draft (all arithmetic in the independent reference package):

  S0 continuous reference  : dense-grid (16x16) float premultiplied pixels
  S1 coverage estimator    : production 2x2 grid, float premultiplied
  S2 transfer only         : S1 unpremultiplied through binary64 sRGB, no round
  S3 8-bit encoded         : S2 rounded to the 0..255 lattice
  P  production artifact   : the actual PNG emitted by the Go service

Stage differences isolate one mechanism each:
  E_coverage  = ||S1 - S0||  (sampling/coverage; S0 is an estimator, see docs)
  E_transfer  = ||S2 - S1||  (linear->sRGB float round-trip representation)
  E_encoding  = ||S3 - S2||  (8-bit lattice rounding)
  E_reference = ||S3 - P||   (independent implementation vs production)

Metrics:
  * premultiplied linear-light RMSE/max (physical), per channel;
  * code-space deltas for E_encoding and E_reference;
  * post-compositing sRGB Euclidean and CIEDE2000 over declared backdrops.

Outputs machine-readable JSON into ../data/.
"""

import json
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
PACK = HERE.parent
sys.path.insert(0, str(PACK))

from reference import composite as comp
from reference import metrics as met
from reference import pngio
from reference import raster
from reference import srgb

SOURCES = PACK / "sources"
PNG_DIR = PACK / "png"
DATA = PACK / "data"

# Backdrops declared for the "flatten before judging" rule.
BACKDROPS = {
    "dark": (0.04, 0.05, 0.08),
    "mid": (0.5, 0.5, 0.5),
    "light": (0.93, 0.94, 0.95),
}
DENSE_GRID = 8
CONVERGENCE_GRID = 4


def load_doc(name):
    return json.loads((SOURCES / name).read_text())


def premul_stats(grid_a, grid_b):
    """RMSE/max on premultiplied linear channels, plus coverage-alpha stats."""
    sums = [0.0, 0.0, 0.0, 0.0]
    maxv = [0.0] * 4
    count = 0
    for row_a, row_b in zip(grid_a, grid_b):
        for pa, pb in zip(row_a, row_b):
            for k, (va, vb) in enumerate(
                    ((pa.r, pb.r), (pa.g, pb.g),
                     (pa.b, pb.b), (pa.a, pb.a))):
                d = va - vb
                sums[k] += d * d
                maxv[k] = max(maxv[k], abs(d))
            count += 1
    keys = ("r_premul", "g_premul", "b_premul", "alpha")
    return {
        "rmse": {k: math.sqrt(s / count) for k, s in zip(keys, sums)},
        "max_abs": {k: m for k, m in zip(keys, maxv)},
    }


def codes_to_premul(codes_grid):
    """Reconstruct float premultiplied pixels from 8-bit NRGBA codes."""
    out = []
    for row in codes_grid:
        new_row = []
        for codes in row:
            r8, g8, b8, a8 = codes
            if a8 == 0:
                new_row.append(comp.Premul(0.0, 0.0, 0.0, 0.0))
                continue
            straight = comp.Straight(r8 / 255.0, g8 / 255.0,
                                     b8 / 255.0, a8 / 255.0)
            new_row.append(comp.Premul.from_straight(straight))
        out.append(new_row)
    return out


def s2_grid(grid_2x2):
    """S2: unpremul through float sRGB, then re-premultiply (no rounding)."""
    out = []
    for row in grid_2x2:
        new_row = []
        for px in row:
            if px.a <= 0.0:
                new_row.append(comp.Premul(0.0, 0.0, 0.0, 0.0))
                continue
            st = px.unpremul()
            new_row.append(comp.Premul.from_straight(st))
        out.append(new_row)
    return out


def flatten_grid(grid, backdrop):
    bd = comp.Straight(*backdrop, 1.0)
    return [[comp.flatten_over_backdrop(px, bd) for px in row]
            for row in grid]


def straight_pair_stats(grid_a, grid_b, backdrop, metric):
    fa, fb = flatten_grid(grid_a, backdrop), flatten_grid(grid_b, backdrop)
    vals = []
    for ra, rb in zip(fa, fb):
        for ca, cb in zip(ra, rb):
            if metric == "de00":
                vals.append(met.delta_e00(
                    (ca.r, ca.g, ca.b), (cb.r, cb.g, cb.b)))
            else:
                vals.append(math.sqrt(
                    (ca.r - cb.r) ** 2 + (ca.g - cb.g) ** 2
                    + (ca.b - cb.b) ** 2))
    return {
        "max": max(vals),
        "mean": sum(vals) / len(vals),
        "p99": sorted(vals)[int(0.99 * (len(vals) - 1))],
    }


def analyse(stem):
    envelope = load_doc(stem + ".json")
    doc = envelope["document"]
    s0 = raster.render_float(doc, DENSE_GRID)
    s1 = raster.render_float(doc, 2)
    s2 = s2_grid(s1)
    s3_codes = raster.encode_nrgba8(s1)
    s3 = codes_to_premul(s3_codes)

    prod = pngio.read_png(str(PNG_DIR / (stem + ".png")))
    if prod["color_type"] == 2:
        # Opaque RGB PNG: Go's encoder drops the alpha channel entirely.
        prod_codes = [[px + (255,) for px in row] for row in prod["pixels"]]
    else:
        prod_codes = prod["pixels"]

    result = {
        "draft": stem,
        "canvas": [doc["width"], doc["height"]],
        "dense_grid": f"{DENSE_GRID}x{DENSE_GRID} (reference estimator)",
        "stages": {
            "E_coverage_2x2_vs_16x16_linear": premul_stats(s1, s0),
            "E_transfer_float_srgb_roundtrip_linear": premul_stats(s2, s1),
            "E_encoding_8bit_lattice_linear": premul_stats(s3, s2),
        },
        "code_space": {
            "independent_s3_vs_production_png": met.code_stats(
                s3_codes, prod_codes),
        },
        "post_composite": {},
    }

    # Encoding error measured where callers actually see it: after flatten.
    for bd_name, bd in BACKDROPS.items():
        result["post_composite"][bd_name] = {
            "E_encoding_srgb_unit":
                straight_pair_stats(s3, s2, bd, "srgb_euclid"),
            "E_encoding_deltaE00":
                straight_pair_stats(s3, s2, bd, "de00"),
            "E_coverage_deltaE00":
                straight_pair_stats(s1, s0, bd, "de00"),
        }

    # Convergence check for the coverage estimator: 4x4 vs 8x8.
    s0_4 = raster.render_float(doc, CONVERGENCE_GRID)
    result["estimator_convergence_4_vs_8_linear"] = premul_stats(s0_4, s0)
    return result


def main():
    DATA.mkdir(exist_ok=True)
    summary = []
    for src in sorted(SOURCES.glob("*.json")):
        stem = src.stem
        result = analyse(stem)
        (DATA / f"{stem}.stages.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        enc = result["stages"]["E_encoding_8bit_lattice_linear"]
        cov = result["stages"]["E_coverage_2x2_vs_16x16_linear"]
        ref = result["code_space"]["independent_s3_vs_production_png"]
        summary.append({
            "draft": stem,
            "encoding_max_linear": enc["max_abs"],
            "encoding_rmse_linear": enc["rmse"],
            "coverage_max_linear": cov["max_abs"],
            "coverage_rmse_linear": cov["rmse"],
            "reference_vs_production_mismatched_pixels":
                ref["pixels_different"],
            "reference_vs_production_max_code_delta":
                ref["max_rgb_code_delta"],
        })
        print(f"analysed {stem}: mismatched pixels vs production = "
              f"{ref['pixels_different']}")
    (DATA / "stage-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
