"""Exact planar topology of the flattened fill set ("inked" region).

Model of the product that this module evaluates
------------------------------------------------
`internal/raster` flattens each shape path with 16 quadratic / 24 cubic steps
and, for every sample point p, maps p into the shape's local coordinates with
the inverse transform, then tests `Path.Contains(q, fill_rule)`.  Shapes are
painted over one another; with opaque brushes the inked set is the union of the
per-shape filled sets.  Layer clip and mask are canvas-space nonzero paths.

So the inked set is exactly: union over shapes of
    image under the shape matrix of { q : fill(local_path, q, rule) }.
This module reconstructs that set from the flattened polygons.  It performs no
boolean operation and no fill conversion: it only evaluates the declared rules.

Geometry
--------
Open vertical slabs at every vertex x-coordinate.  In a slab, edges are linear
and vertically ordered; open cells are the bands between consecutive edges.
Two virtual exterior cells (far left, far right) make the unbounded complement
explicit.

Adjacency
---------
trace   consecutive bands of a slab sharing the full slab-width edge trace
pos     neighbouring slabs whose traces overlap in positive length at the
        shared line, with no vertical path edge covering the overlap
edge    the positive overlap is exactly a vertical path edge
vertex  traces meet only at a shared endpoint

Conventions
-----------
open     connectivity through `trace` and `pos` only.  This is the standard
         open-set reading: fills do not connect through a point or a bare
         boundary edge.
closure  connectivity also through `edge` and `vertex`.

Holes are bounded unfilled components that share positive-length boundary with
the inked component.  A verdict is determinate only when both readings agree.
"""
from __future__ import annotations

from fractions import Fraction as F

from exact import Path, Point, apply, crossings, winding_number

NEG = float("-inf")
POS = float("inf")


class _DSU:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, a: int) -> int:
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int) -> None:
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[b] = a


def canonical(path: Path) -> Path:
    out = []
    for ring in path:
        r = list(ring)
        while len(r) > 1 and r[-1] == r[0]:
            r.pop()
        out.append(r)
    return out


def _segments(regions):
    """(region index, ring index, ring length, vertex index, a, b) for all edges."""
    out = []
    for ri, reg in enumerate(regions):
        rings = canonical(reg["canvas"])
        for ki, ring in enumerate(rings):
            n = len(ring)
            for i, a in enumerate(ring):
                b = ring[(i + 1) % n]
                if a != b:
                    out.append((ri, ki, n, i, a, b))
    return out


def _split(a, b, c, d):
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    den = r[0] * s[1] - r[1] * s[0]
    qp = (c[0] - a[0], c[1] - a[1])
    if den != 0:
        t = (qp[0] * s[1] - qp[1] * s[0]) / den
        u = (qp[0] * r[1] - qp[1] * r[0]) / den
        return [(t, u)] if 0 <= t <= 1 and 0 <= u <= 1 else []
    if qp[0] * r[1] - qp[1] * r[0] != 0:
        return []
    rr = r[0] * r[0] + r[1] * r[1]
    ss = s[0] * s[0] + s[1] * s[1]
    if rr == 0 or ss == 0:
        return []
    tc = ((c[0] - a[0]) * r[0] + (c[1] - a[1]) * r[1]) / rr
    td = ((d[0] - a[0]) * r[0] + (d[1] - a[1]) * r[1]) / rr
    lo, hi = max(F(0), min(tc, td)), min(F(1), max(tc, td))
    if lo > hi:
        return []
    out = []
    for t in sorted({lo, hi}):
        p = (a[0] + t * r[0], a[1] + t * r[1])
        u = ((p[0] - c[0]) * s[0] + (p[1] - c[1]) * s[1]) / ss
        out.append((t, u))
    return out


def _subtract(pieces, a, b):
    nxt = []
    for p0, p1 in pieces:
        if b <= p0 or a >= p1:
            nxt.append((p0, p1))
            continue
        if p0 < a:
            nxt.append((p0, a))
        if b < p1:
            nxt.append((b, p1))
    return nxt


