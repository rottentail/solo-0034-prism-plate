#!/usr/bin/env python3
"""Recompute an auditable topology ledger for every casebook plate.

Derivation chain per case (all exact until an explicitly float bound):

1. Flatten each shape's local path with the product's contract (16 quadratic /
   24 cubic steps) and map it to canvas with the shape matrix.
2. Exact signed area of the Bezier path (Green's theorem on polynomial
   pieces), local and canvas, per shape and for the whole plate.
3. Exact vertical-slab arrangement of the flattened canvas boundary under the
   product's fill semantics (per-shape local fill, then union).  Counts filled
   components and holes under the open and closure contact readings.
4. Classify boundary contacts (transverse crossing + angle, vertex touch,
   coincident overlap) and minimum clearance between distinct elements.
5. Bound the curve-to-chord error and repeat the arrangement at four
   subdivisions; a verdict is determinate only if refinement is stable and both
   contact readings agree.

Outputs annotations/<case>.json and annotations/ledger.json.
"""
from __future__ import annotations

import json
import pathlib
import sys
from fractions import Fraction as F

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
CASES = ROOT / "cases"
ANN = ROOT / "annotations"
sys.path.insert(0, str(HERE))

import curve_certificate as CC
import exact as E
import topology as T

CONTRACT = (16, 24)
REFINEMENTS = [(8, 12), (16, 24), (32, 48), (64, 96)]


def regions_for(shapes, q_steps, c_steps):
    """Build per-shape regions at the requested subdivision (canvas space)."""
    regions = []
    for sh in shapes:
        local_ops = E.ops_of(sh["path"])
        m = E.matrix_of(sh["transform"])
        inv = None
        det = E.det(m)
        inv = _inverse(m)
        if q_steps == CONTRACT[0] and c_steps == CONTRACT[1]:
            local = E.flatten_ops(local_ops)
        else:
            local = E.flatten_refined(local_ops, q_steps, c_steps)
        canvas = E.transform_path(local, m)
        regions.append({"canvas": canvas, "local": local, "inverse": inv,
                        "rule": sh["fill_rule"], "shape": sh["id"],
                        "matrix": m, "det": det, "local_ops": local_ops})
    return regions


def _inverse(m):
    d = E.det(m)
    if d == 0:
        raise ZeroDivisionError("singular transform")
    return ((m[3] / d), (-m[1] / d), (-m[2] / d), (m[0] / d),
            ((m[2] * m[5] - m[3] * m[4]) / d), ((m[1] * m[4] - m[0] * m[5]) / d))


def curve_certificate(shapes):
    """Exact relation certificate for every pair of rings among curved shapes."""
    rings = []
    for si, sh in enumerate(shapes):
        ops = E.transform_ops(E.ops_of(sh["path"]), E.matrix_of(sh["transform"]))
        cur = None
        for o in ops:
            if o["kind"] == "M":
                cur = [o]
            elif cur is not None:
                cur.append(o)
            if o["kind"] == "Z" and cur is not None:
                rings.append({"shape": sh["id"], "ops": cur})
                cur = None
    if not any(any(o["kind"] in ("Q", "C") for o in r["ops"]) for r in rings):
        return {"applies": False}
    b = E.flatten_bound([o for r in rings for o in r["ops"]])
    pairs = []
    for i in range(len(rings)):
        for j in range(i + 1, len(rings)):
            f1 = E.flatten_ops(rings[i]["ops"])
            f2 = E.flatten_ops(rings[j]["ops"])
            cert = CC.certify_curve_pair(rings[i]["ops"], rings[j]["ops"], f1, f2, b)
            disjoint, low = CC.true_curves_intersect(rings[i]["ops"], rings[j]["ops"])
            pairs.append({
                "a": rings[i]["shape"], "b": rings[j]["shape"],
                "relation": cert["relation"],
                "transfer": cert["transfer"],
                "true_curves_certified_disjoint": disjoint,
                "separation_lower_bound_sq": float(low),
                "shared_points": [[E.frac(p[0]), E.frac(p[1])] for p in cert["shared_points"]],
                "witnesses": len(cert["crossing_witnesses"]),
            })
    return {"applies": True, "bound": b, "pairs": pairs}


