#!/usr/bin/env python3
"""Draw region and uncertainty overlays for each case.

Outputs annotations/<case>.svg: the exact flattened boundary (black), filled
cells at 30% teal, holes at 12% rose, and every contact/margin marker.  They
are analysis diagrams, not product renders; the product proofs are out/<case>.svg
and out/<case>.png.
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
CASES = ROOT / "cases"
ANN = ROOT / "annotations"
sys.path.insert(0, str(HERE))

from exact import load_document, ops_of, matrix_of, flatten_ops
import exact as E
import topology as T


def num(v):
    if isinstance(v, str):
        from fractions import Fraction as F
        v = F(v)
    return f"{float(v):.4f}"


def ring_d(ring):
    out = []
    for i, p in enumerate(ring):
        out.append(("M" if i == 0 else "L") + num(p[0]) + " " + num(p[1]))
    out.append("Z")
    return " ".join(out)


def main():
    index = json.loads((CASES / "index.json").read_text())
    for entry in index["cases"]:
        case = load_document(str(CASES / entry["file"]))
        ann = json.loads((ANN / (entry["id"] + ".json")).read_text())
        w = case["document"]["width"]; h = case["document"]["height"]
        shapes = case["document"]["layers"][0]["shapes"]
        regions = []
        for sh in shapes:
            m = matrix_of(sh["transform"])
            inv = None
            d = E.det(m)
            inv = ((m[3] / d), (-m[1] / d), (-m[2] / d), (m[0] / d),
                   ((m[2] * m[5] - m[3] * m[4]) / d), ((m[1] * m[4] - m[0] * m[5]) / d))
            local = flatten_ops(ops_of(sh["path"]))
            regions.append({"canvas": E.transform_path(local, m), "local": local,
                            "inverse": inv, "rule": sh["fill_rule"]})
        topo = T.arrangement_regions(regions)

        out = []
        out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h + 26}" viewBox="0 0 {w} {h + 50}">')
        out.append(f'<rect width="{w}" height="{h + 50}" fill="#f7f4ec"/>')
        # filled cells and holes (open reading)
        for c in topo["cells_detail"]:
            if c["filled"] and c["poly"]:
                out.append(f'<path d="{ring_d(c["poly"])}" fill="#1c9da8" fill-opacity="0.32" stroke="none"/>')
        for c in topo.get("hole_cells", []):
            if c["poly"]:
                out.append(f'<path d="{ring_d(c["poly"])}" fill="#c2427a" fill-opacity="0.16" stroke="none"/>')
        # boundary
        for reg in regions:
            for ring in T.canonical(reg["canvas"]):
                out.append(f'<path d="{ring_d(ring)}" fill="none" stroke="#1c1c1c" stroke-width="0.7"/>')
        # markers
        for u in ann["uncertain"]:
            kind = u["kind"]
            if kind in ("transverse-crossing", "vertex-contact"):
                p = u["point"]
                color = "#d2382c" if kind == "transverse-crossing" else "#8a4fd8"
                out.append(f'<circle cx="{num(p[0])}" cy="{num(p[1])}" r="4.5" '
                           f'fill="none" stroke="{color}" stroke-width="1.4"/>')
            elif kind == "coincident-overlap":
                for seg in u["segments"]:
                    (x0, y0), (x1, y1) = seg
                    out.append(f'<line x1="{num(x0)}" y1="{num(y0)}" x2="{num(x1)}" y2="{num(y1)}" '
                               f'stroke="#d28a00" stroke-width="2.4" stroke-dasharray="3 2"/>')
            elif kind == "approximation-margin":
                if u.get("point"):
                    px, py = u["point"]
                    out.append(f'<circle cx="{num(px)}" cy="{num(py)}" r="7" '
                               f'fill="none" stroke="#d28a00" stroke-width="1.4"/>')
        # caption + legend strip: four short lines at 7 px
        base = float(h)
        out.append(
            f'<g font-family="DejaVu Sans" font-size="7.5" fill="#222">'
            f'<text x="6" y="{base+11}">{entry["id"]}</text>'
            f'<text x="6" y="{base+21}">directed {ann["areas"]["canvas_signed_exact"]}'
            f'  C {topo["components_open"]}/{topo["components_closure"]}'
            f'  H {topo["holes_open"]}/{topo["holes_closure"]}'
            f'  {ann["status"]}</text>'
            f'<circle cx="9" cy="{base+30}" r="3" fill="none" stroke="#d2382c"/>'
            f'<text x="16" y="{base+33}">crossing</text>'
            f'<circle cx="64" cy="{base+30}" r="3" fill="none" stroke="#8a4fd8"/>'
            f'<text x="71" y="{base+33}">vertex</text>'
            f'<line x1="106" y1="{base+30}" x2="117" y2="{base+30}" stroke="#d28a00" '
            f'stroke-width="2.2" stroke-dasharray="2.5 1.5"/>'
            f'<text x="121" y="{base+33}">coincident</text>'
            f'<circle cx="9" cy="{base+42}" r="4" fill="none" stroke="#d28a00"/>'
            f'<text x="16" y="{base+45}">clearance below 2b</text>'
            f'<line x1="94" y1="{base+42}" x2="104" y2="{base+42}" stroke="#2f7d32" '
            f'stroke-width="1.2" stroke-dasharray="1.6 1.6"/>'
            f'<text x="108" y="{base+45}">closest non-contact pair</text></g>')
        out.append('</svg>')
        (ANN / (entry["id"] + ".svg")).write_text("\n".join(out) + "\n", encoding="utf-8")
        print("wrote", entry["id"] + ".svg")


if __name__ == "__main__":
    main()