def _traces(edges, x0, x1):
    mid = (x0 + x1) / 2
    out = []
    for ei, (u, v) in enumerate(edges):
        if u[0] == v[0]:
            continue
        lo, hi = (u, v) if u[0] < v[0] else (v, u)
        if lo[0] < mid < hi[0]:
            slope = (hi[1] - lo[1]) / (hi[0] - lo[0])

            def y_at(x, lo=lo, slope=slope):
                return lo[1] + slope * (x - lo[0])

            out.append((y_at(mid), ei, y_at))
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def region_fill(reg: dict, p: Point, rule: str) -> bool:
    q = apply(reg["inverse"], p) if reg.get("inverse") is not None else p
    if rule == "evenodd":
        return crossings(reg["local"], q) % 2 == 1
    return winding_number(reg["local"], q) != 0


def arrangement_regions(regions) -> dict:
    """regions: [{canvas: Path, local: Path, inverse: Matrix|None, rule: str}]"""
    segs = _segments(regions)
    n = len(segs)
    splits = [[F(0), F(1)] for _ in range(n)]
    for i in range(n):
        a, b = segs[i][4], segs[i][5]
        for j in range(i + 1, n):
            for t, u in _split(a, b, segs[j][4], segs[j][5]):
                if 0 < t < 1:
                    splits[i].append(t)
                if 0 < u < 1:
                    splits[j].append(u)

    hits: dict[tuple[Point, Point], set] = {}
    for i, (_, _, _, _, a, b) in enumerate(segs):
        ts = sorted(set(splits[i]))
        pts = [(a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])) for t in ts]
        for u, v in zip(pts, pts[1:]):
            if u != v:
                key = (u, v) if u < v else (v, u)
                hits.setdefault(key, set()).add(i)
    edges = sorted(hits)
    xs = sorted({p[0] for e in edges for p in e})
    empty = {"components": 0, "holes": 0, "components_open": 0, "components_closure": 0,
             "holes_open": 0, "holes_closure": 0, "components_determinate": True,
             "holes_determinate": True, "filled_area": F(0), "faces": 0,
             "faces_closure": 0, "cell_count": 0, "component_detail": [],
             "vertex_contacts": 0, "degenerate": True, "edges": []}
    if len(xs) < 2:
        return empty

    vertical_at: dict[F, list] = {}
    for u, v in edges:
        if u[0] == v[0]:
            vertical_at.setdefault(u[0], []).append((min(u[1], v[1]), max(u[1], v[1])))

    cells = []
    slabs = []
    for si in range(len(xs) - 1):
        x0, x1 = xs[si], xs[si + 1]
        traces = _traces(edges, x0, x1)
        ids = []
        for k in range(len(traces) + 1):
            lo = traces[k - 1] if k > 0 else None
            hi = traces[k] if k < len(traces) else None
            if lo is None and hi is None:
                continue
            unbounded = lo is None or hi is None
            mid = (x0 + x1) / 2
            if unbounded:
                sy = (hi[2](mid) + 1) if lo is None else (lo[2](mid) - 1)
            else:
                sy = (lo[2](mid) + hi[2](mid)) / 2
            area = None
            if not unbounded:
                area = (x1 - x0) * ((hi[2](x0) - lo[2](x0)) + (hi[2](x1) - lo[2](x1))) / 2
            cid = len(cells)
            poly = None
            if not unbounded:
                poly = [(x0, lo[2](x0)), (x1, lo[2](x1)), (x1, hi[2](x1)), (x0, hi[2](x0))]
            cells.append({
                "slab": si, "poly": poly,
                "lo0": lo[2](x0) if lo else NEG, "lo1": lo[2](x1) if lo else NEG,
                "hi0": hi[2](x0) if hi else POS, "hi1": hi[2](x1) if hi else POS,
                "mid": (mid, sy), "area": area, "unbounded": unbounded,
            })
            ids.append(cid)
        slabs.append(ids)

    left = len(cells)
    cells.append({"slab": -1, "lo0": NEG, "lo1": NEG, "hi0": POS, "hi1": POS,
                  "mid": (xs[0] - 1, 0), "area": None, "unbounded": True, "exterior": True})
    right = len(cells)
    cells.append({"slab": len(slabs), "lo0": NEG, "lo1": NEG, "hi0": POS, "hi1": POS,
                  "mid": (xs[-1] + 1, 0), "area": None, "unbounded": True, "exterior": True})
    bands = [[left]] + slabs + [[right]]

    pairs = []
    for ids in bands:
        for a, b in zip(ids, ids[1:]):
            pairs.append((a, b, "trace"))
    for si in range(len(bands) - 1):
        x = xs[0] if si == 0 else (xs[-1] if si == len(bands) - 2 else xs[si])
        verts = sorted(vertical_at.get(x, []))
        for li in bands[si]:
            L = cells[li]
            for ri in bands[si + 1]:
                R = cells[ri]
                lo, hi = max(L["lo1"], R["lo0"]), min(L["hi1"], R["hi0"])
                if lo < hi:
                    pieces = [(lo, hi)]
                    for a, b in verts:
                        pieces = _subtract(pieces, a, b)
                    kind = "pos" if any(p0 < p1 for p0, p1 in pieces) else "edge"
                    pairs.append((li, ri, kind))
                elif lo == hi and lo not in (NEG, POS):
                    pairs.append((li, ri, "vertex"))

    for c in cells:
        filled = False
        for reg in regions:
            if region_fill(reg, c["mid"], reg["rule"]):
                filled = True
                break
        c["filled"] = filled and not c["unbounded"]
        c["exterior"] = c.get("exterior", False)
        if not c["unbounded"] and len(regions) == 1:
            c["winding"] = winding_number(regions[0]["local"], c["mid"]) if regions[0].get("inverse") is None else None

    def counts(use_boundary: bool):
        dsu = _DSU(len(cells))
        unb = [i for i, c in enumerate(cells) if c["exterior"]]
        for i in unb[1:]:
            dsu.union(unb[0], i)
        for a, b, kind in pairs:
            if kind in ("edge", "vertex") and not use_boundary:
                continue
            if cells[a]["filled"] != cells[b]["filled"]:
                continue
            dsu.union(a, b)
        filled_groups: dict[int, list] = {}
        unfilled_groups: dict[int, list] = {}
        for i, c in enumerate(cells):
            g = dsu.find(i)
            (filled_groups if c["filled"] else unfilled_groups).setdefault(g, []).append(i)
        boundary_unfilled = set()
        for a, b, kind in pairs:
            if kind == "vertex":
                continue
            if cells[a]["filled"] != cells[b]["filled"]:
                outsider = a if not cells[a]["filled"] else b
                boundary_unfilled.add(dsu.find(outsider))
        exterior_groups = {dsu.find(i) for i in unb}
        holes = sum(1 for g in unfilled_groups
                    if g in boundary_unfilled and g not in exterior_groups)
        comps = []
        for g, ids in filled_groups.items():
            comps.append({"area": sum(cells[i]["area"] for i in ids),
                          "sample": cells[max(ids, key=lambda i: cells[i]["area"])]["mid"],
                          "cells": len(ids)})
        comps.sort(key=lambda c: c["area"], reverse=True)
        return len(comps), holes, comps, len(filled_groups) + len(unfilled_groups)

    c_open, h_open, comps, faces = counts(False)
    c_closed, h_closed, _, faces_closed = counts(True)

    # hole polygons under the open reading, for annotation only
    dsu2 = _DSU(len(cells))
    ex = [i for i, c in enumerate(cells) if c["exterior"]]
    for i in ex[1:]:
        dsu2.union(ex[0], i)
    for a, b, kind in pairs:
        if kind != "trace" and kind != "pos":
            continue
        if cells[a]["filled"] == cells[b]["filled"]:
            dsu2.union(a, b)
    filled_groups: dict[int, list] = {}
    unfilled_groups: dict[int, list] = {}
    for i, c in enumerate(cells):
        g = dsu2.find(i)
        (filled_groups if c["filled"] else unfilled_groups).setdefault(g, []).append(i)
    boundary_unfilled = set()
    for a, b, kind in pairs:
        if kind == "vertex":
            continue
        if cells[a]["filled"] != cells[b]["filled"]:
            outsider = a if not cells[a]["filled"] else b
            boundary_unfilled.add(dsu2.find(outsider))
    exterior_groups = {dsu2.find(i) for i in ex}
    hole_cells = []
    for g, ids in unfilled_groups.items():
        if g in boundary_unfilled and g not in exterior_groups:
            for i in ids:
                if cells[i].get("poly") is not None:
                    hole_cells.append({"poly": cells[i]["poly"], "area": cells[i]["area"]})
    signed_check = None
    if len(regions) == 1 and regions[0].get("inverse") is None:
        signed_check = F(0)
        for c in cells:
            if c.get("winding") is not None and c.get("area") is not None:
                signed_check += c["winding"] * c["area"]
    return {
        "signed_area_from_faces": signed_check,
        "hole_area": sum((c["area"] for c in hole_cells if c["area"] is not None), F(0)),
        "hole_cell_count": len(hole_cells),
        "components": c_open, "holes": h_open,
        "components_open": c_open, "components_closure": c_closed,
        "holes_open": h_open, "holes_closure": h_closed,
        "components_determinate": c_open == c_closed,
        "holes_determinate": h_open == h_closed,
        "filled_area": sum(c["area"] for c in comps),
        "faces": faces, "faces_closure": faces_closed,
        "cell_count": len(cells), "component_detail": comps,
        "vertex_contacts": sum(1 for _, _, k in pairs if k == "vertex"),
        "degenerate": False,
        "edges": edges,
        "cells_detail": [{"poly": c.get("poly"), "filled": c["filled"],
                          "sample": c["mid"], "area": c.get("area")} for c in cells
                         if c.get("poly") is not None],
        "hole_cells": hole_cells,
    }


