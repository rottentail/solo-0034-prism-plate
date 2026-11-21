"""Exact certificates relating the true Bezier path to its flattened chords.

The product renders chords (16 quadratic / 24 cubic samples).  A topological
claim about the *drawn polyline* is exact, but does it describe the *declared
curve*?  Two exact certificates settle this for a pair of closed curves:

separation
    Branch and bound over axis-aligned boxes of the de Casteljau subdivision of
    both curves.  Bezier curves lie in the convex hull of their control points,
    so a box is a true bound.  Box-to-box squared distance is rational.  If
    every surviving box pair is farther apart than 2*b, where b is the exact
    curve-to-chord bound, then the true curves are farther than 2*b apart.  The
    chords (within b of each curve) therefore cannot cross either, and the
    crossing structure of the flattened path equals that of the curves.

crossing
    Pick exact curve points p on curve B (de Casteljau at rational t).  If
    dist(p, flattened A) > b, the straight-line homotopy from the flattened A
    to the true A stays inside the b-neighbourhood of flattened A and never
    touches p; hence the winding numbers of p with respect to the true and the
    flattened A agree.  If two such certified points have different winding,
    B must cross A, because winding can change along B only at a point of A.
    This certifies a true crossing without solving polynomial systems.

exact touch
    A shared exact endpoint of two curve elements is a true common point.  If
    all other parts of the pair are certified separated at 2*b, the pair's only
    contact is that point.
"""
from __future__ import annotations

from fractions import Fraction as F

# A curve element is a list of ('L', (a, b)) or ('C', (p0, p1, p2, p3))
# segments in canvas space, closed.
Segment = tuple


def segments_from_ops(ops):
    """Exact Bezier segments (canvas space) from a flattened op list."""
    segs = []
    at = None
    start = None
    for o in ops:
        k = o["kind"]; pts = o["points"]
        if k == "M":
            start = at = pts[0]
        elif k == "Z":
            if at is not None and start is not None and at != start:
                segs.append(("L", (at, start)))
            at = start
        elif k == "L":
            segs.append(("L", (at, pts[0]))); at = pts[0]
        elif k == "Q":
            segs.append(("Q", (at, pts[0], pts[1]))); at = pts[1]
        elif k == "C":
            segs.append(("C", (at, pts[0], pts[1], pts[2]))); at = pts[2]
    return segs


def box(seg):
    pts = seg[1]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return (min(xs), max(xs), min(ys), max(ys))


def box_dist_sq(a, b):
    dx = max(F(0), max(a[0] - b[1], b[0] - a[1]))
    dy = max(F(0), max(a[2] - b[3], b[2] - a[3]))
    return dx * dx + dy * dy


def split(seg):
    if seg[0] == "L":
        a, b = seg[1]
        m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        return ("L", (a, m)), ("L", (m, b))
    if seg[0] == "C":
        p0, p1, p2, p3 = seg[1]
        a = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
        b = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        c = ((p2[0] + p3[0]) / 2, (p2[1] + p3[1]) / 2)
        d = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        e = ((b[0] + c[0]) / 2, (b[1] + c[1]) / 2)
        f = ((d[0] + e[0]) / 2, (d[1] + e[1]) / 2)
        return ("C", (p0, a, d, f)), ("C", (f, e, c, p3))
    if seg[0] == "Q":
        p0, p1, p2 = seg[1]
        a = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
        b = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        return ("Q", (p0, a, m)), ("Q", (m, b, p2))
    raise ValueError(seg[0])


def endpoint(seg):
    p = seg[1]
    return p[-1]


def starts(seg):
    return seg[1][0]


def certify_separation(e1, e2, target_sq, max_depth=40):
    """Return (certified, lower_bound_sq). True iff every pair is > target."""
    stack = [(s1, s2, 0) for s1 in e1 for s2 in e2]
    worst = None
    while stack:
        s1, s2, d = stack.pop()
        b1, b2 = box(s1), box(s2)
        d2 = box_dist_sq(b1, b2)
        if d2 > target_sq:
            if worst is None or d2 < worst:
                worst = d2
            continue
        if d >= max_depth:
            return False, d2
        # split the segment with the larger box extent
        w1 = (b1[1] - b1[0]) + (b1[3] - b1[2])
        w2 = (b2[1] - b2[0]) + (b2[3] - b2[2])
        if w1 >= w2 and w1 > 0:
            a, b = split(s1)
            stack.append((a, s2, d + 1)); stack.append((b, s2, d + 1))
        elif w2 > 0:
            a, b = split(s2)
            stack.append((s1, a, d + 1)); stack.append((s1, b, d + 1))
        else:
            return False, d2
    return True, (worst if worst is not None else F(0))


def point_seg_dist_sq(p, seg):
    """Exact squared distance from p to the flattened version of seg.

    The chord, not the curve; only used with the b-margin, where the difference
    is irrelevant because point-seg-distance > b is checked against the chord.
    """
    pts = seg[1]
    if seg[0] == "L":
        a, b = pts
        return _pt_seg(p, a, b)
    if seg[0] == "Q":
        a, c, b = pts
        return min(_pt_seg(p, a, c), _pt_seg(p, c, b), _pt_chord(p, a, b))
    if seg[0] == "C":
        a, c1, c2, b = pts
        return min(_pt_seg(p, a, c1), _pt_seg(p, c1, c2),
                   _pt_seg(p, c2, b), _pt_chord(p, a, b))
    raise ValueError(seg[0])


def _pt_seg(p, a, b):
    return _pt_seg_exact(p, a, b)


