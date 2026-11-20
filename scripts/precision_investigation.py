#!/usr/bin/env python3
"""Recomputable precision comparison for the 1e6-scale quadrilateral case.

Separates four geometries that screenshots alone cannot distinguish:

  A. author intent   : exact decimal arithmetic (Decimal) of the submitted
                       contour vertices under the submitted transform.
  B. service float64 : IEEE-754 binary64 forward evaluation, the same order
                       of operations as Go's Matrix.Apply / raster pipeline.
  C. SVG file text   : the exact numbers the exporter writes, after its
                       "%.9g" serialization of both path coords and matrix.
  D. viewer float64  : binary64 evaluation of the numbers parsed from the
                       serialized SVG text (what an SVG consumer actually draws).

Also reports the inverse-transform sampling used by the canonical PNG
renderer, which never performs the forward large-number subtraction.

Run:  python3 -I scripts/precision_investigation.py
Only the Python standard library is used.
"""

import json
import math
from decimal import Decimal, getcontext

getcontext().prec = 60

# --- Exact submitted values (copied from the reproduction document) ---------
LOCAL = [
    (Decimal("0.000008000002708286047"), Decimal("0")),
    (Decimal("319999.99988"), Decimal("-319999.999872")),
    (Decimal("0.000040000013541430235"), Decimal("0")),
    (Decimal("-319999.999832"), Decimal("319999.999872")),
]
TRANSFORM = [
    Decimal("999999.9996"),
    Decimal("999999.9996"),
    Decimal("999999.9995"),
    Decimal("999999.9996"),
    Decimal("0"),
    Decimal("0"),
]

# The exporter's exact serialization contract (internal/vector number()).
def svgnum(x: float) -> str:
    return f"{x:.9g}"


def apply_decimal(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def apply_f64(m, x, y):
    # Explicit stepwise f64 arithmetic matching Go:
    # m0*x + m2*y + m4, each operation rounded to binary64 in sequence.
    xx = math.fma(m[0], x, math.fma(m[2], y, m[4])) if hasattr(math, "fma") else (
        m[0] * x + m[2] * y + m[4])
    yy = math.fma(m[1], x, math.fma(m[3], y, m[5])) if hasattr(math, "fma") else (
        m[1] * x + m[3] * y + m[5])
    return (xx, yy)


def polygon_area(pts):
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - y1 * x2
    return abs(s) / 2


def polygon_area_decimal(pts):
    s = Decimal(0)
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - y1 * x2
    return abs(s) / 2


def main():
    report = {"layers": {}, "points": [], "summary": {}}

    # A. author intent in exact decimal
    intent = [apply_decimal(TRANSFORM, x, y) for x, y in LOCAL]
    report["layers"]["A_author_intent_decimal"] = {
        "points": [(str(x), str(y)) for x, y in intent],
        "area": str(polygon_area_decimal(intent)),
    }

    # B. service-side binary64 forward evaluation of the *submitted* numbers
    m64 = [float(v) for v in TRANSFORM]
    local64 = [(float(x), float(y)) for x, y in LOCAL]
    svc = [apply_f64(m64, x, y) for x, y in local64]
    report["layers"]["B_service_f64_full_precision_inputs"] = {
        "points": [(repr(x), repr(y)) for x, y in svc],
        "area": polygon_area(svc),
    }

    # C. the numbers actually written into the SVG file
    path_tokens = [(svgnum(x), svgnum(y)) for x, y in local64]
    matrix_tokens = [svgnum(v) for v in m64]
    report["layers"]["C_svg_file_tokens"] = {
        "path_vertices": path_tokens,
        "transform_matrix": matrix_tokens,
    }

    # D. viewer reparses the tokens and evaluates in binary64
    mv = [float(t) for t in matrix_tokens]
    lv = [(float(a), float(b)) for a, b in path_tokens]
    viewer = [apply_f64(mv, x, y) for x, y in lv]
    report["layers"]["D_viewer_f64_from_svg_tokens"] = {
        "points": [(repr(x), repr(y)) for x, y in viewer],
        "area": polygon_area(viewer),
    }

    # per-point bookkeeping across all layers
    for i, (lx, ly) in enumerate(LOCAL):
        row = {
            "index": i,
            "local_input_decimal": (str(lx), str(ly)),
            "A_intent": (str(intent[i][0]), str(intent[i][1])),
            "B_service_f64": (repr(svc[i][0]), repr(svc[i][1])),
            "C_svg_tokens": path_tokens[i],
            "D_viewer_f64": (repr(viewer[i][0]), repr(viewer[i][1])),
        }
        report["points"].append(row)

    # PNG pipeline: inverse matrix and a couple of sample points
    d = m64[0] * m64[3] - m64[1] * m64[2]
    inv = [
        m64[3] / d, -m64[1] / d, -m64[2] / d, m64[0] / d,
        (m64[2] * m64[5] - m64[3] * m64[4]) / d,
        (m64[1] * m64[4] - m64[0] * m64[5]) / d,
    ]
    report["png_inverse_pipeline"] = {
        "determinant_f64": repr(d),
        "inverse_matrix_%.9g": [svgnum(v) for v in inv],
        "sample_pixel_centers_inverse_mapped": {},
    }
    # sample a few pixel-center points of the 48x48 canvas back into local space
    for px, py in [(10, 10), (24, 24), (40, 40), (8, 8)]:
        qx = inv[0] * px + inv[2] * py + inv[4]
        qy = inv[1] * px + inv[3] * py + inv[5]
        report["png_inverse_pipeline"]["sample_pixel_centers_inverse_mapped"][
            f"({px},{py})"] = (repr(qx), repr(qy))

    # error magnification: each %.9g token error, multiplied by the 1e6 scale
    worst_matrix_err = max(abs(mv[k] - m64[k]) for k in range(4))
    worst_coord_err = max(abs(lv[k][0] - local64[k][0]) for k in range(4))
    report["summary"] = {
        "area_intent_decimal": str(polygon_area_decimal(intent)),
        "area_service_f64": polygon_area(svc),
        "area_viewer_from_svg": polygon_area(viewer),
        "max_abs_matrix_token_error": repr(worst_matrix_err),
        "max_abs_path_token_error": repr(worst_coord_err),
        "matrix_token_error_scaled_to_canvas": repr(worst_matrix_err * 3.2e5),
        "path_coord_token_error_scaled_to_canvas": repr(worst_coord_err * 1e6),
        "service_minus_intent_per_point": [
            (repr(svc[i][0] - float(intent[i][0])),
             repr(svc[i][1] - float(intent[i][1])))
            for i in range(4)
        ],
        "viewer_minus_service_per_point": [
            (repr(viewer[i][0] - svc[i][0]), repr(viewer[i][1] - svc[i][1]))
            for i in range(4)
        ],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