def arrangement(path: Path, rule: str) -> dict:
    return arrangement_regions([{"canvas": path, "local": path, "inverse": None, "rule": rule}])


# ---------------------------------------------------------------------------
# contacts and clearance (canvas space, all regions)


def contacts(regions) -> dict:
    segs = _segments(regions)
    crossings, vertex_touch, overlap = [], [], []
    same_region = []  # endpoint/overlap contacts inside one flattened ring
    for i in range(len(segs)):
        ri, ki, ni, ii, a, b = segs[i]
        for j in range(i + 1, len(segs)):
            rj, kj, nj, ij, c, d = segs[j]
            adjacent = (ri == rj and ki == kj and
                        ((ii + 1) % ni == ij or (ij + 1) % nj == ii))
            if adjacent:
                continue
            # same ring, same undirected segment re-walked in reverse: a
            # duplicated edge inside one self-overlapping ring
            if ri == rj and ki == kj and a == d and b == c:
                overlap.append({"a": [a, b], "b": [c, d], "regions": [ri, rj],
                                "kind": "self-duplicate"})
                continue
            if _collinear_overlap(a, b, c, d):
                entry = {"a": [a, b], "b": [c, d], "regions": [ri, rj]}
                # distinct rings (even of one compound shape) are distinct
                # boundary elements; only a single ring overlapping itself is
                # an internal self-contact.
                keep = (ri != rj) or (ki != kj)
                (overlap if keep else same_region).append(entry)
                continue
            params = _split(a, b, c, d)
            crossed = False
            touched = []
            for t, u in params:
                if 0 < t < 1 and 0 < u < 1:
                    angle = _angle(a, b, c, d)
                    crossings.append({"point": (a[0] + t * (b[0] - a[0]),
                                                a[1] + t * (b[1] - a[1])),
                                      "angle": angle, "regions": [ri, rj]})
                    crossed = True
                elif 0 <= t <= 1 and 0 <= u <= 1:
                    touched.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
            if not crossed:
                keep = (ri != rj) or (ki != kj)
                for p in touched:
                    if keep:
                        vertex_touch.append(p)
                    # a shared endpoint inside one ring is the ring closing on
                    # itself (self-touch) and stays in the internal bucket
                    elif p not in (a, b, c, d):
                        same_region.append(p)
    vertex_touch = _uniq(vertex_touch)
    return {"crossings": crossings, "vertex_touches": vertex_touch,
            "coincident_overlaps": overlap,
            "internal_self_contacts": _uniq([v for v in same_region if v is not None]),
            "min_angle": min((c["angle"] for c in crossings if c["angle"] is not None),
                             default=None)}


