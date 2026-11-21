"""Analytic coverage and closed-form error bounds.

The dense-grid renderer in :mod:`reference.raster` is an estimator whose own
discretisation is unknown.  For axis-aligned rectangles the exact coverage
of a pixel is a piecewise-linear function of the edge offsets, so it can be
computed in closed form.  This module provides that exact coverage plus the
closed-form bounds used in the audit report:

1. **Coverage bound (straight edges).**  The production 2x2 grid samples at
   offsets 1/4 and 3/4 inside the pixel.  For one vertical (or horizontal)
   edge, the grid estimate differs from the exact coverage by at most 1/4;
   two independent edges give at most 1/2, and with the partial-pixel
   geometry used here (at most one edge cuts a given pixel column) the
   attainable worst case for one edge is 1/4.  This module verifies the
   bound numerically by exhaustively sweeping edge offsets.

2. **8-bit encoding bound.**  Rounding to the 0..255 lattice commits at
   most half a code in sRGB straight space, |Δc| <= 1/510 ≈ 0.001961 per
   channel.  After decode to linear light the worst-case displacement is
   the largest sRGB step width over [0,1], which sits at the dark end
   (codes 0->1); the exact value is computed here rather than asserted.

3. **Flattened bound.**  For a pixel with coverage alpha ``a`` over an
   opaque backdrop, the post-compositing sRGB displacement is bounded by
   ``a`` times the straight-space encoding half-step, because source-over
   in straight display space is convex:
   ``out = a*src + (1-a)*backdrop``.
"""

from . import srgb


def rect_coverage_1d(lo: float, hi: float, pixel_origin: int) -> float:
    """Exact fraction [0,1] of the unit interval [k, k+1) inside [lo, hi]."""
    left = max(lo, float(pixel_origin))
    right = min(hi, float(pixel_origin + 1))
    return max(0.0, right - left)


def grid_coverage_1d(lo: float, hi: float, pixel_origin: int,
                     grid: int = 2) -> float:
    """Production-style midpoint grid estimate of the 1-D coverage."""
    hits = 0
    for i in range(grid):
        sample = pixel_origin + (i + 0.5) / grid
        if lo <= sample < hi:
            hits += 1
    return hits / grid


def sweep_edge_error(grid: int = 2) -> dict:
    """Exhaustively sweep one vertical edge; return observed and bound gap.

    The rectangle spans [0, edge) so exactly one edge cuts pixels near
    ``edge``.  Offsets are swept at fine resolution plus every grid
    midpoint and every half/midpoint tie location.
    """
    worst = 0.0
    worst_at = 0.0
    probes = [i / 1000.0 for i in range(1001)]
    probes += [(i + 0.5) / grid + k for k in range(3)
               for i in range(grid)]
    probes += [k + 0.25 for k in range(3)] + [k + 0.75 for k in range(3)]
    for edge in sorted(set(probes)):
        for k in range(3):
            exact = rect_coverage_1d(0.0, edge, k)
            est = grid_coverage_1d(0.0, edge, k, grid)
            gap = abs(exact - est)
            if gap > worst:
                worst, worst_at = gap, edge
    return {
        "grid": f"{grid}x{grid}",
        "observed_max_coverage_error": worst,
        "observed_at_edge_offset": worst_at,
        "analytic_bound_single_edge": 0.25,
        "bound_holds": worst <= 0.25 + 1e-12,
    }


def linear_step_widths() -> dict:
    """Largest linear-light jump between adjacent sRGB codes 0..255."""
    widths = []
    for code in range(256):
        widths.append(srgb.decode(code / 255.0))
    diffs = [widths[k + 1] - widths[k] for k in range(255)]
    return {
        "max_linear_step": max(diffs),
        "argmax_code_pair": (diffs.index(max(diffs)),
                             diffs.index(max(diffs)) + 1),
        "min_nonzero_linear_step": min(d for d in diffs if d > 0.0),
        "half_step_srgb_units": 1.0 / 510.0,
    }


def flatten_bound_demo() -> list:
    """Closed-form post-composite bound evaluated at representative alphas.

    For source straight value ``s`` quantised to ``s+e`` with |e| <= 1/510
    and coverage ``a`` over opaque backdrop ``b``:
        |out' - out| = a|e| <= a/510  (sRGB straight units).
    """
    rows = []
    for a in (1.0, 0.5, 0.10, 0.01, 0.002):
        rows.append({"alpha": a,
                     "straight_srgb_bound": a / 510.0})
    return rows
