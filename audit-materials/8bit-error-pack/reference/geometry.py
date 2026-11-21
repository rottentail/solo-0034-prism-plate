"""Contour flattening, affine maps and point-in-polygon tests.

Re-derived from the document contract stated in the project README and
scene schema: quadratics subdivide into 16 fixed segments, cubics into 24.
The ray-crossing test mirrors the half-open edge convention used by the
production rasteriser so that independent results are directly comparable.
"""

import math
from dataclasses import dataclass

Point = tuple  # (x, y)


@dataclass(frozen=True)
class Matrix:
    """Affine transform [a b c d e f] mapping (x,y)->(ax+cy+e, bx+dy+f)."""

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    @classmethod
    def identity(cls) -> "Matrix":
        return cls(1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    def apply(self, p: Point) -> Point:
        x, y = p
        return (self.a * x + self.c * y + self.e,
                self.b * x + self.d * y + self.f)

    def inverse(self) -> "Matrix":
        det = self.a * self.d - self.b * self.c
        if abs(det) < 1e-10:
            raise ValueError("singular transform")
        return Matrix(
            self.d / det, -self.b / det, -self.c / det, self.a / det,
            (self.c * self.f - self.d * self.e) / det,
            (self.b * self.e - self.a * self.f) / det,
        )


def flatten(ops: list) -> list:
    """Flatten path verbs to closed polygonal rings."""
    rings: list = []
    ring: list = []
    at: Point = (0.0, 0.0)

    def flush():
        if len(ring) >= 3:
            rings.append(tuple(ring))
        ring.clear()

    for op in ops:
        kind = op["kind"]
        if kind == "M":
            flush()
            at = (float(op["points"][0]["x"]), float(op["points"][0]["y"]))
            ring.append(at)
        elif kind == "L":
            at = (float(op["points"][0]["x"]), float(op["points"][0]["y"]))
            ring.append(at)
        elif kind == "Q":
            start = at
            cx = float(op["points"][0]["x"])
            cy = float(op["points"][0]["y"])
            ex = float(op["points"][1]["x"])
            ey = float(op["points"][1]["y"])
            for i in range(1, 17):
                t = i / 16.0
                u = 1.0 - t
                ring.append((
                    u * u * start[0] + 2 * u * t * cx + t * t * ex,
                    u * u * start[1] + 2 * u * t * cy + t * t * ey,
                ))
            at = (ex, ey)
        elif kind == "C":
            start = at
            a1 = (float(op["points"][0]["x"]), float(op["points"][0]["y"]))
            a2 = (float(op["points"][1]["x"]), float(op["points"][1]["y"]))
            end = (float(op["points"][2]["x"]), float(op["points"][2]["y"]))
            for i in range(1, 25):
                t = i / 24.0
                u = 1.0 - t
                ring.append((
                    u ** 3 * start[0] + 3 * u * u * t * a1[0]
                    + 3 * u * t * t * a2[0] + t ** 3 * end[0],
                    u ** 3 * start[1] + 3 * u * u * t * a1[1]
                    + 3 * u * t * t * a2[1] + t ** 3 * end[1],
                ))
            at = end
        elif kind == "Z":
            flush()
        else:
            raise ValueError(f"unknown verb {kind}")
    flush()
    return rings


def ring_contains(ring: tuple, p: Point) -> tuple:
    """Return (crossing, winding_delta_sign) for one closed ring."""
    crossings = 0
    winding = 0
    px, py = p
    n = len(ring)
    for i in range(n):
        ax, ay = ring[i]
        bx, by = ring[(i + 1) % n]
        if (ay <= py < by) or (by <= py < ay):
            x_hit = ax + (py - ay) * (bx - ax) / (by - ay)
            if x_hit > px:
                crossings += 1
                winding += 1 if by > ay else -1
    return crossings, winding


def ring_bbox(ring: tuple) -> tuple:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return (min(xs), min(ys), max(xs), max(ys))


def path_contains(rings: list, p: Point, rule: str) -> bool:
    total_cross = 0
    total_winding = 0
    px, py = p
    for ring in rings:
        x0, y0, x1, y1 = ring_bbox(ring)
        if px < x0 or px > x1 or py < y0 or py > y1:
            continue
        c, w = ring_contains(ring, p)
        total_cross += c
        total_winding += w
    if rule == "evenodd":
        return total_cross % 2 == 1
    if rule == "nonzero":
        return total_winding != 0
    raise ValueError(f"unknown fill rule {rule}")
