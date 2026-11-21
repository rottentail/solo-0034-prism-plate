"""Colour-difference metrics for the error study.

Metrics are split by the question they answer:

* code-level metrics (``max_code_delta`` ...) — measure the 8-bit container,
  and are the only honest metric *before* compositing;
* linear-light RMSE / sRGB RMSE — physical and display-encoded magnitudes;
* :func:`delta_e00` — CIEDE2000 perceptual distance, valid **only for opaque
  colours**.  It must never be fed a straight RGB pair from low-alpha
  pixels; the audit pipeline first flattens both candidates over the same
  opaque backdrop and only then computes delta E.

XYZ/Lab use the standard sRGB D65 illuminant matrix.  CIEDE2000 follows
Sharma et al. (2005), including the hue rotation term.
"""

import math

from . import srgb

_D65 = (0.95047, 1.0, 1.08883)


def srgb_to_xyz(r: float, g: float, b: float) -> tuple:
    lr, lg, lb = srgb.decode(r), srgb.decode(g), srgb.decode(b)
    return (
        0.4124564 * lr + 0.3575761 * lg + 0.1804375 * lb,
        0.2126729 * lr + 0.7151522 * lg + 0.0721750 * lb,
        0.0193339 * lr + 0.1191920 * lg + 0.9503041 * lb,
    )


def xyz_to_lab(x: float, y: float, z: float) -> tuple:
    def f(t: float) -> float:
        eps = 216 / 24389
        kap = 24389 / 27
        return t ** (1 / 3) if t > eps else (kap * t + 16) / 116

    fx, fy, fz = f(x / _D65[0]), f(y / _D65[1]), f(z / _D65[2])
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def srgb_to_lab(r: float, g: float, b: float) -> tuple:
    return xyz_to_lab(*srgb_to_xyz(r, g, b))


def _deg2rad(d: float) -> float:
    return d * math.pi / 180.0


def _rad2deg(r: float) -> float:
    return r * 180.0 / math.pi


def delta_e00(c1: tuple, c2: tuple, kl: float = 1.0,
              kc: float = 1.0, kh: float = 1.0) -> float:
    """CIEDE2000 between two sRGB straight colours (opaque only)."""
    l1, a1, b1 = srgb_to_lab(*c1)
    l2, a2, b2 = srgb_to_lab(*c2)

    c1_ab = math.hypot(a1, b1)
    c2_ab = math.hypot(a2, b2)
    c_bar = (c1_ab + c2_ab) / 2.0
    g = 0.5 * (1.0 - math.sqrt(c_bar ** 7 / (c_bar ** 7 + 25.0 ** 7)))
    a1p = (1.0 + g) * a1
    a2p = (1.0 + g) * a2
    c1p = math.hypot(a1p, b1)
    c2p = math.hypot(a2p, b2)

    def hue(ap: float, bp: float) -> float:
        if ap == 0.0 and bp == 0.0:
            return 0.0
        h = _rad2deg(math.atan2(bp, ap))
        return h + 360.0 if h < 0.0 else h

    h1p, h2p = hue(a1p, b1), hue(a2p, b2)

    d_lp = l2 - l1
    d_cp = c2p - c1p
    if c1p * c2p == 0.0:
        d_hp = 0.0
    elif abs(h2p - h1p) <= 180.0:
        d_hp = h2p - h1p
    elif h2p - h1p > 180.0:
        d_hp = h2p - h1p - 360.0
    else:
        d_hp = h2p - h1p + 360.0
    d_hp = 2.0 * math.sqrt(c1p * c2p) * math.sin(_deg2rad(d_hp / 2.0))

    l_bar_p = (l1 + l2) / 2.0
    c_bar_p = (c1p + c2p) / 2.0
    if c1p * c2p == 0.0:
        h_bar_p = h1p + h2p
    elif abs(h1p - h2p) <= 180.0:
        h_bar_p = (h1p + h2p) / 2.0
    elif h1p + h2p < 360.0:
        h_bar_p = (h1p + h2p + 360.0) / 2.0
    else:
        h_bar_p = (h1p + h2p - 360.0) / 2.0

    t = (1.0
         - 0.17 * math.cos(_deg2rad(h_bar_p - 30.0))
         + 0.24 * math.cos(_deg2rad(2.0 * h_bar_p))
         + 0.32 * math.cos(_deg2rad(3.0 * h_bar_p + 6.0))
         - 0.20 * math.cos(_deg2rad(4.0 * h_bar_p - 63.0)))
    d_theta = 30.0 * math.exp(-(((h_bar_p - 275.0) / 25.0) ** 2))
    r_c = 2.0 * math.sqrt(c_bar_p ** 7 / (c_bar_p ** 7 + 25.0 ** 7))
    s_l = 1.0 + (0.015 * (l_bar_p - 50.0) ** 2) \
        / math.sqrt(20.0 + (l_bar_p - 50.0) ** 2)
    s_c = 1.0 + 0.045 * c_bar_p
    s_h = 1.0 + 0.015 * c_bar_p * t
    r_t = -math.sin(_deg2rad(2.0 * d_theta)) * r_c

    return math.sqrt(
        (d_lp / (kl * s_l)) ** 2
        + (d_cp / (kc * s_c)) ** 2
        + (d_hp / (kh * s_h)) ** 2
        + r_t * (d_cp / (kc * s_c)) * (d_hp / (kh * s_h))
    )


