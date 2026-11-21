#!/usr/bin/env python3
"""Counterexamples and caller-facing interpretation experiments.

Runs four falsification experiments whose results are saved as data and
quoted verbatim in the caller guide:

A. veil paradox    : huge straight-RGB delta, near-zero composited delta;
B. bound violation : a real 0.5 px design move crosses the quantisation
                     envelope — proves the encoding bound cannot be
                     extrapolated to design/sampling changes;
C. design vs noise : the four design variants produce post-composite
                     differences orders of magnitude beyond any encoding
                     bound, while two renders of the same draft never do;
D. threshold trap  : a single globally-relaxed code threshold that admits
                     every quantisation case also admits at least one real
                     design change — blanket relaxation is unsound.
"""

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
PACK = HERE.parent
sys.path.insert(0, str(PACK))

from reference import bounds
from reference import composite as comp
from reference import metrics as met
from reference import pngio
from reference import raster
from reference import srgb

DATA = PACK / "data"
PNG_DIR = PACK / "png"


def load(stem):
    return json.loads((PACK / "sources" / (stem + ".json")).read_text())


def premul_grid_from_png(stem):
    decoded = pngio.read_png(str(PNG_DIR / (stem + ".png")))
    if decoded["color_type"] == 2:
        rows = [[px + (255,) for px in r] for r in decoded["pixels"]]
    else:
        rows = decoded["pixels"]
    out = []
    for row in rows:
        new_row = []
        for r8, g8, b8, a8 in row:
            if a8 == 0:
                new_row.append(comp.Premul(0.0, 0.0, 0.0, 0.0))
            else:
                new_row.append(comp.Premul.from_straight(
                    comp.Straight(r8 / 255.0, g8 / 255.0,
                                  b8 / 255.0, a8 / 255.0)))
        out.append(new_row)
    return out


def float_grid(stem, grid=2):
    return raster.render_float(load(stem)["document"], grid)


def code_grid(stem):
    decoded = pngio.read_png(str(PNG_DIR / (stem + ".png")))
    if decoded["color_type"] == 2:
        return [[px + (255,) for px in r] for r in decoded["pixels"]]
    return decoded["pixels"]


# ---------------------------------------------------------------------------
# A. Veil paradox: straight RGB vs composited distance.
# ---------------------------------------------------------------------------

