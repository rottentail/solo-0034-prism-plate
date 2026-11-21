#!/usr/bin/env python3
"""Derive the readability record from service-rendered pixels.

Inputs are only files under out/ that the service produced. The analyzer never
renders and never reimplements the renderer; it composites straight-alpha PNGs
over the page backdrops and applies measure.py.
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import design  # noqa: E402
import measure  # noqa: E402
import pngio  # noqa: E402

OUT = ROOT / "out"
REC = ROOT / "record"
PAGES = ["light", "dark", "checker", "mid"]


def load(rel):
    return pngio.read(OUT / rel)


def grade_item(it, built, rgba):
    w, h, _ = pngio.read(OUT / it["png"])
    parts = []
    for p in built["parts"]:
        loc = it["local"][p["slot"]]
        _, _, lrgb = load(loc["png"])
        parts.append({
            "slot": p["slot"],
            "mask": p["mask"],            # exclusive pixels only
            "local": lrgb,
            "role": p["role"],
            "decisive": p["slot"] in design.DECISIVE[it["status"]],
        })

    out = {}
    for page in PAGES:
        out[page] = measure.image_grade(w, h, rgba, measure.BACKDROPS[page], parts,
                                        built["masks"] if it["status"] == "pending" else None,
                                        it["dx"], it["dy"])
    return out


def main():
    manifest = json.loads((OUT / "manifest.json").read_text())
    findings = {
        "schema": "prism-icon-findings-v2",
        "model": {
            "compositing": "source-over in sRGB code space, straight alpha from the PNG",
            "contrast": "WCAG 2.x ratio against the local backdrop rendered under the part",
            "thresholds": {
                "non_text_contrast": measure.NON_TEXT_CONTRAST,
                "visible_delta": measure.VISIBLE_DELTA,
                "gray_separation_rms": measure.GRAY_SEPARATION,
                "crisp_opaque_fraction": measure.CRISP_OPAQUE,
            },
            "pages": measure.BACKDROP_HEX,
        },
        "items": [],
        "gray_separation": [],
        "alignment": [],
    }

    for it in manifest["items"]:
        built = design.build(it["status"], it["variant"], it["dx"], it["dy"])
        w, h, rgba = load(it["png"])
        grades = grade_item(it, built, rgba)
        # Decisive part, on its exclusive pixels: a pixel also covered by
        # another ink part says nothing about the decisive stroke's own
        # visibility. The same mask feeds crispness and erasure.
        dm = built["masks"]["hole"] if it["status"] == "pending" else (lambda x, y: False)
        if it["status"] != "pending":
            for part in built["parts"]:
                if part["slot"] in it["decisive"]:
                    dm = part["mask"]
                    break
        crisp = measure.alpha_slot(w, h, rgba, dm) if it["status"] != "pending" else None
        # erasure on every page
        _, _, reduced = load(it["reduced"]["png"])
        erasure = {page: measure.erasure(w, h, rgba, reduced, measure.BACKDROPS[page], dm)
                   for page in PAGES}
        findings["items"].append({
            "status": it["status"], "variant": it["variant"], "offset": it["offset"],
            "dx": it["dx"], "dy": it["dy"], "plate": it["plate"], "png": it["png"],
            "artifact_digest": it["artifact_digest"], "scene_digest": it["scene_digest"],
            "slots": it["slots"], "decisive": it["decisive"],
            "decisive_alpha": crisp, "hole_alpha": it["hole"],
            "pages": grades, "erasure": erasure,
        })

    # grayscale separability of approved vs rejected, every page and every offset
    idx = {(i["status"], i["variant"], i["offset"]): i for i in manifest["items"]}
    for variant in design.VARIANTS:
        for offset in [f"dx{dx:.1f}_dy{dy:.1f}".replace(".", "p") for dx, dy in design.OFFSETS]:
            a = idx[("approved", variant, offset)]
            r = idx[("rejected", variant, offset)]
            _, _, ra = load(a["png"])
            _, _, rr = load(r["png"])
            w, h, _ = load(a["png"])
            union = [max(ra[i * 4 + 3], rr[i * 4 + 3]) > 0 for i in range(w * h)]
            for page in PAGES:
                ca = measure.composite(w, h, ra, measure.BACKDROPS[page])
                cr = measure.composite(w, h, rr, measure.BACKDROPS[page])
                sep = measure.separation(ca, cr, union)
                findings["gray_separation"].append({
                    "variant": variant, "page": page, "offset": offset, **sep,
                })

    # alignment: how the bar's peak opacity and thickness react to a 0.5 px move
    for p in manifest["probes"]:
        findings["alignment"].append({
            "plate": p["plate"], "orientation": p["orientation"], "width": p["width"],
            "dx": p["dx"], "dy": p["dy"],
            "peak_alpha": p["peak_alpha"], "opaque_fraction": p["opaque_fraction"],
            "cross_axis_spans": p["cross_axis_spans"],
        })

    REC.mkdir(parents=True, exist_ok=True)
    (REC / "findings.json").write_text(json.dumps(findings, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    rows = []
    for it in findings["items"]:
        for page in PAGES:
            g = it["pages"][page]
            rows.append({
                "status": it["status"], "variant": it["variant"], "offset": it["offset"],
                "page": page,
                "core_contrast": g["core_contrast"], "decisive_contrast": g["decisive_contrast"],
                "coverage": g["coverage"], "mean_mark_delta": g["mean_mark_delta"],
                "decisive_peak_alpha": (it["decisive_alpha"] or {}).get("peak_alpha", ""),
                "decisive_opaque_fraction": (it["decisive_alpha"] or {}).get("opaque_fraction", ""),
                "hole_max_alpha": (it["hole_alpha"] or {}).get("max_alpha", ""),
                "erasure_decisive_max": it["erasure"][page]["decisive_max_delta"],
                "erasure_other_max": it["erasure"][page]["other_max_delta"],
            })
    with (REC / "measurements.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)
    print(f"items={len(findings['items'])} gray={len(findings['gray_separation'])} rows={len(rows)}")


if __name__ == "__main__":
    main()