def code_stats(rows_a: list, rows_b: list) -> dict:
    """8-bit code-space statistics over matching RGBA grids."""
    max_rgb = max_a = 0
    sum_rgb_sq = 0.0
    n_rgb = 0
    diff_pixels = 0
    for ra, rb in zip(rows_a, rows_b):
        for pa, pb in zip(ra, rb):
            drgb = max(abs(pa[k] - pb[k]) for k in range(3))
            da = abs(pa[3] - pb[3])
            max_rgb = max(max_rgb, drgb)
            max_a = max(max_a, da)
            for k in range(3):
                sum_rgb_sq += (pa[k] - pb[k]) ** 2
                n_rgb += 1
            if pa != pb:
                diff_pixels += 1
    return {
        "max_rgb_code_delta": max_rgb,
        "max_alpha_code_delta": max_a,
        "rgb_rmse_codes": math.sqrt(sum_rgb_sq / n_rgb),
        "pixels_different": diff_pixels,
        "pixels_total": sum(len(r) for r in rows_a),
    }


def composited_delta_stats(px_a: list, px_b: list, backdrop: tuple,
                           metric: str = "de00") -> dict:
    """Flatten two premul pixel grids over one backdrop, then measure.

    ``px_a``/``px_b`` are :class:`composite.Premul` grids (float truth or
    reconstructed from 8-bit codes alike).  Backdrop is an opaque sRGB
    straight triple.
    """
    from .composite import Premul, Straight, flatten_over_backdrop

    bd = Straight(*backdrop, 1.0)
    vals = []
    for ra, rb in zip(px_a, px_b):
        for a, b in zip(ra, rb):
            ca = flatten_over_backdrop(a, bd)
            cb = flatten_over_backdrop(b, bd)
            if metric == "de00":
                vals.append(delta_e00((ca.r, ca.g, ca.b),
                                      (cb.r, cb.g, cb.b)))
            elif metric == "srgb_euclid":
                vals.append(math.sqrt((ca.r - cb.r) ** 2
                                      + (ca.g - cb.g) ** 2
                                      + (ca.b - cb.b) ** 2))
            else:
                raise ValueError(metric)
    return {
        f"{metric}_max": max(vals),
        f"{metric}_mean": sum(vals) / len(vals),
        f"{metric}_p99": sorted(vals)[int(0.99 * (len(vals) - 1))],
    }
