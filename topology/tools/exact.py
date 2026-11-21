"""Exact rational geometry for the Prism Plate topology casebook.

Analysis-only.  It does not implement boolean operations or fill conversion;
it evaluates the fill set that the product's own path semantics define.

Coordinates are read as exact rationals (json parse_float=Fraction).  Bezier
area is integrated exactly with Green's theorem.  Flattening mirrors the
product contract (16 quadratic / 24 cubic steps) so derivations can be compared
with the emitted PNG/SVG.
"""
from __future__ import annotations

import json
import math
from fractions import Fraction as F
from typing import Sequence

Point = tuple[F, F]
Ring = list[Point]
Path = list[Ring]
Matrix = tuple[F, F, F, F, F, F]


# ---------------------------------------------------------------------------
# input / transforms


def load_document(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh, parse_float=F, parse_int=F)


def as_point(p) -> Point:
    if isinstance(p, dict):
        return (F(p["x"]), F(p["y"]))
    return (F(p[0]), F(p[1]))


def ops_of(src) -> list[dict]:
    out = []
    for o in src:
        pts = [as_point(p) for p in o.get("points", [])]
        out.append({"kind": o["kind"], "points": pts})
    return out


def matrix_of(m) -> Matrix:
    return tuple(F(v) for v in m)


def apply(m: Matrix, p: Point) -> Point:
    return (m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5])


def det(m: Matrix) -> F:
    return m[0] * m[3] - m[1] * m[2]


def transform_ops(ops: Sequence[dict], m: Matrix) -> list[dict]:
    return [{"kind": o["kind"], "points": [apply(m, p) for p in o["points"]]} for o in ops]


def transform_path(path: Path, m: Matrix) -> Path:
    return [[apply(m, p) for p in ring] for ring in path]


# ---------------------------------------------------------------------------
# flatten: identical sampling formula to internal/geom.Flatten


def flatten_ops(ops: Sequence[dict]) -> Path:
    rings: Path = []
    ring: Ring = []
    at: Point = (F(0), F(0))

    def flush():
        nonlocal ring
        if len(ring) >= 3:
            rings.append(ring)
        ring = []

    for o in ops:
        k = o["kind"]
        p = o["points"]
        if k == "M":
            flush()
            at = p[0]
            ring.append(at)
        elif k == "L":
            at = p[0]
            ring.append(at)
        elif k == "Q":
            start = at
            control, end = p[0], p[1]
            for i in range(1, 17):
                t = F(i, 16)
                u = 1 - t
                ring.append((u * u * start[0] + 2 * u * t * control[0] + t * t * end[0],
                             u * u * start[1] + 2 * u * t * control[1] + t * t * end[1]))
            at = end
        elif k == "C":
            start = at
            a, b, end = p[0], p[1], p[2]
            for i in range(1, 25):
                t = F(i, 24)
                u = 1 - t
                ring.append((u ** 3 * start[0] + 3 * u * u * t * a[0] + 3 * u * t * t * b[0] + t ** 3 * end[0],
                             u ** 3 * start[1] + 3 * u * u * t * a[1] + 3 * u * t * t * b[1] + t ** 3 * end[1]))
            at = end
        elif k == "Z":
            flush()
        else:
            raise ValueError("bad verb " + k)
    flush()
    return rings


