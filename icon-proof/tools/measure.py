"""Readability measurements over real prism-plate renders.

Every pixel input comes from the service (straight-alpha sRGB PNG). The
terminal composites such an image over its own backdrop with source-over in
sRGB code space; that model is applied here and stated in the report so every
number can be re-derived.

Vocabulary used by the criteria
-------------------------------
page backdrop    the terminal surface: light list row, dark proof, checker, mid.
local backdrop   what is actually behind one drawn part: the page plus every
                 part drawn below it. Obtained by rendering the prefix of the
                 plate (all lower parts, none at or above) — a real render,
                 not a guess.
slot             one drawn part (a status stroke, the ring, the hole liner,
                 the backing plate).
mark             a composited pixel that differs from the page by more than
                 VISIBLE_DELTA code values on some channel.
contrast         WCAG 2.x ratio between a composited pixel and its local
                 backdrop. 3.0 is the WCAG 1.4.11 floor for non-text graphics.
decisive         the stroke/region that carries the status distinction.
erasure          a comparison between the full icon and a real render of the
                 same plate with the decisive part removed (pending: its hole
                 filled). If a page erases exactly the decisive part, the full
                 and the reduced render coincide inside it while staying
                 different elsewhere.
"""
from __future__ import annotations

import math

VISIBLE_DELTA = 8.0
NON_TEXT_CONTRAST = 3.0
GRAY_SEPARATION = 18.0
CRISP_OPAQUE = 0.5


# ---------------------------------------------------------------------------
# page backdrops

def bg_solid(hexstr):
    h = hexstr.lstrip("#")
    c = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return lambda x, y: c


