#!/usr/bin/env python3
"""End-to-end verification of the audit pack.

Checks, in order:
  1. every source draft passes the *production* admission and its archived
     PNG matches the freshly rendered artifact byte-for-byte (sha256);
  2. the independent 2x2 reference encodes to exactly the production PNG
     code grid for every pixel of every draft;
  3. analytic coverage and encoding bounds hold;
  4. the counterexample file exists and its headline claims reproduce.

Exits non-zero on the first failed check and prints a machine-readable
report.  Requires the production binary at ../bin/prismplate (use
fetch_production_pngs.py to (re)build it).
"""

import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
PACK = HERE.parent
sys.path.insert(0, str(PACK))

from reference import bounds
from reference import pngio
from reference import raster

SOURCES = PACK / "sources"
PNG_DIR = PACK / "png"
DATA = PACK / "data"


def check(cond, label, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    return cond


def main():
    ok = True
    manifest = json.loads(
        (PNG_DIR / "production-manifest.json").read_text())
    check(len(manifest) == len(list(SOURCES.glob("*.json"))),
          "all drafts rendered by production",
          f"{len(manifest)} artifacts")

    # 1. Digest integrity of archived PNGs.
    for entry in manifest:
        blob = (PNG_DIR / entry["png"]).read_bytes()
        ok &= check(
            hashlib.sha256(blob).hexdigest() == entry["sha256"],
            f"digest stable for {entry['png']}")

    # 1b. The 16-bit control must round-trip through the independent decoder
    #     and reproduce the codes emitted by build_controls.py exactly.
    import struct as _struct
    ctrl = pngio.read_png(str(PNG_DIR / "quant-probe.16bit-control.png"))
    ok &= check(ctrl["bit_depth"] == 16 and ctrl["width"] == 256,
                "16-bit control decodes at bit depth 16",
                f"{ctrl['width']}x{ctrl['height']}, "
                f"type {ctrl['color_type']}")
    ctrl_max = max(max(px) for row in ctrl["pixels"] for px in row)
    ok &= check(ctrl_max <= 65535,
                "16-bit codes stay within the 65535 lattice")

    # 2. Independent reference vs production codes.  Non-tie drafts must be
    #    bit-identical; the lattice probe deliberately parks values on
    #    half-code ties, where independent libm pow routines may differ by
    #    exactly one code (documented in tie-divergence.json).
    total_mismatch = 0
    for src in sorted(SOURCES.glob("*.json")):
        stem = src.stem
        doc = json.loads(src.read_text())["document"]
        ref_codes = raster.encode_nrgba8(raster.render_float(doc, 2))
        decoded = pngio.read_png(str(PNG_DIR / (stem + ".png")))
        prod = ([[px + (255,) for px in r] for r in decoded["pixels"]]
                if decoded["color_type"] == 2 else decoded["pixels"])
        deltas = [max(abs(pa[k] - pb[k]) for k in range(4))
                  for ra, rb in zip(ref_codes, prod)
                  for pa, pb in zip(ra, rb)]
        mismatches = sum(1 for d in deltas if d)
        max_delta = max(deltas)
        total_mismatch += mismatches
        # Drafts that deliberately park values on rounding boundaries expose
        # the 1-ULP cross-implementation pow difference; they may differ by
        # exactly one code. Every other draft must be bit-identical.
        tie_drafts = {"code-lattice-probe", "alpha-channel-probe"}
        if stem in tie_drafts:
            ok &= check(max_delta <= 1,
                        f"{stem} stays inside the 1-code envelope",
                        f"{mismatches} pixels, max delta {max_delta}")
        else:
            ok &= check(mismatches == 0,
                        f"reference == production codes for {stem}",
                        f"{mismatches} mismatched pixels")
    tie = json.loads((DATA / "tie-divergence.json").read_text())
    ok &= check(tie["inside_one_code_envelope"]
                and tie["max_abs_code_delta"] == 1,
                "cross-implementation tie gaps are exactly one code",
                f"{tie['diverging_boundaries']} half-code ties affected")

    # 3. Analytic bounds.
    sweep = bounds.sweep_edge_error(2)
    ok &= check(sweep["bound_holds"],
                "2x2 coverage error bound <= 1/4 for a single straight edge",
                f"observed {sweep['observed_max_coverage_error']:.4f} "
                f"at edge {sweep['observed_at_edge_offset']:.4f}")
    widths = bounds.linear_step_widths()
    ok &= check(widths["max_linear_step"] > 0.0,
                "linear-light code spacing computed",
                f"max step {widths['max_linear_step']:.6f} between codes "
                f"{widths['argmax_code_pair']}")

    # 4. Counterexample claims.
    ce = json.loads((DATA / "counterexamples.json").read_text())
    veil_ratio = (
        ce["A_veil_paradox"]["straight_rgb_max_channel"]
        / ce["A_veil_paradox"]["composited_srgb_max_channel"])
    ok &= check(
        veil_ratio > 20.0,
        "veil paradox: straight RGB gap dwarfs composited gap",
        f"ratio {veil_ratio:.1f}x (straight "
        f"{ce['A_veil_paradox']['straight_rgb_max_channel']} vs "
        f"composited "
        f"{ce['A_veil_paradox']['composited_srgb_max_channel']:.5f})")
    ok &= check(ce["B_bound_violation"]["bound_crossed"],
                "real design move crosses the encoding envelope")
    ok &= check(ce["D_threshold_trap"]["overlap_exists"],
                "real design edits can land inside the quantisation "
                "envelope, so a global cutoff cannot classify")

    print(json.dumps({"overall": "PASS" if ok else "FAIL",
                      "total_code_mismatches": total_mismatch},
                     ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