def flatten_refined(ops: Sequence[dict], q_steps: int, c_steps: int) -> Path:
    rings: Path = []
    ring: Ring = []
    at: Point = (F(0), F(0))

    def flush():
        nonlocal ring
        if len(ring) >= 3:
            rings.append(ring)
        ring = []

    for o in ops:
        k = o["kind"]
        p = o["points"]
        if k == "M":
            flush(); at = p[0]; ring.append(at)
        elif k == "L":
            at = p[0]; ring.append(at)
        elif k == "Q":
            start = at; control, end = p[0], p[1]
            for i in range(1, q_steps + 1):
                t = F(i, q_steps); u = 1 - t
                ring.append((u * u * start[0] + 2 * u * t * control[0] + t * t * end[0],
                             u * u * start[1] + 2 * u * t * control[1] + t * t * end[1]))
            at = end
        elif k == "C":
            start = at; a, b, end = p[0], p[1], p[2]
            for i in range(1, c_steps + 1):
                t = F(i, c_steps); u = 1 - t
                ring.append((u ** 3 * start[0] + 3 * u * u * t * a[0] + 3 * u * t * t * b[0] + t ** 3 * end[0],
                             u ** 3 * start[1] + 3 * u * u * t * a[1] + 3 * u * t * t * b[1] + t ** 3 * end[1]))
            at = end
        elif k == "Z":
            flush()
    flush()
    return rings


# ---------------------------------------------------------------------------
# exact signed area (Green's theorem over polynomial segments)


def _mul(a: list[F], b: list[F]) -> list[F]:
    r = [F(0)] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            r[i + j] += x * y
    return r


def _curve_integral(xs: list[F], ys: list[F]) -> F:
    dx = [i * xs[i] for i in range(1, len(xs))]
    dy = [i * ys[i] for i in range(1, len(ys))]
    p = _mul(xs, dy)
    q = _mul(ys, dx)
    n = max(len(p), len(q))
    p += [F(0)] * (n - len(p))
    q += [F(0)] * (n - len(q))
    return sum((p[k] - q[k]) / (k + 1) for k in range(n)) / 2


def segment_area(seg, start: Point) -> F:
    if seg[0] == "L":
        x0, y0 = start
        x1, y1 = seg[1]
        return (x0 * y1 - y0 * x1) / 2
    if seg[0] == "Q":
        x0, y0 = start; x1, y1 = seg[1]; x2, y2 = seg[2]
        return _curve_integral([x0, 2 * (x1 - x0), x0 - 2 * x1 + x2],
                               [y0, 2 * (y1 - y0), y0 - 2 * y1 + y2])
    if seg[0] == "C":
        x0, y0 = start; x1, y1 = seg[1]; x2, y2 = seg[2]; x3, y3 = seg[3]
        return _curve_integral([x0, 3 * (x1 - x0), 3 * (x0 - 2 * x1 + x2), -x0 + 3 * x1 - 3 * x2 + x3],
                               [y0, 3 * (y1 - y0), 3 * (y0 - 2 * y1 + y2), -y0 + 3 * y1 - 3 * y2 + y3])
    raise ValueError(seg[0])


def exact_signed_area_ops(ops: Sequence[dict]) -> F:
    total = F(0)
    start = cur = None
    for o in ops:
        k = o["kind"]; pts = o["points"]
        if k == "M":
            start = cur = pts[0]
        elif k == "Z":
            total += segment_area(("L", start), cur)
            cur = start
        elif k == "L":
            total += segment_area(("L", pts[0]), cur); cur = pts[0]
        elif k == "Q":
            total += segment_area(("Q", pts[0], pts[1]), cur); cur = pts[1]
        elif k == "C":
            total += segment_area(("C", pts[0], pts[1], pts[2]), cur); cur = pts[2]
    return total


def signed_area_rings(path: Path) -> F:
    total = F(0)
    for ring in path:
        for i, a in enumerate(ring):
            b = ring[(i + 1) % len(ring)]
            total += a[0] * b[1] - a[1] * b[0]
    return total / 2


# ---------------------------------------------------------------------------
# exact point membership of the flattened path


def winding_number(path: Path, p: Point) -> int:
    px, py = p
    w = 0
    for ring in path:
        for i, a in enumerate(ring):
            b = ring[(i + 1) % len(ring)]
            if a[1] <= py:
                if b[1] > py and _cross(a, b, p) > 0:
                    w += 1
            else:
                if b[1] <= py and _cross(a, b, p) < 0:
                    w -= 1
    return w