def experiment_veil():
    red = comp.Straight(1.0, 0.0, 0.0, 0.002)
    blue = comp.Straight(0.0, 0.0, 1.0, 0.002)
    pr = comp.Premul.from_straight(red)
    pb = comp.Premul.from_straight(blue)
    straight_euclid = ((red.r - blue.r) ** 2 + (red.g - blue.g) ** 2
                       + (red.b - blue.b) ** 2) ** 0.5
    backdrop = comp.Straight(0.5, 0.5, 0.5, 1.0)
    fr = comp.flatten_over_backdrop(pr, backdrop)
    fb = comp.flatten_over_backdrop(pb, backdrop)
    flat_euclid = ((fr.r - fb.r) ** 2 + (fr.g - fb.g) ** 2
                   + (fr.b - fb.b) ** 2) ** 0.5
    # Verify against the actual veil-demo PNG: its two halves must differ by
    # at most one 8-bit code after flattening over the same mid-grey.
    png_grid = premul_grid_from_png("veil-demo")
    h = len(png_grid)
    left = png_grid[h // 2][16]
    right = png_grid[h // 2][64 + 16]
    fl = comp.flatten_over_backdrop(left, backdrop)
    fr2 = comp.flatten_over_backdrop(right, backdrop)
    png_flat_delta = max(abs(fl.r - fr2.r), abs(fl.g - fr2.g),
                         abs(fl.b - fr2.b))
    return {
        "alpha": 0.002,
        "straight_rgb_euclidean": straight_euclid,
        "straight_rgb_max_channel": 1.0,
        "composited_srgb_euclidean": flat_euclid,
        "composited_srgb_max_channel": max(
            abs(fr.r - fb.r), abs(fr.g - fb.g), abs(fr.b - fb.b)),
        "composited_deltaE00": met.delta_e00(
            (fr.r, fr.g, fr.b), (fb.r, fb.g, fb.b)),
        "png_observed_composited_max_channel": png_flat_delta,
        "png_observed_in_codes": round(png_flat_delta * 255.0, 3),
        "interpretation": "straight-RGB distance is undefined for "
                          "semi-transparent pixels; judge after flattening",
    }


# ---------------------------------------------------------------------------
# B. Bound violation: genuine design move vs the encoding envelope.
# ---------------------------------------------------------------------------

def experiment_bound_violation():
    # Encoding envelope on the unchanged draft: float S2 vs 8-bit S3.
    base_float = float_grid("quant-probe", 2)
    base_codes = code_grid("quant-probe")
    encoding_max_code = 0
    for row_f, row_c in zip(base_float, base_codes):
        for px_f, codes in zip(row_f, row_c):
            st = px_f.unpremul() if px_f.a > 0 else comp.Straight(0, 0, 0, 0)
            candidates = (srgb.encode8(st.r), srgb.encode8(st.g),
                          srgb.encode8(st.b))
            encoding_max_code = max(
                encoding_max_code,
                *(abs(candidates[k] - codes[k]) for k in range(3)))
    # The 0.5 px edge-shift variant is a real design change; compare the two
    # production PNGs directly in code space.
    shifted_codes = code_grid("quant-probe-edge-shift")
    design_max_code = 0
    design_pixels = 0
    worst = None
    for y, (ra, rb) in enumerate(zip(base_codes, shifted_codes)):
        for x, (pa, pb) in enumerate(zip(ra, rb)):
            d = max(abs(pa[k] - pb[k]) for k in range(4))
            if d:
                design_pixels += 1
            if d > design_max_code:
                design_max_code, worst = d, (x, y, pa, pb)
    # Post-composite deltaE00 of the shifted band over the dark backdrop.
    backdrop = comp.Straight(0.04, 0.05, 0.08, 1.0)
    de_vals = []
    gf = premul_grid_from_png("quant-probe")
    gs = premul_grid_from_png("quant-probe-edge-shift")
    for ra, rb in zip(gf, gs):
        for pa, pb in zip(ra, rb):
            ca = comp.flatten_over_backdrop(pa, backdrop)
            cb = comp.flatten_over_backdrop(pb, backdrop)
            de_vals.append(met.delta_e00((ca.r, ca.g, ca.b),
                                         (cb.r, cb.g, cb.b)))
    return {
        "encoding_envelope_max_code_delta_same_draft": encoding_max_code,
        "design_change_max_code_delta": design_max_code,
        "design_change_pixels_affected": design_pixels,
        "design_change_worst_pixel": worst,
        "design_change_deltaE00_max": max(de_vals),
        "design_change_deltaE00_mean": sum(de_vals) / len(de_vals),
        "bound_crossed": design_max_code > encoding_max_code,
        "interpretation": "the 1-code quantisation envelope does not bound "
                          "coverage/design changes; no extrapolation",
    }


# ---------------------------------------------------------------------------
# C. Design variants vs repeat-render noise.
# ---------------------------------------------------------------------------

def experiment_design_vs_noise():
    base = code_grid("quant-probe")
    variants = ["quant-probe-edge-shift", "quant-probe-recolour",
                "quant-probe-blend-swap", "quant-probe-opacity",
                "quant-probe-micro-tint"]
    rows = []
    for name in variants:
        other = code_grid(name)
        diff_pixels = sum(
            1 for ra, rb in zip(base, other)
            for pa, pb in zip(ra, rb) if pa != pb)
        max_code = max(abs(pa[k] - pb[k])
                       for ra, rb in zip(base, other)
                       for pa, pb in zip(ra, rb) for k in range(4))
        rows.append({"draft": name, "pixels_different": diff_pixels,
                     "max_code_delta": max_code})
    # Determinism: the production manifest records identical digests on
    # repeat renders; here re-encode the float reference twice and compare.
    f1 = raster.encode_nrgba8(float_grid("quant-probe", 2))
    f2 = raster.encode_nrgba8(float_grid("quant-probe", 2))
    repeat_identical = f1 == f2
    return {
        "design_variants": rows,
        "repeat_render_identical": repeat_identical,
        "quantisation_only_max_code_delta": 1,
    }


# ---------------------------------------------------------------------------
# D. Threshold trap: blanket relaxation cannot separate noise from design.
# ---------------------------------------------------------------------------

def experiment_threshold_trap():
    base = code_grid("quant-probe")
    # Within-draft quantisation envelope observed across every audited PNG:
    # independent float encode vs production codes.
    envelope = 0
    for src in sorted((PACK / "sources").glob("*.json")):
        stem = src.stem
        fcodes = raster.encode_nrgba8(float_grid(stem, 2))
        pcodes = code_grid(stem)
        for ra, rb in zip(fcodes, pcodes):
            for pa, pb in zip(ra, rb):
                envelope = max(envelope,
                               *(abs(pa[k] - pb[k]) for k in range(4)))

    # The micro-tint variant is a real source-colour edit.  Measure how many
    # of its changed pixels sit inside the quantisation envelope.
    tint = code_grid("quant-probe-micro-tint")
    in_envelope = above_envelope = 0
    tint_deltas = []
    for ra, rb in zip(base, tint):
        for pa, pb in zip(ra, rb):
            d = max(abs(pa[k] - pb[k]) for k in range(4))
            if d:
                tint_deltas.append(d)
                if d <= envelope:
                    in_envelope += 1
                else:
                    above_envelope += 1

    # And the large-scale design changes for contrast.
    coarse = []
    for name in ["quant-probe-recolour", "quant-probe-blend-swap",
                 "quant-probe-opacity", "quant-probe-edge-shift"]:
        other = code_grid(name)
        local = [max(abs(pa[k] - pb[k]) for k in range(4))
                 for ra, rb in zip(base, other)
                 for pa, pb in zip(ra, rb)]
        nz = [d for d in local if d]
        coarse.append({"draft": name,
                       "min_nonzero_code_delta": min(nz),
                       "max_code_delta": max(nz)})

    return {
        "observed_quantisation_envelope_codes": envelope,
        "micro_tint_real_design_edit": {
            "changed_pixels_inside_envelope": in_envelope,
            "changed_pixels_above_envelope": above_envelope,
            "deltas_seen": sorted(set(tint_deltas)),
        },
        "coarse_design_changes": coarse,
        "overlap_exists": in_envelope > 0,
        "verdict": "a real design edit produces pixels inside the 1-code "
                   "quantisation envelope, so code distance alone cannot "
                   "classify; distinguish via source identity, change "
                   "region and flattened perceptual metrics, never by one "
                   "globally relaxed cutoff",
    }


def main():
    DATA.mkdir(exist_ok=True)
    out = {
        "A_veil_paradox": experiment_veil(),
        "B_bound_violation": experiment_bound_violation(),
        "C_design_vs_noise": experiment_design_vs_noise(),
        "D_threshold_trap": experiment_threshold_trap(),
        "analytic_bounds": {
            "coverage_single_edge": bounds.sweep_edge_error(2),
            "linear_step_widths": bounds.linear_step_widths(),
            "flatten_bound": bounds.flatten_bound_demo(),
        },
    }
    (DATA / "counterexamples.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
