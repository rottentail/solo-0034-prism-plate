#!/usr/bin/env python3
"""Independent reference rasterizers for the moire atlas.

Three deliberately separate renderings of the *same* geometric scenes:

  * render_contract : an independent re-implementation of the prismplate
    raster contract (2x2 point coverage, linear-light multiply, sRGB 8-bit).
    It shares no code with the service; agreement is evidence the stripe
    readings come from the documented contract, not from a shared bug.
  * render_oversampled : the same geometry sampled at 8x8 sub-pixel points,
    an approximation of the continuous (pre-sampling) image. This is the
    "what the design really is" reference.
  * render_folded : the analytic sampled model on the same 2x2 grid, where
    every grating is evaluated at its Nyquist-folded wave vector. Agreement
    with the service PNG isolates the sampling-artifact component.
"""
import math

SRGB_ALPHA, SRGB_BETA = 0.055, 1.055


def srgb_to_linear(v):
    v /= 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def linear_to_srgb(v):
    v = min(1.0, max(0.0, v))
    if v <= 0.0031308:
        u = v * 12.92
    else:
        u = 1.055 * v ** (1 / 2.4) - 0.055
    return int(round(u * 255))


def apply_affine(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def invert_affine(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    return [d / det, -b / det, -c / det, a / det,
            (c * f - d * e) / det, (b * e - a * f) / det]


def point_in_ring(x, y, ring):
    inside = False
    n = len(ring)
    for i in range(n):
        ax, ay = ring[i]
        bx, by = ring[(i + 1) % n]
        if (ay > y) != (by > y):
            xcross = ax + (y - ay) * (bx - ax) / (by - ay)
            if xcross > x:
                inside = not inside
    return inside


def grating_geometry(g, size):
    """Return (normal vector, period, shift along normal) in canvas space."""
    theta = math.radians(g["angle_deg"])
    n = (math.cos(theta), math.sin(theta))
    return n, g["period"], g["shift"]


def grating_black(n, period, shift, x, y, cx, cy, angle_deg, size):
    """Finite 50%-duty bar membership, matching the generated rectangle tiling.

    The scene builds finite rectangles in a rotated local frame. Replicating
    that finite geometry (rather than an infinite grating) is required for
    pixel-exact agreement near the ends of the oblique bars.
    """
    theta = math.radians(angle_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    # Inverse of the scene's rotation matrix R(theta) (local -> canvas):
    # lx =  cos_t*dx + sin_t*dy ; ly = -sin_t*dx + cos_t*dy.
    lx = cos_t * (x - cx) + sin_t * (y - cy)
    ly = -sin_t * (x - cx) + cos_t * (y - cy)
    half_span = size * 0.5 * (abs(cos_t) + abs(sin_t)) + 2.0
    half_len = half_span
    if ly < -half_len or ly > half_len:
        return False
    # Equivalent to testing membership in one of the finite tiled rectangles:
    # bars span [i*p + shift, i*p + shift + p/2).
    frac = ((lx - shift) / period) - math.floor((lx - shift) / period)
    return frac < 0.5


def _composite(gratings, blends, x, y, size):
    """Linear-light composite of black/white opaque layers at one point."""
    cx = cy = size / 2.0
    dst = (1.0, 1.0, 1.0)  # white background, linear light
    dst_a = 1.0
    for g, blend in zip(gratings, blends):
        n, period, shift = grating_geometry(g, size)
        black = grating_black(n, period, shift, x, y, cx, cy, g["angle_deg"], size)
        src = (0.0, 0.0, 0.0) if black else (1.0, 1.0, 1.0)
        src_a = 1.0
        if blend == "normal":
            dst, dst_a = src, src_a
        else:  # multiply, matches paint.Over for opaque layers
            dst = tuple(dst[c] * src[c] for c in range(3))
    return dst


def render_contract(case):
    """Exact prismplate 2x2 point-sampling contract, independently written."""
    size = case["size"]
    gratings = case["gratings"]
    blends = ["normal", "multiply"] if len(gratings) == 2 else ["normal"]
    img = [[0] * size for _ in range(size)]
    for y in range(size):
        for x in range(size):
            acc = [0.0, 0.0, 0.0]
            for sy in range(2):
                for sx in range(2):
                    px = x + (sx + 0.5) / 2.0
                    py = y + (sy + 0.5) / 2.0
                    col = _composite(gratings, blends, px, py, size)
                    for c in range(3):
                        acc[c] += col[c] / 4.0
            img[y][x] = tuple(linear_to_srgb(v) for v in acc)
    return img


def render_oversampled(case, sub=8):
    """Continuous-image approximation: 8x8 jitter-free dense coverage."""
    size = case["size"]
    gratings = case["gratings"]
    blends = ["normal", "multiply"] if len(gratings) == 2 else ["normal"]
    img = [[0] * size for _ in range(size)]
    for y in range(size):
        for x in range(size):
            acc = [0.0, 0.0, 0.0]
            for sy in range(sub):
                for sx in range(sub):
                    px = x + (sx + 0.5) / sub
                    py = y + (sy + 0.5) / sub
                    col = _composite(gratings, blends, px, py, size)
                    for c in range(3):
                        acc[c] += col[c] / (sub * sub)
            img[y][x] = tuple(linear_to_srgb(v) for v in acc)
    return img


def fold(f):
    return f - round(f)


def render_folded(case):
    """Analytic sampling model on the infinite lattice via folded vectors.

    A sampled square grating with continuous phase k.r - shift/p in cycles
    equals, at every lattice point, the square grating with folded vector
    kf = fold(k) and the same signed phase term. This renderer intentionally
    ignores the finite bar lengths: it isolates the *pure sampling* image, so
    any residual difference from the service PNG is attributable to geometry
    (finite bars / true low-frequency beats), not to the folding math.
    """
    size = case["size"]
    cx = cy = size / 2.0
    folded = []
    for g in case["gratings"]:
        theta = math.radians(g["angle_deg"])
        f = 1.0 / g["period"]
        kx = fold(f * math.cos(theta))
        ky = fold(f * math.sin(theta))
        # Real square wave: S(phi) = (frac(phi) < 0.5). Under carrier
        # conjugation (folded vector reversed), S(-phi) = 1 - S(phi), so the
        # folded wave uses magnitude |kf| along the folded unit direction and
        # negates the phase. Build direction, magnitude and signed phase term.
        kmag = math.hypot(kx, ky)
        u = (kx / kmag, ky / kmag) if kmag > 0 else (0.0, 0.0)
        reversed_ = (kx < 0.0) or (kx == 0.0 and ky < 0.0)
        folded.append((u, kmag, g["shift"] / g["period"], reversed_))
    blends = ["normal", "multiply"] if len(case["gratings"]) == 2 else ["normal"]
    img = [[0] * size for _ in range(size)]
    for y in range(size):
        for x in range(size):
            acc = [0.0, 0.0, 0.0]
            for sy in range(2):
                for sx in range(2):
                    dx = x + (sx + 0.5) / 2.0 - cx
                    dy = y + (sy + 0.5) / 2.0 - cy
                    dst = (1.0, 1.0, 1.0)
                    for idx, (u, kmag, shift_phase, reversed_) in enumerate(folded):
                        s = u[0] * dx + u[1] * dy
                        # Folded phase = |kf|*s + shift/p when reversed,
                        # |kf|*s - shift/p otherwise (conjugation flips both).
                        phi = kmag * s + (shift_phase if reversed_ else -shift_phase)
                        black = (phi - math.floor(phi)) < 0.5
                        src = (0.0, 0.0, 0.0) if black else (1.0, 1.0, 1.0)
                        if blends[idx] == "normal":
                            dst = src
                        else:
                            dst = tuple(dst[c] * src[c] for c in range(3))
                    for c in range(3):
                        acc[c] += dst[c] / 4.0
            img[y][x] = tuple(linear_to_srgb(v) for v in acc)
    return img
