#!/usr/bin/env python3
"""Derive the terminal decision criteria from the measured record.

Nothing here is asserted by hand: each (variant, page, offset) cell is graded
from record/findings.json and the terminal rule is applied to the grades.
Changing a threshold in one place re-derives the eligibility table.

The rule answers three questions per cell:
  1. is the icon visible at all on this page?          -> ink contrast
  2. if it is, does the decisive marker survive?       -> decisive contrast
  3. which of the two loss modes happened?
       whole-icon dimmed  : ink contrast fails, decisive fails with it
       decisive erased    : ink contrast holds, decisive contrast fails and
                            the page already looks like a render with the
                            decisive part removed (erasure probe corroborates)
Sub-pixel loss is a fourth, separate mode: the decisive part is visible but
has been split across a pixel boundary so far that most of its pixels are no
longer fully opaque.
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

import design  # noqa: E402

OUT = ROOT / "out"
REC = ROOT / "record"
POL = ROOT / "policy"

REQUIRED_PAGES = ["light", "dark", "checker"]   # the three composites named in the brief
PAGES = ["light", "dark", "checker", "mid"]     # mid grey is an extra stress page
OFFSETS = ["dx0p0_dy0p0", "dx0p5_dy0p0", "dx0p0_dy0p5", "dx0p5_dy0p5"]
INTEGER = {"dx0p0_dy0p0"}

T_CONTRAST = 3.0      # WCAG 2.x non-text graphics floor
T_GRAY = 18.0         # approved/rejected grayscale RMS, code values
T_ERASE = 24.0        # erasure probe: "the reduced render looks the same here"
T_OPAQUE = 0.5        # decisive part: at least half its exclusive pixels fully opaque
T_MEAN_ALPHA = 200    # ...and their average alpha

# Which construction is allowed to claim "one asset for every page":
# a transparent asset must hold on all required pages; the badge carries its
# own opaque backing, so it is called out separately rather than passed off as
# a transparent icon.
TRANSPARENT = {"plain-ink", "plain-paper", "mid", "fine", "hair", "thread",
               "halo", "duplex", "erasure-light", "erasure-dark"}


def cell_grade(it, page):
    g = it["pages"][page]
    e = it["erasure"][page]
    ink = None
    dec = g["decisive_contrast"]
    if it["status"] == "pending":
        hole = g["slots"].get("hole", {})
        ink = g["core_contrast"]           # the ring, the only ordinary ink
        dec = hole.get("p75_contrast")     # the hole marker is the decisive region
        hollow = bool(it["hole_alpha"] and it["hole_alpha"]["max_alpha"] == 0)
    else:
        ink = g["core_contrast"]
        hollow = None

    ink_ok = ink is not None and ink >= T_CONTRAST
    dec_ok = dec is not None and dec >= T_CONTRAST
    ink_lost = not ink_ok
    dec_lost = not dec_ok

    # erasure corroboration: does this page make the full render indistinguishable
    # from the reduced render inside the decisive region?
    erased_inside = (e["decisive_mean_delta"] is not None and e["decisive_mean_delta"] < T_ERASE)
    eye = it["decisive_alpha"]
    crisp_ok = True
    if it["status"] != "pending" and eye is not None:
        crisp_ok = eye["opaque_fraction"] >= T_OPAQUE and eye["mean_alpha"] >= T_MEAN_ALPHA

    if ink_lost and dec_lost:
        mode = "whole-icon-lost"
    elif dec_lost:
        mode = "decisive-erased" if erased_inside else "decisive-lost-unexplained"
    elif not crisp_ok:
        mode = "decisive-diluted-by-subpixel"
    else:
        mode = "intact"

    reasons = []
    if hollow is False:
        reasons.append("hole_not_hollow")
    if ink_lost:
        reasons.append("ink_below_contrast")
    if dec_lost:
        reasons.append("decisive_below_contrast")
    if erased_inside and dec_lost and ink_ok:
        reasons.append("erasure_probe_confirms_decisive_erased")
    if not crisp_ok:
        reasons.append("decisive_crispness_below_floor")
    return {
        "mode": mode,
        "ink_contrast": ink,
        "decisive_contrast": dec,
        "erasure_inside_mean": e["decisive_mean_delta"],
        "erasure_outside_max": e["other_max_delta"],
        "decisive_opaque_fraction": (eye or {}).get("opaque_fraction"),
        "decisive_mean_alpha": (eye or {}).get("mean_alpha"),
        "reasons": reasons,
    }


def main():
    findings = json.loads((REC / "findings.json").read_text())
    items = {(i["status"], i["variant"], i["offset"]): i for i in findings["items"]}
    sep = {(g["variant"], g["page"], g["offset"]): g for g in findings["gray_separation"]}

    cells = []
    for variant in design.VARIANTS:
        for page in PAGES:
            for offset in OFFSETS:
                grades = {}
                reasons = []
                for status in design.STATUSES:
                    grades[status] = cell_grade(items[(status, variant, offset)], page)
                    reasons.extend(f"{status}:{r}" for r in grades[status]["reasons"])
                gray = sep[(variant, page, offset)]
                if gray["rms"] < T_GRAY:
                    reasons.append("approved_vs_rejected_gray_rms_below_floor")
                modes = sorted({g["mode"] for g in grades.values()})
                cells.append({
                    "variant": variant,
                    "page": page,
                    "offset": offset,
                    "integer_placement": offset in INTEGER,
                    "modes": modes,
                    "verdict": "pass" if not reasons else "fail",
                    "reasons": sorted(set(reasons)),
                    "gray_rms": gray["rms"],
                    "statuses": grades,
                })

    rollup = []
    for variant in design.VARIANTS:
        per_page = {}
        for page in PAGES:
            rows = [c for c in cells if c["variant"] == variant and c["page"] == page]
            per_page[page] = {
                "pass_integer": all(c["verdict"] == "pass" for c in rows if c["offset"] in INTEGER),
                "pass_all_offsets": all(c["verdict"] == "pass" for c in rows),
                "failing_offsets": sorted({c["offset"] for c in rows if c["verdict"] == "fail"}),
                "modes": sorted({m for c in rows for m in c["modes"]}),
                "reasons": sorted({r for c in rows for r in c["reasons"]}),
            }
        pages_ok = [p for p in PAGES if per_page[p]["pass_all_offsets"]]
        required_ok = [p for p in REQUIRED_PAGES if p in pages_ok]
        if len(required_ok) == len(REQUIRED_PAGES) and variant in TRANSPARENT:
            choice = "generic-transparent"
        elif len(required_ok) == len(REQUIRED_PAGES):
            choice = "generic-with-own-backing"
        elif required_ok:
            choice = "page-specific"
        else:
            choice = "text-fallback"
        rollup.append({
            "variant": variant,
            "choice": choice,
            "pages_ok_all_offsets": pages_ok,
            "pages_ok_integer_only": [p for p in PAGES
                                      if per_page[p]["pass_integer"] and not per_page[p]["pass_all_offsets"]],
            "per_page": per_page,
        })

    # Per required page: which variants survive, and the terminal's choice.
    # Among the variants that pass on every required page, prefer the one with
    # the best worst-case decisive contrast across pages and placements; the
    # badge carries its own opaque backing, so it is listed but never ranked
    # as a transparent generic asset.
    def worst_decisive(variant):
        vals = []
        for page in REQUIRED_PAGES:
            for offset in OFFSETS:
                c = next(c for c in cells if c["variant"] == variant
                         and c["page"] == page and c["offset"] == offset)
                for g in c["statuses"].values():
                    if g["decisive_contrast"] is not None:
                        vals.append(g["decisive_contrast"])
        return min(vals) if vals else 0.0

    pages_choice = {}
    for page in REQUIRED_PAGES:
        ok = [r["variant"] for r in rollup if page in r["pages_ok_all_offsets"]]
        transparent_ok = [v for v in ok
                          if next(r for r in rollup if r["variant"] == v)["choice"]
                          == "generic-transparent"]
        transparent_ok.sort(key=worst_decisive, reverse=True)
        pages_choice[page] = {
            "passing_variants": ok,
            "generic_variants": transparent_ok,
            "recommended": transparent_ok[0] if transparent_ok else None,
            "fallback": "text" if not ok else None,
        }
    # one asset serving every required page, if any
    common = [v for v in design.VARIANTS
              if all(v in [r["variant"] for r in rollup if p in r["pages_ok_all_offsets"]]
                     for p in REQUIRED_PAGES)
              and next(r for r in rollup if r["variant"] == v)["choice"] == "generic-transparent"]
    common.sort(key=worst_decisive, reverse=True)
    one_asset = {
        "variant": common[0] if common else None,
        "candidates": common,
        "worst_decisive_contrast": {v: round(worst_decisive(v), 2) for v in common},
        "rule": "one transparent asset for light/dark/checker at every tested placement",
    }

    policy = {
        "schema": "prism-icon-policy-v1",
        "per_page_choice": pages_choice,
        "one_asset": one_asset,
        "thresholds": {
            "wcag_non_text_contrast": T_CONTRAST,
            "gray_rms_separation": T_GRAY,
            "erasure_mean_delta": T_ERASE,
            "decisive_opaque_fraction": T_OPAQUE,
            "decisive_mean_alpha": T_MEAN_ALPHA,
        },
        "required_pages": REQUIRED_PAGES,
        "pages": PAGES,
        "offsets_tested": OFFSETS,
        "cells": cells,
        "variants": rollup,
    }
    POL.mkdir(parents=True, exist_ok=True)
    (POL / "decision.json").write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = ["| variant | chosen as | light | dark | checker | mid |",
             "|---|---|---|---|---|---|"]
    for r in rollup:
        row = ["pass" if r["per_page"][p]["pass_all_offsets"] else "FAIL" for p in PAGES]
        lines.append(f"| `{r['variant']}` | {r['choice']} | " + " | ".join(row) + " |")
    lines += ["", "| variant | residual failure reasons |", "|---|---|"]
    for r in rollup:
        reasons = sorted({x for p in r["per_page"].values() for x in p["reasons"]})
        lines.append(f"| `{r['variant']}` | {', '.join(reasons) or '—'} |")
    lines += ["", "| status | page | variant | mode | ink | decisive | erasure inside mean |",
              "|---|---|---|---|---|---|---|"]
    for c in cells:
        if c["offset"] != "dx0p0_dy0p0":
            continue
        for st, g in c["statuses"].items():
            if g["mode"] != "intact":
                lines.append(
                    f"| {st} | {c['page']} | `{c['variant']}` | {g['mode']} | "
                    f"{g['ink_contrast']} | {g['decisive_contrast']} | {g['erasure_inside_mean']} |")
    (REC / "policy-table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    lines += ["", f"one asset for all required pages: `{one_asset['variant']}` "
              f"(worst decisive contrast {one_asset['worst_decisive_contrast'].get(one_asset['variant'])})"]
    lines += ["", "| page | recommended | other variants that pass | fallback |", "|---|---|---|---|"]
    for page, ch in pages_choice.items():
        others = [v for v in ch["passing_variants"] if v != ch["recommended"]]
        lines.append(f"| {page} | `{ch['recommended']}` | {', '.join('`'+v+'`' for v in others) or '—'} | {ch['fallback'] or '—'} |")
    (REC / "policy-table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"cells={len(cells)}")
    for r in rollup:
        print(f"  {r['variant']:14s} {r['choice']:24s} pages={r['pages_ok_all_offsets']} int={r['pages_ok_integer_only']}")


if __name__ == "__main__":
    main()