def bg_checker(a="#E6E8EC", b="#FFFFFF", period=8):
    ca = tuple(int(a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    cb = tuple(int(b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return lambda x, y: ca if ((x // period) + (y // period)) % 2 == 0 else cb


BACKDROPS = {
    "light": bg_solid("#FFFFFF"),
    "dark": bg_solid("#14161C"),
    "checker": bg_checker(),
    "mid": bg_solid("#6E7580"),
}
BACKDROP_HEX = {"light": "#FFFFFF", "dark": "#14161C",
                "checker": "#E6E8EC / #FFFFFF", "mid": "#6E7580"}


# ---------------------------------------------------------------------------
# colour primitives

def _lin(v):
    v /= 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def lum(rgb):
    return 0.2126 * _lin(rgb[0]) + 0.7152 * _lin(rgb[1]) + 0.0722 * _lin(rgb[2])


def contrast(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def gray(rgb):
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def over_pixel(rgba, i, bg):
    r, g, b, a = rgba[i * 4], rgba[i * 4 + 1], rgba[i * 4 + 2], rgba[i * 4 + 3]
    af = a / 255.0
    return (r * af + bg[0] * (1 - af), g * af + bg[1] * (1 - af), b * af + bg[2] * (1 - af))


def composite(w, h, rgba, bgfn):
    return [over_pixel(rgba, i, bgfn(i % w, i // w)) for i in range(w * h)]


def delta(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))


def _pct(values, q):
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] if lo == hi else s[lo] * (hi - k) + s[hi] * (k - lo)


# ---------------------------------------------------------------------------
# slot grading

def slot_grade(w, h, full_comp, local_comp, mask):
    """Contrast of one slot against the thing actually behind it."""
    ratios, deltas = [], []
    for y in range(h):
        for x in range(w):
            if not mask(x, y):
                continue
            i = y * w + x
            ratios.append(contrast(full_comp[i], local_comp[i]))
            deltas.append(delta(full_comp[i], local_comp[i]))
    if not ratios:
        return {"pixels": 0, "p75_contrast": 0.0, "median_contrast": 0.0,
                "peak_contrast": 0.0, "visible_fraction": 0.0}
    return {
        "pixels": len(ratios),
        "p75_contrast": round(_pct(ratios, 0.75), 2),
        "median_contrast": round(_pct(ratios, 0.5), 2),
        "peak_contrast": round(max(ratios), 2),
        "visible_fraction": round(sum(1 for d in deltas if d > VISIBLE_DELTA) / len(deltas), 3),
    }


def alpha_slot(w, h, rgba, mask):
    vals = [rgba[(y * w + x) * 4 + 3] for y in range(h) for x in range(w) if mask(x, y)]
    if not vals:
        return {"pixels": 0, "peak_alpha": 0, "mean_alpha": 0.0, "opaque_fraction": 0.0}
    return {
        "pixels": len(vals),
        "peak_alpha": max(vals),
        "mean_alpha": round(sum(vals) / len(vals), 1),
        "opaque_fraction": round(sum(1 for a in vals if a >= 250) / len(vals), 3),
    }


def image_grade(w, h, rgba, page_fn, parts, masks=None, dx=0.0, dy=0.0):
    """Grade one icon on one page.

    parts: list of {"slot", "mask", "local": rgba or None, "decisive": bool}
    Returns coverage against the page plus per-slot contrast against the local
    backdrop rendered underneath each slot.
    """
    full = composite(w, h, rgba, page_fn)
    page = [page_fn(i % w, i // w) for i in range(w * h)]
    marks = [delta(full[i], page[i]) > VISIBLE_DELTA for i in range(w * h)]
    diffs = [delta(full[i], page[i]) for i in range(w * h) if marks[i]]

    slots = {}
    for p in parts:
        local = composite(w, h, p["local"], page_fn) if p["local"] else page
        slots[p["slot"]] = slot_grade(w, h, full, local, p["mask"])

    # The hole is not a drawn part: what makes it a marker is that the pixels
    # inside it are the page itself, seen against the ring drawn around them.
    if masks and "hole" in masks:
        rim = masks.get("rim") or masks.get("liner")
        if rim:
            slots["hole"] = hole_vs_rim(w, h, rgba, full, masks["hole"], rim)
    # "core" is the ordinary ink of the glyph. An outline, a backing plate, a
    # weld and the rim are construction: they are reported per slot but do not
    # stand in for the glyph body when the criteria ask "is the icon visible?".
    decisive = [p["slot"] for p in parts if p["decisive"]]
    core = [p["slot"] for p in parts if not p["decisive"] and p.get("role", "ink") == "ink"]
    return {
        "coverage": round(sum(marks) / (w * h), 3),
        "mean_mark_delta": round(sum(diffs) / max(1, len(diffs)), 1),
        "slots": slots,
        "decisive_contrast": round(min(slots[s]["p75_contrast"] for s in decisive), 2) if decisive else None,
        "core_contrast": round(min(slots[s]["p75_contrast"] for s in core), 2) if core else None,
    }


def hole_vs_rim(w, h, rgba, full, hole_mask, liner_mask):
    """Legibility of the pending marker.

    The marker is the enclosed hole, and the thing it must be distinguished
    from is the rim that borders it (the liner slot). The rim is what the eye
    compares the hole with, so a rim whose colour merges into the page also
    merges the hole away, even when the outer ring still reads.
    """
    hv = [full[y * w + x] for y in range(h) for x in range(w) if hole_mask(x, y)]
    rv = [full[y * w + x] for y in range(h) for x in range(w)
          if liner_mask(x, y) and rgba[(y * w + x) * 4 + 3] >= 250]
    if not hv or not rv:
        return {"pixels": 0, "p75_contrast": 0.0, "rim_pixels": len(rv)}
    sm = tuple(sum(c[i] for c in rv) / len(rv) for i in range(3))
    ratios = [contrast(c, sm) for c in hv]
    return {"pixels": len(hv), "rim_pixels": len(rv),
            "p75_contrast": round(_pct(ratios, 0.75), 2),
            "median_contrast": round(_pct(ratios, 0.5), 2),
            "peak_contrast": round(max(ratios), 2),
            "rim_rgb": [round(v, 1) for v in sm]}


# ---------------------------------------------------------------------------
# grayscale separation and erasure

def gray_of(comp):
    return [gray(c) for c in comp]


def separation(comp_a, comp_b, union):
    ds = [abs(gray_of(comp_a)[i] - gray_of(comp_b)[i]) for i in range(len(comp_a)) if union[i]]
    if not ds:
        return {"pixels": 0, "rms": 0.0, "peak": 0.0, "fraction_ge_18": 0.0}
    rms = (sum(d * d for d in ds) / len(ds)) ** 0.5
    return {
        "pixels": len(ds),
        "rms": round(rms, 2),
        "peak": round(max(ds), 1),
        "fraction_ge_18": round(sum(1 for d in ds if d >= GRAY_SEPARATION) / len(ds), 3),
    }


def erasure(w, h, full_rgba, reduced_rgba, page_fn, decisive_mask):
    cf = composite(w, h, full_rgba, page_fn)
    cr = composite(w, h, reduced_rgba, page_fn)
    inside, outside = [], []
    for y in range(h):
        for x in range(w):
            i = y * w + x
            d = delta(cf[i], cr[i])
            (inside if decisive_mask(x, y) else outside).append(d)
    return {
        "decisive_max_delta": round(max(inside), 1) if inside else None,
        "decisive_mean_delta": round(sum(inside) / len(inside), 2) if inside else None,
        "other_max_delta": round(max(outside), 1) if outside else None,
        "other_mean_delta": round(sum(outside) / len(outside), 2) if outside else None,
    }


def hole_alpha(w, h, rgba, hole_mask):
    vals = [rgba[(y * w + x) * 4 + 3] for y in range(h) for x in range(w) if hole_mask(x, y)]
    return {
        "pixels": len(vals),
        "max_alpha": max(vals) if vals else 0,
        "mean_alpha": round(sum(vals) / len(vals), 1) if vals else 0,
        "hollow": bool(vals) and max(vals) == 0,
    }