def crossings(path: Path, p: Point) -> int:
    px, py = p
    n = 0
    for ring in path:
        for i, a in enumerate(ring):
            b = ring[(i + 1) % len(ring)]
            if (a[1] <= py < b[1]) or (b[1] <= py < a[1]):
                x = a[0] + (py - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                if x > px:
                    n += 1
    return n


def filled(path: Path, p: Point, rule: str) -> bool:
    return crossings(path, p) % 2 == 1 if rule == "evenodd" else winding_number(path, p) != 0


def _cross(a: Point, b: Point, p: Point) -> F:
    return (b[0] - a[0]) * (p[1] - a[1]) - (p[0] - a[0]) * (b[1] - a[1])


# ---------------------------------------------------------------------------
# curve approximation bounds


def _norm(v) -> float:
    return math.hypot(float(v[0]), float(v[1]))


def flatten_bound(ops: Sequence[dict], q_steps=16, c_steps=24) -> float:
    """True upper bound on distance between each curve and its uniform chords.

    C2 curve: chord error on a parameter interval of length 1/n is at most
    (1/8)(1/n)^2 max|B''|.  Quadratic: B'' = 2(P0 - 2P1 + P2).
    Cubic: B'' = 6((1-t)d0 + t d1), d0=P0-2P1+P2, d1=P1-2P2+P3, so
    max|B''| <= 6 max(|d0|, |d1|).  The bound is in manuscript units.
    """
    worst = 0.0
    at = (F(0), F(0))
    for o in ops:
        pts = o["points"]
        if o["kind"] == "M":
            at = pts[0]
        elif o["kind"] == "L":
            at = pts[0]
        elif o["kind"] == "Q":
            p0, p1, p2 = at, pts[0], pts[1]
            d = (p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1])
            worst = max(worst, (2 * _norm(d)) / (8 * q_steps ** 2))
            at = p2
        elif o["kind"] == "C":
            p0, p1, p2, p3 = at, pts[0], pts[1], pts[2]
            d0 = (p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1])
            d1 = (p1[0] - 2 * p2[0] + p3[0], p1[1] - 2 * p2[1] + p3[1])
            worst = max(worst, (6 * max(_norm(d0), _norm(d1))) / (8 * c_steps ** 2))
            at = p3
    return worst


def max_chord_deviation(ops: Sequence[dict], q_steps=16, c_steps=24, samples=65) -> float:
    """Observed maximum distance from curve point to its own chord."""
    worst = 0.0
    at = None
    for o in ops:
        pts = o["points"]
        k = o["kind"]
        if k == "M":
            at = pts[0]
        elif k == "L":
            at = pts[0]
        elif k in ("Q", "C"):
            n = q_steps if k == "Q" else c_steps
            if k == "Q":
                p0, p1, p2 = at, pts[0], pts[1]

                def ev(t):
                    u = 1 - t
                    return (float(u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]),
                            float(u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
            else:
                p0, p1, p2, p3 = at, pts[0], pts[1], pts[2]

                def ev(t):
                    u = 1 - t
                    return (float(u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0]),
                            float(u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
            for i in range(n):
                a = ev(i / n)
                b = ev((i + 1) / n)
                for si in range(samples):
                    t = (i + si / (samples - 1)) / n
                    x, y = ev(t)
                    worst = max(worst, _point_segment_distance(x, y, a, b))
            at = pts[1] if k == "Q" else pts[2]
    return worst


def _point_segment_distance(px, py, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    dd = dx * dx + dy * dy
    if dd == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / dd
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


# ---------------------------------------------------------------------------
# serialisation helpers


def frac(v: F) -> str:
    if v.denominator == 1:
        return str(v.numerator)
    return f"{v.numerator}/{v.denominator}"


def num(v: F) -> float:
    return float(v)


def jsonable(v):
    """Exact, hashable JSON form: fractions as strings, points as [x, y]."""
    if isinstance(v, F):
        return frac(v)
    if isinstance(v, tuple):
        return [jsonable(x) for x in v]
    if isinstance(v, list):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    return v