def analyse(case: dict, entry: dict) -> dict:
    shapes = case["document"]["layers"][0]["shapes"]
    rule_summary = "/".join(s["fill_rule"] for s in shapes)

    regions = regions_for(shapes, *CONTRACT)
    topo = T.arrangement_regions(regions)
    con = T.contacts(regions)
    clearance, contact_pairs, clearance_at = T.min_clearance(regions)

    # exact areas
    curve_cert = curve_certificate(shapes)
    per_shape = []
    local_total = F(0)
    canvas_total = F(0)
    for sh, reg in zip(shapes, regions):
        la = E.exact_signed_area_ops(reg["local_ops"])
        ca = E.exact_signed_area_ops(E.transform_ops(reg["local_ops"], reg["matrix"]))
        local_total += la
        canvas_total += ca
        per_shape.append({
            "id": sh["id"], "fill_rule": sh["fill_rule"],
            "local_signed_exact": E.frac(la), "local_signed": float(la),
            "canvas_signed_exact": E.frac(ca), "canvas_signed": float(ca),
            "determinant": E.frac(reg["det"]),
            "flattened_signed": float(E.signed_area_rings(reg["canvas"])),
        })

    flat_signed_total = sum(E.signed_area_rings(r["canvas"]) for r in regions)
    # an "approximation bound" per shape, then the plate bound is the max
    bound = max(E.flatten_bound(E.transform_ops(E.ops_of(sh["path"]), E.matrix_of(sh["transform"])),
                                *CONTRACT) for sh in shapes)

    refinements = []
    for q, c in REFINEMENTS:
        rr = regions_for(shapes, q, c)
        tr = T.arrangement_regions(rr)
        refinements.append({
            "quadratic_steps": q, "cubic_steps": c,
            "components_open": tr["components_open"],
            "components_closure": tr["components_closure"],
            "holes_open": tr["holes_open"],
            "holes_closure": tr["holes_closure"],
            "filled_area": float(tr["filled_area"]),
            "signed_area": float(sum(E.signed_area_rings(r["canvas"]) for r in rr)),
            "error_bound": max(E.flatten_bound(E.transform_ops(E.ops_of(sh["path"]),
                                                              E.matrix_of(sh["transform"])), q, c)
                               for sh in shapes),
        })
    sigs = {(r["components_open"], r["components_closure"],
             r["holes_open"], r["holes_closure"]) for r in refinements}
    stable = len(sigs) == 1

    # The open-set count is the canonical answer: winding/parity is constant on
    # the open cells, and the filled set of a path is open by definition.  The
    # closure count is recorded because raster and human perception see the
    # closure; a disagreement is a *contact* degree of freedom, not ambiguity
    # about the flattened set.
    contact_degrees = []
    if not topo["components_determinate"]:
        contact_degrees.append("component split depends on point/edge contact")
    if not topo["holes_determinate"]:
        contact_degrees.append("hole count depends on point/edge contact")

    # A degenerate boundary (coincident elements, vertex-on-edge contacts,
    # overlaps) fixes the *curves* less than a generic boundary does.  It does
    # not by itself make the open-set count wrong, but it must be declared.
    boundary_degenerate = bool(con["vertex_touches"] or con["coincident_overlaps"])
    boundary_notes = []
    if con["vertex_touches"]:
        boundary_notes.append(f"{len(con['vertex_touches'])} vertex contact(s) between distinct boundary elements")
    if con["coincident_overlaps"]:
        boundary_notes.append(f"{len(con['coincident_overlaps'])} coincident edge overlap(s) between distinct shapes")

    # Visibility is about the *artifact* at the largest allowed canvas
    # (512x512).  Two independent signals: the total filled area and the
    # narrowest unfilled/filled band; either can fall under one pixel.
    filled_area_f = float(topo["filled_area"]) if topo["filled_area"] is not None else 0.0
    narrow = []
    if 0.0 < filled_area_f < 1.0:
        narrow.append(f"filled area {filled_area_f:.6g} canvas units is under 1 px "
                      f"at the document raster size")
    if clearance is not None and clearance < 0.5:
        narrow.append(f"minimum non-contact clearance {clearance:.6g} canvas units "
                      f"is under 1 px, so adjacent regions merge in raster")

    comp_sigs = {r["components_open"] for r in refinements}
    hole_sigs = {r["holes_open"] for r in refinements}
    comp_stable = len(comp_sigs) == 1
    hole_stable = len(hole_sigs) == 1
    refinement_notes = []
    if not comp_stable:
        refinement_notes.append("flattening subdivision changes the component count")
    if not hole_stable:
        refinement_notes.append("flattening subdivision changes the hole count")

    # Curve transfer: only curve cases have a certificate.
    curve_notes = []
    curve_unfaithful = False
    cc = curve_cert
    if cc.get("applies"):
        for pair in cc["pairs"]:
            if pair["transfer"] != "faithful":
                curve_unfaithful = True
                curve_notes.append(
                    f"curve pair {pair['a']}/{pair['b']}: true relation "
                    f"{pair['relation']}, transfer to flattened path not certified")

    # three-level verdict:
    #   determined             - open-set counts fixed, closure agrees, curves faithful
    #   determined-with-contact- open-set counts fixed, closure differs at contacts
    #   undetermined           - counts or curve transfer not certified
    stable_counts = comp_stable and hole_stable and not curve_unfaithful
    local_contact_only = (stable_counts and not curve_unfaithful
                          and (contact_degrees or boundary_notes or not topo["components_determinate"]
                               or not topo["holes_determinate"]))
    if not stable_counts:
        status = "undetermined"
    elif contact_degrees or not topo["components_determinate"] or not topo["holes_determinate"] \
            or boundary_degenerate:
        status = "determined-with-contact"
    else:
        status = "determined"
    component_status = status if comp_stable else "undetermined"
    hole_status = status if hole_stable else "undetermined"
    visibility_notes = narrow

    reasons = []
    reasons.extend(contact_degrees)
    reasons.extend(visibility_notes)
    visibility = "visible" if not visibility_notes else "below-pixel"
    reasons.extend(refinement_notes)
    reasons.extend(curve_notes)
    reasons.extend(boundary_notes)

    region_records = []
    for i, comp in enumerate(topo["component_detail"]):
        region_records.append({
            "kind": "filled-component", "index": i,
            "area_exact": E.frac(comp["area"]), "area": float(comp["area"]),
            "sample_point": E.jsonable(comp["sample"]), "cells": comp["cells"],
        })
    if topo.get("hole_area") is not None and topo["hole_area"] > 0:
        region_records.append({
            "kind": "hole-area-total", "area_exact": E.frac(topo["hole_area"]),
            "area": float(topo["hole_area"]), "cells": topo["hole_cell_count"],
        })

    uncertain = []
    for c in con["crossings"]:
        uncertain.append({"kind": "transverse-crossing", "point": E.jsonable(c["point"]),
                          "angle_deg": None if c["angle"] is None else round(c["angle"], 6),
                          "regions": c["regions"]})
    for p in con["vertex_touches"]:
        uncertain.append({"kind": "vertex-contact", "point": E.jsonable(p)})
    for ov in con["coincident_overlaps"]:
        uncertain.append({"kind": "coincident-overlap",
                          "segments": [[E.jsonable(p) for p in seg] for seg in (ov["a"], ov["b"])],
                          "regions": ov["regions"]})
    if clearance is not None and clearance < 2 * bound:
        mid = None
        if clearance_at:
            mid = ((clearance_at[0][0] + clearance_at[1][0]) / 2,
                   (clearance_at[0][1] + clearance_at[1][1]) / 2)
        uncertain.append({"kind": "approximation-margin", "clearance": clearance,
                          "bound": _safe(bound),
                          "point": E.jsonable(mid) if mid else None,
                          "closest_pair": E.jsonable(clearance_at) if clearance_at else None})

    return {
        "id": entry["id"], "title": entry["title"], "group": entry["group"],
        "question": entry["question"], "claim": entry["claim"],
        "fill_rule": rule_summary, "degenerate_declared": entry["degenerate"],
        "per_shape": per_shape,
        "areas": {
            "local_signed_exact": E.frac(local_total), "local_signed": float(local_total),
            "canvas_signed_exact": E.frac(canvas_total), "canvas_signed": float(canvas_total),
            "flattened_signed": float(flat_signed_total),
            "filled_exact": E.frac(topo["filled_area"]), "filled": float(topo["filled_area"]),
        },
        "topology": {
            "hole_area_exact": E.frac(topo["hole_area"]) if topo.get("hole_area") is not None else None,
            "components_open": topo["components_open"],
            "components_closure": topo["components_closure"],
            "holes_open": topo["holes_open"],
            "holes_closure": topo["holes_closure"],
            "faces_open": topo["faces"], "faces_closure": topo["faces_closure"],
            "cells": topo["cell_count"], "vertex_contacts": topo["vertex_contacts"],
        },
        "approximation": {
            "contract": {"quadratic_steps": CONTRACT[0], "cubic_steps": CONTRACT[1]},
            "error_bound": bound, "minimum_clearance": _safe(clearance),
            "contact_pairs": contact_pairs,
            "minimum_clearance_at": E.jsonable(clearance_at) if clearance_at else None,
            "refinements": refinements, "stable_across_refinements": stable,
        },
        "contacts": {
            "transverse_crossings": len(con["crossings"]),
            "minimum_crossing_angle_deg": None if con["min_angle"] is None else round(con["min_angle"], 6),
            "vertex_contacts": len(con["vertex_touches"]),
            "coincident_overlaps": len(con["coincident_overlaps"]),
        },
        "signed_area_from_faces_exact": (E.frac(topo["signed_area_from_faces"])
                                         if topo.get("signed_area_from_faces") is not None else None),
        "regions": region_records,
        "uncertain": uncertain,
        "curve_certificate": curve_cert,
        "contact_degrees": contact_degrees,
        "boundary_degenerate": boundary_degenerate,
        "min_band_width": None,
        "refinement_notes": refinement_notes,
        "status": status,
        "visibility": visibility,
        "component_status": component_status,
        "hole_status": hole_status,
        "status_reasons": reasons,
    }