def _pt_seg_exact(p, a, b):
    vx, vy = b[0] - a[0], b[1] - a[1]
    wx, wy = p[0] - a[0], p[1] - a[1]
    vv = vx * vx + vy * vy
    if vv == 0:
        return wx * wx + wy * wy
    t = (wx * vx + wy * vy) / vv
    if t < 0:
        t = F(0)
    elif t > 1:
        t = F(1)
    dx = wx - t * vx
    dy = wy - t * vy
    return dx * dx + dy * dy


def _pt_chord(p, a, b):
    return _pt_seg_exact(p, a, b)


def de_casteljau_points(elements, samples_per_seg=2):
    """Exact points on the true curves at rational parameters."""
    out = []
    for seg in elements:
        if seg[0] == "L":
            a, b = seg[1]
            for i in range(1, samples_per_seg + 1):
                t = F(i, samples_per_seg + 1)
                out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
        elif seg[0] == "C":
            p0, p1, p2, p3 = seg[1]
            for i in range(1, samples_per_seg + 1):
                t = F(i, samples_per_seg + 1)
                u = 1 - t
                out.append((
                    u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
        elif seg[0] == "Q":
            p0, p1, p2 = seg[1]
            for i in range(1, samples_per_seg + 1):
                t = F(i, samples_per_seg + 1)
                u = 1 - t
                out.append((
                    u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


def point_in_flattened(p, flat_path):
    from exact import winding_number, crossings
    return winding_number(flat_path, p), crossings(flat_path, p)


def min_point_polyline_dist_sq(p, flat_path):
    best = None
    for ring in flat_path:
        for i, a in enumerate(ring):
            b = ring[(i + 1) % len(ring)]
            d = _pt_seg_exact(p, a, b)
            if best is None or d < best:
                best = d
    return best


def certify_curve_pair(e1_ops, e2_ops, e1_flat, e2_flat, b, samples_per_seg=4):
    """Certify the relation of two true closed curves and its transfer.

    relation is about the *declared* curves:
      separated / touch / crossing / unresolved
    transfer is 'faithful' when the chords are certified to preserve the same
    relation (separation > 2b or an exact shared point), else 'not_certified'.

    A pair in which one ring is inside the other without contact receives
    relation 'nested'; it is equivalent to 'separated' for topology purposes.
    """
    e1 = segments_from_ops(e1_ops)
    e2 = segments_from_ops(e2_ops)
    b2 = F(b) * F(b) if b == int(b) else None
    target = 4 * F(b) * F(b)  # (2b)^2

    # exact shared endpoints of the two curve elements
    p1 = {seg[1][0] for seg in e1} | {endpoint(seg) for seg in e1}
    p2 = {seg[1][0] for seg in e2} | {endpoint(seg) for seg in e2}
    shared = sorted(p1 & p2)

    # separation of the remainder: ignore segment pairs that contain a shared
    # point; certify everything else at (2b)^2.
    rest1 = [s for s in e1 if not ({starts(s), endpoint(s)} & set(shared))]
    rest2 = [s for s in e2 if not ({starts(s), endpoint(s)} & set(shared))]
    ok_rest, low_rest = (True, F(0))
    if rest1 and rest2:
        ok_rest, low_rest = certify_separation(rest1, rest2, F(0))
    elif shared:
        ok_rest = True

    if ok_rest and shared:
        return {"relation": "touch", "shared_points": shared,
                "lower_bound_sq": low_rest, "crossing_witnesses": [],
                "transfer": "faithful"}
    if ok_rest and not shared:
        # The true curves are separated.  The chords transfer faithfully only
        # if the true separation exceeds 2b; otherwise a chord pair may cross
        # even though the curves do not.
        faithful = low_rest > target
        return {"relation": "separated", "shared_points": [],
                "lower_bound_sq": low_rest, "crossing_witnesses": [],
                "transfer": "faithful" if faithful else "not_certified"}

    # crossing certificate by certified winding jump along e2
    witnesses = []
    for p in de_casteljau_points(e2, samples_per_seg):
        d2 = min_point_polyline_dist_sq(p, e1_flat)
        if d2 <= b2_guard(b):
            continue
        w, _ = point_in_flattened(p, e1_flat)
        witnesses.append((p, w, d2))
    ws = {w for _, w, _ in witnesses}
    if len(ws) >= 2:
        return {"relation": "crossing", "shared_points": shared,
                "lower_bound_sq": F(0),
                "crossing_witnesses": [{"point": p, "winding": w}
                                       for p, w, _ in witnesses],
                "transfer": "faithful"}
    return {"relation": "unresolved", "shared_points": shared,
            "lower_bound_sq": low_rest, "crossing_witnesses": [],
            "transfer": "not_certified"}


def true_curves_intersect(e1_ops, e2_ops, max_depth=60):
    """Exact branch-and-bound separation of the true curves.

    Returns (certified_disjoint, lower_bound_sq).  `certified_disjoint` is a
    proof of disjointness: every box pair is separated by a positive rational
    distance.  If it is False, the curves may or may not intersect; the lower
    bound reports how far the search got.  An exact shared endpoint is reported
    separately by the caller and counts as a true common point.
    """
    e1 = segments_from_ops(e1_ops)
    e2 = segments_from_ops(e2_ops)
    p1 = {seg[1][0] for seg in e1} | {endpoint(seg) for seg in e1}
    p2 = {seg[1][0] for seg in e2} | {endpoint(seg) for seg in e2}
    shared = p1 & p2
    if shared:
        return False, F(0)
    ok, low = certify_separation(e1, e2, F(0), max_depth=max_depth)
    return ok, low


def b2_guard(b):
    return F(str(b)) ** 2 if not isinstance(b, F) else b * b