def _sector_order(rays):
    import math
    return sorted(rays, key=lambda r: math.atan2(float(r[1]), float(r[0])))


def classify_vertex(rays_a, rays_b):
    """Classify two boundary arcs meeting at one point.

    rays_a / rays_b are the outgoing direction vectors of each arc.  If the
    four rays alternate in angular order the arcs cross transversely (stable).
    Otherwise they touch (tangent or one-sided), which is unstable.
    """
    import math
    tagged = [(math.atan2(float(r[1]), float(r[0])), "a") for r in rays_a] + \
             [(math.atan2(float(r[1]), float(r[0])), "b") for r in rays_b]
    tagged.sort()
    labels = [t for _, t in tagged]
    # alternating means no two adjacent labels are equal in the cyclic order
    alt = all(labels[i] != labels[(i + 1) % len(labels)] for i in range(len(labels)))
    # angular separation between the two arcs' closest rays
    def ang(r):
        return math.atan2(float(r[1]), float(r[0]))
    best = None
    for ra in rays_a:
        for rb in rays_b:
            d = abs(ang(ra) - ang(rb))
            d = min(d, 2 * math.pi - d)
            if best is None or d < best:
                best = d
    return {"transverse": alt, "min_gap_deg": math.degrees(best) if best is not None else None}