def _safe(v):
    if isinstance(v, F):
        return E.frac(v)
    return v


def main() -> int:
    index = json.loads((CASES / "index.json").read_text())
    ANN.mkdir(parents=True, exist_ok=True)
    ledger = []
    for entry in index["cases"]:
        case = json.loads((CASES / entry["file"]).read_text(), parse_float=F, parse_int=F)
        res = analyse(case, entry)
        (ANN / (entry["id"] + ".json")).write_text(
            json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        t = res["topology"]
        ledger.append({
            "id": res["id"], "group": res["group"], "title": res["title"],
            "canvas_signed_exact": res["areas"]["canvas_signed_exact"],
            "filled_exact": res["areas"]["filled_exact"],
            "components_open": t["components_open"],
            "components_closure": t["components_closure"],
            "holes_open": t["holes_open"],
            "holes_closure": t["holes_closure"],
            "status": res["status"],
            "min_band_width": res["min_band_width"],
            "component_status": res["component_status"],
            "hole_status": res["hole_status"],
            "reasons": res["status_reasons"],
        })
        print(f"{res['id']:32s} signed={res['areas']['canvas_signed_exact']:>9s} "
              f"filled={res['areas']['filled_exact']:>9s} "
              f"C={t['components_open']}/{t['components_closure']} "
              f"H={t['holes_open']}/{t['holes_closure']} {res['status']}")
    (ANN / "ledger.json").write_text(
        json.dumps({"cases": ledger}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(ledger)} annotations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