def _uniq(items):
    out = []
    for it in items:
        if it not in out:
            out.append(it)
    return out


def _angle(a, b, c, d):
    import math
    r = (float(b[0] - a[0]), float(b[1] - a[1]))
    s = (float(d[0] - c[0]), float(d[1] - c[1]))
    nr, ns = math.hypot(*r), math.hypot(*s)
    if nr == 0 or ns == 0:
        return None
    v = max(-1.0, min(1.0, (r[0] * s[0] + r[1] * s[1]) / (nr * ns)))
    ang = math.degrees(math.acos(v))
    return min(ang, 180 - ang)


def _collinear_overlap(a, b, c, d):
    r = (b[0] - a[0], b[1] - a[1])
    if r[0] * (c[1] - a[1]) - r[1] * (c[0] - a[0]) != 0:
        return False
    if r[0] * (d[1] - a[1]) - r[1] * (d[0] - a[0]) != 0:
        return False
    rr = r[0] * r[0] + r[1] * r[1]
    if rr == 0:
        return False
    tc = ((c[0] - a[0]) * r[0] + (c[1] - a[1]) * r[1]) / rr
    td = ((d[0] - a[0]) * r[0] + (d[1] - a[1]) * r[1]) / rr
    lo, hi = max(F(0), min(tc, td)), min(F(1), max(tc, td))
    return lo < hi


def min_clearance(regions):
    """Smallest distance between boundary elements that do not meet.

    Pairs that properly cross, touch at an endpoint, or overlap are contacts,
    not clearances, and are excluded.  Returns (min_gap, contact_pairs) where
    contact_pairs counts intersecting/touching/overlapping pairs among
    non-adjacent elements.
    """
    segs = _segments(regions)
    segs.sort(key=lambda e: min(e[4][0], e[5][0]))
    best = None
    best_at = None
    contacts = 0
    for i in range(len(segs)):
        ri, ki, ni, ii, a, b = segs[i]
        for j in range(i + 1, len(segs)):
            rj, kj, nj, ij, c, d = segs[j]
            if min(c[0], d[0]) > max(a[0], b[0]):
                break
            if ri == rj and ki == kj and ((ii + 1) % ni == ij or (ij + 1) % nj == ii):
                continue
            if _collinear_overlap(a, b, c, d):
                contacts += 1
                continue
            if _split(a, b, c, d):
                contacts += 1
                continue
            af, bf = (float(a[0]), float(a[1])), (float(b[0]), float(b[1]))
            cf, df = (float(c[0]), float(c[1])), (float(d[0]), float(d[1]))
            val = min(_distance(*af, cf, df), _distance(*bf, cf, df),
                      _distance(*cf, af, bf), _distance(*df, af, bf))
            if best is None or val < best:
                best = val
                best_at = (_closest_pair(af, bf, cf, df))
    return best, contacts, best_at


def _closest_pair(a, b, c, d):
    import math
    cands = []
    def proj(p, q, r):
        qx, qy = q; px, py = p; rx, ry = r
        dx, dy = rx - qx, ry - qy
        dd = dx * dx + dy * dy
        if dd == 0:
            return (qx, qy)
        t = max(0.0, min(1.0, ((px - qx) * dx + (py - qy) * dy) / dd))
        return (qx + t * dx, qy + t * dy)
    for p, q, r in ((a, c, d), (b, c, d)):
        qq = proj(p, q, r)
        cands.append((math.hypot(p[0]-qq[0], p[1]-qq[1]), p, qq))
    for p, q, r in ((c, a, b), (d, a, b)):
        qq = proj(p, q, r)
        cands.append((math.hypot(p[0]-qq[0], p[1]-qq[1]), p, qq))
    cands.sort(key=lambda t: t[0])
    return (cands[0][1], cands[0][2])


def _distance(px, py, a, b):
    import math
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    dd = dx * dx + dy * dy
    if dd == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / dd))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))
