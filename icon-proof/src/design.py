"""Editable source for the 16x16 terminal status icon family.

This module is the single definition of the artwork. It emits prism-plate
Document JSON, which is the canonical editable source plate: the terminal can
POST/PUT any emitted document to the service unchanged and re-render it later.

Coordinate and rendering contract
---------------------------------
* canvas 16x16, document background fully transparent (alpha 0);
* coordinates are canvas pixels, origin top-left, matching prism-plate's
  document space; the renderer samples 2x2 points per pixel, so a polygon edge
  on a half-pixel boundary produces 50 % alpha on both sides;
* the renderer has no stroke primitive, so every "stroke" is a filled polygon;
* a sub-pixel placement is modelled as a pure translation of every shape in the
  layer by 0.5 px, which is what a compact terminal layout does when a row
  height lands between device pixels.

Status vocabulary
-----------------
pending   a hollow ring: the enclosed transparent hole IS the status mark
approved  a check: down-stroke, then the decisive up-stroke
rejected  a cross: one diagonal, then the decisive second diagonal

The decisive stroke is named in DECISIVE. It is the stroke whose absence
degrades the glyph into a different or ambiguous shape, and the report measures
it separately from the rest of the icon. That separation is what distinguishes
"this backdrop changed the contrast of the whole icon" from "this backdrop
erased exactly the stroke that carries the status".
"""
from __future__ import annotations

import json
import math

GRID = 16
CANVAS = (GRID / 2.0, GRID / 2.0)

# --- palette (sRGB hex) ----------------------------------------------------
INK = "#101820"          # dark single-tone ink
PAPER = "#F7F9FC"        # light single-tone ink
MID = "#6E7580"          # mid tone; deliberately near the 3:1 floor
LIGHT_PAGE = "#FFFFFF"
DARK_PAGE = "#14161C"

# --- geometry --------------------------------------------------------------
W_BOLD = 2.6      # status strokes at 16 px
W_FINE = 2.0      # compact construction
W_HAIR = 1.5      # a stroke that lands on a half-pixel boundary by nature
W_THREAD = 1.0    # exactly one device pixel
HALO = 1.2        # outline thickness of the two-tone construction
PLATE_R = 7.4     # opaque backing disc radius

CHECK = [(3.2, 8.6), (6.6, 12.2), (12.8, 4.2)]     # down, then up
CROSS_A = [(4.0, 4.0), (12.0, 12.0)]                # first diagonal
CROSS_B = [(12.0, 4.0), (4.0, 12.0)]                # decisive diagonal

RING_OUT = 6.6
RING_IN = 4.6
HOLE_R = 3.2

STATUSES = ["pending", "approved", "rejected"]
VARIANTS = ["plain-ink", "plain-paper", "mid", "fine", "hair", "thread",
            "halo", "duplex", "badge", "erasure-light", "erasure-dark"]
OFFSETS = [(0.0, 0.0), (0.5, 0.0), (0.0, 0.5), (0.5, 0.5)]

DECISIVE = {
    "approved": {"up"},
    "rejected": {"diag_b"},
    "pending": {"hole"},   # a hole is a region, not a stroke; see measure.py
}


def rgb(hexstr):
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


# ---------------------------------------------------------------------------
# polygons

def _unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return dx / n, dy / n, n


def thick_line(a, b, width, extend=0.0):
    ux, uy, n = _unit(a, b)
    ax, ay = a[0] - ux * extend, a[1] - uy * extend
    bx, by = b[0] + ux * extend, b[1] + uy * extend
    nx, ny = -uy * width / 2.0, ux * width / 2.0
    return [(ax + nx, ay + ny), (bx + nx, by + ny), (bx - nx, by - ny), (ax - nx, ay - ny)]


def disc(cx, cy, r, n=32):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
            for i in range(n)]


def annulus(cx, cy, r_out, r_in, n=32):
    return (disc(cx, cy, r_out, n), disc(cx, cy, r_in, n))


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# ---------------------------------------------------------------------------
# scene emission

def _ops(rings):
    ops = []
    for ring in rings:
        pts = ring if isinstance(ring, list) else list(ring)
        ops.append({"kind": "M", "points": [{"x": pts[0][0], "y": pts[0][1]}]})
        for x, y in pts[1:]:
            ops.append({"kind": "L", "points": [{"x": x, "y": y}]})
        ops.append({"kind": "Z"})
    return ops


def _solid(hexstr):
    r, g, b = rgb(hexstr)
    return {"kind": "solid", "color": {"r": r, "g": g, "b": b, "a": 1.0}}


def shape(sid, rings, hexstr, rule="nonzero", dx=0.0, dy=0.0):
    flat = []
    for r in rings:
        if r and isinstance(r[0], tuple):
            flat.append(r)
        else:
            flat.extend(r)
    return {
        "id": sid,
        "path": _ops(flat),
        "fill_rule": rule,
        "brush": _solid(hexstr),
        "transform": [1, 0, 0, 1, dx, dy],
    }


def document(shapes, dx=0.0, dy=0.0):
    for s in shapes:
        s["transform"] = [1, 0, 0, 1, dx, dy]
    return {
        "width": GRID,
        "height": GRID,
        "background": {"r": 0.0, "g": 0.0, "b": 0.0, "a": 0.0},
        "layers": [{"id": "icon", "opacity": 1.0, "blend": "normal", "shapes": shapes}],
    }


# ---------------------------------------------------------------------------
# analytic masks (design coordinates; translated by dx/dy like the plate)

def mask_seg(a, b, width, dx, dy):
    def f(x, y):
        px, py = x + 0.5 - dx, y + 0.5 - dy
        ux, uy, n = _unit(a, b)
        t = (px - a[0]) * ux + (py - a[1]) * uy
        if t < 0 or t > n:
            return False
        return abs(-(px - a[0]) * uy + (py - a[1]) * ux) <= width / 2.0
    return f


def mask_disc(cx, cy, r, dx, dy, inner=0.0):
    def f(x, y):
        px, py = x + 0.5 - dx, y + 0.5 - dy
        d2 = (px - cx) ** 2 + (py - cy) ** 2
        return inner * inner <= d2 <= r * r
    return f


def mask_hole(dx, dy, r=HOLE_R):
    return mask_disc(*CANVAS, r, dx, dy)


def mask_hole_core(dx, dy):
    """Inner part of the hole, one pixel inside the rim: must be fully clear."""
    return mask_disc(*CANVAS, HOLE_R - 1.0, dx, dy)


# ---------------------------------------------------------------------------
# variant construction
#
# Every variant returns parts in draw order. A part is:
#   {"slot": name, "shape": scene shape, "mask": pixel predicate}
# and the measurement harness renders each part's prefix (everything below it)
# to obtain the local backdrop the part is actually seen against.

def _glyph_parts(status, width, scale, colours, prefix=""):
    """Strokes and joint for a check/cross. Returns (parts, decisive slots)."""
    cx, cy = CANVAS
    def s(p):
        return (p[0] - cx) * scale + cx, (p[1] - cy) * scale + cy

    segs, joints = [], []
    if status == "approved":
        a, b, c = (s(p) for p in CHECK)
        segs = [("down", a, b), ("up", b, c)]
        joints = [(b, width / 2.0)]
    elif status == "rejected":
        a, b = (s(p) for p in CROSS_A)
        c, d = (s(p) for p in CROSS_B)
        segs = [("diag_a", a, b), ("diag_b", c, d)]
        joints = [((cx, cy), width / 2.0)]
    else:
        raise ValueError(status)

    parts = []
    for name, a, b in segs:
        parts.append({
            "slot": prefix + name,
            "shape": shape(prefix + name, [thick_line(a, b, width)], colours[name]),
            "mask": mask_seg(a, b, width, 0.0, 0.0),
        })
    for i, (c, r) in enumerate(joints):
        parts.append({
            "slot": prefix + "joint",
            "role": "weld",   # same colour as the strokes: no independent edge
            "shape": shape(prefix + "joint", [disc(c[0], c[1], r)], colours["joint"]),
            "mask": mask_disc(c[0], c[1], r, 0.0, 0.0),
        })
    return parts


def _halo_parts(status, parts, halo_hexstr):
    out = []
    cx, cy = CANVAS
    if status == "pending":
        out.append({"slot": "halo", "role": "outline", "shape": shape("halo", [annulus(cx, cy, RING_OUT + HALO, RING_IN - HALO)], halo_hexstr, "evenodd"),
                    "mask": mask_disc(cx, cy, RING_OUT + HALO, 0.0, 0.0, RING_IN - HALO)})
        out.append({"slot": "liner", "role": "rim", "shape": shape("liner", [annulus(cx, cy, RING_IN, RING_IN - HALO)], halo_hexstr, "evenodd"),
                    "mask": mask_disc(cx, cy, RING_IN, 0.0, 0.0, RING_IN - HALO)})
        return out
    # stroke halo: a wider, extended stroke under each stroke, plus joint cover
    # One polygon for the whole outline, not one per stroke: two overlapping
    # halo strokes with the same fill are harmless with nonzero winding, but a
    # single union polygon keeps the outline exactly HALO thick all the way
    # round the vertex instead of bulging at the join.
    centre = []
    for p in parts:
        if p["slot"].endswith("joint"):
            continue
        centre.append(p["_seg"])
    rings = []
    for (a, b, w) in centre:
        rings.append(thick_line(a, b, w + 2 * HALO, extend=HALO))
    # cover the miter gap at the vertex
    for p in parts:
        if p["slot"].endswith("joint"):
            c, r = p["_joint"]
            rings.append(disc(c[0], c[1], r + HALO))
    out.append({"slot": "halo", "role": "outline",
                "shape": shape("halo", rings, halo_hexstr),
                "mask": _halo_mask(centre, parts, HALO)})
    return out


def _halo_mask(centre, parts, halo):
    segs = [(a, b, w + 2 * halo) for (a, b, w) in centre]
    joints = [p["_joint"] for p in parts if p["slot"].endswith("joint")]

    def f(x, y):
        if any(mask_seg(a, b, w, 0.0, 0.0)(x, y) for a, b, w in segs):
            return True
        return any(mask_disc(c[0], c[1], r + halo, 0.0, 0.0)(x, y) for c, r in joints)
    return f


def _ring_parts(colour, liner_colour):
    cx, cy = CANVAS
    return [
        {"slot": "liner", "role": "rim", "shape": shape("liner", [annulus(cx, cy, RING_IN, HOLE_R)], liner_colour, "evenodd"),
         "mask": mask_disc(cx, cy, RING_IN, 0.0, 0.0, HOLE_R)},
        {"slot": "ring", "shape": shape("ring", [annulus(cx, cy, RING_OUT, RING_IN)], colour, "evenodd"),
         "mask": mask_disc(cx, cy, RING_OUT, 0.0, 0.0, RING_IN)},
    ]


def build(status, variant, dx=0.0, dy=0.0):
    """Return {"document", "parts", "masks", "meta"} for one icon."""
    if status not in STATUSES:
        raise ValueError(status)
    if variant not in VARIANTS:
        raise ValueError(variant)

    scale = 1.0
    width = W_BOLD
    parts = []
    notes = {}

    if variant == "fine":
        width = W_FINE
    elif variant == "hair":
        width = W_HAIR
    elif variant == "thread":
        width = W_THREAD
    elif variant == "badge":
        scale = 0.76
        width = W_BOLD * scale

    # ---- single-tone variants -------------------------------------------
    if variant in ("plain-ink", "plain-paper", "mid", "fine", "hair", "thread"):
        colour = {"plain-ink": INK, "plain-paper": PAPER, "mid": MID,
                  "fine": INK, "hair": INK, "thread": INK}[variant]
        if status == "pending":
            parts = _ring_parts(colour, colour)
        else:
            parts = _glyph_parts(status, width, scale,
                                 {"down": colour, "up": colour, "diag_a": colour,
                                  "diag_b": colour, "joint": colour})

    # ---- two-tone halo ---------------------------------------------------
    elif variant == "halo":
        if status == "pending":
            cx, cy = CANVAS
            parts = _halo_parts("pending", [], PAPER) + [
                {"slot": "ring", "shape": shape("ring", [annulus(cx, cy, RING_OUT, RING_IN)], INK, "evenodd"),
                 "mask": mask_disc(cx, cy, RING_OUT, 0.0, 0.0, RING_IN)},
            ]
        else:
            core = _glyph_parts(status, width, scale,
                                {"down": INK, "up": INK, "diag_a": INK,
                                 "diag_b": INK, "joint": INK})
            _tag_geometry(core)
            parts = _halo_parts(status, core, PAPER) + core

    # ---- duplex: light outline + a rim that reads on either page ----------
    elif variant == "duplex":
        if status == "pending":
            cx, cy = CANVAS
            parts = _halo_parts("pending", [], PAPER) + [
                {"slot": "rim", "role": "rim",
                 "shape": shape("rim", [annulus(cx, cy, RING_IN, HOLE_R)], MID, "evenodd"),
                 "mask": mask_disc(cx, cy, RING_IN, 0.0, 0.0, HOLE_R)},
                {"slot": "ring", "shape": shape("ring", [annulus(cx, cy, RING_OUT, RING_IN)], INK, "evenodd"),
                 "mask": mask_disc(cx, cy, RING_OUT, 0.0, 0.0, RING_IN)},
            ]
        else:
            core = _glyph_parts(status, width, scale,
                                {"down": INK, "up": INK, "diag_a": INK,
                                 "diag_b": INK, "joint": INK})
            _tag_geometry(core)
            parts = _halo_parts(status, core, PAPER) + core

    # ---- opaque backing plate -------------------------------------------
    elif variant == "badge":
        if status == "pending":
            # the plate is an annulus so the status hole stays a real hole
            parts = [{"slot": "badge", "role": "plate",
                      "shape": shape("badge", [annulus(*CANVAS, PLATE_R, RING_IN)], PAPER, "evenodd"),
                      "mask": mask_disc(*CANVAS, PLATE_R, 0.0, 0.0, RING_IN)}]
            # The plate can make the RING page-independent, but not the hole:
            # the hole is transparent by definition, so the rim must be the
            # mid tone that contrasts with both white and near-black pages.
            cx, cy = CANVAS
            parts += [
                {"slot": "liner", "role": "rim",
                 "shape": shape("liner", [annulus(cx, cy, RING_IN, HOLE_R)], MID, "evenodd"),
                 "mask": mask_disc(cx, cy, RING_IN, 0.0, 0.0, HOLE_R)},
                {"slot": "ring", "shape": shape("ring", [annulus(cx, cy, RING_OUT, RING_IN)], INK, "evenodd"),
                 "mask": mask_disc(cx, cy, RING_OUT, 0.0, 0.0, RING_IN)},
            ]
        else:
            parts = [{"slot": "badge", "role": "plate",
                      "shape": shape("badge", [disc(*CANVAS, PLATE_R)], PAPER),
                      "mask": mask_disc(*CANVAS, PLATE_R, 0.0, 0.0)}]
            parts += _glyph_parts(status, width, scale,
                                  {"down": INK, "up": INK, "diag_a": INK,
                                   "diag_b": INK, "joint": INK})

    # ---- deliberate decisive-stroke erasure demos ------------------------
    elif variant in ("erasure-light", "erasure-dark"):
        ink = MID
        erased = LIGHT_PAGE if variant == "erasure-light" else DARK_PAGE
        dec = DECISIVE[status]
        if status == "pending":
            parts = [{"slot": "liner", "role": "rim", "shape": shape("liner", [annulus(*CANVAS, RING_IN, HOLE_R)], erased, "evenodd"),
                      "mask": mask_disc(*CANVAS, RING_IN, 0.0, 0.0, HOLE_R)},
                     {"slot": "ring", "shape": shape("ring", [annulus(*CANVAS, RING_OUT, RING_IN)], ink, "evenodd"),
                      "mask": mask_disc(*CANVAS, RING_OUT, 0.0, 0.0, RING_IN)}]
        else:
            cols = {k: ink for k in ("down", "up", "diag_a", "diag_b", "joint")}
            for k in dec:
                cols[k] = erased
            parts = _glyph_parts(status, width, scale, cols)
            notes["erased_colour"] = erased

    # A pixel where two same-colour strokes overlap carries no information
    # about either stroke: it would be ink whatever the upper stroke did. Grade
    # each ink part on its *exclusive* pixels, i.e. those no other ink part
    # covers. That is the part's own contribution to the glyph.
    ink = [p for p in parts if p.get("role") == "ink"]
    for p in parts:
        if p.get("role") != "ink":
            p["exclusive"] = p["mask"]
            continue
        others = [q["mask"] for q in ink if q is not p]

        def exclusive(x, y, mine=p["mask"], others=others):
            # Erode by one pixel: a pixel adjacent to another ink part picks up
            # that part's antialiased fringe, so it is not evidence about this
            # stroke alone.
            for oy in (-1, 0, 1):
                for ox in (-1, 0, 1):
                    if not mine(x + ox, y + oy):
                        return False
                    if any(o(x + ox, y + oy) for o in others):
                        return False
            return True
        p["exclusive"] = exclusive

    # translate masks with the plate
    if dx or dy:
        for p in parts:
            p["mask"] = _translate_mask(p["mask"], dx, dy)
            p["exclusive"] = _translate_mask(p["exclusive"], dx, dy)

    for p in parts:
        p.setdefault("role", "ink")
    doc = document([p["shape"] for p in parts], dx, dy)
    # order: draw order is the list order; halo parts precede core by construction
    masks = {p["slot"]: p["mask"] for p in parts}
    if status == "pending":
        # The hole marker is graded against the page (there is deliberately
        # nothing under it), so its local backdrop is an empty render.
        masks["hole"] = mask_hole(dx, dy)
        masks["hole_core"] = mask_hole_core(dx, dy)
    # exposed per-slot metadata for the measurement harness
    for p, src in zip(parts, parts):
        pass
    return {
        "document": doc,
        "parts": [{"slot": p["slot"], "mask": p["exclusive"], "full_mask": p["mask"],
                   "role": p["role"]} for p in parts],
        "masks": masks,
        "meta": {"status": status, "variant": variant, "dx": dx, "dy": dy, **notes},
        "decisive": sorted(DECISIVE[status]),
        "geometry": {p["slot"]: _slot_geometry(p) for p in parts},
    }


def _slot_geometry(part):
    return {}


def _tag_geometry(core):
    cx, cy = CANVAS
    if len(core) == 3 and not core[0]["slot"].endswith("joint"):
        names = ["down", "up"]
        pts = [CHECK[0], CHECK[1], CHECK[2]]
        for i, name in enumerate(names):
            core[i]["_seg"] = (pts[i], pts[i + 1], W_BOLD)
        core[2]["_joint"] = (pts[1], W_BOLD / 2.0)
    else:
        core[0]["_seg"] = (CROSS_A[0], CROSS_A[1], W_BOLD)
        core[1]["_seg"] = (CROSS_B[0], CROSS_B[1], W_BOLD)
        core[2]["_joint"] = ((cx, cy), W_BOLD / 2.0)


def _translate_mask(fn, dx, dy):
    return lambda x, y: fn(x - dx, y - dy) if (dx or dy) else fn(x, y)


# ---------------------------------------------------------------------------
# measurement instruments

def scene_without_slot(built, slot):
    """Same plate with one slot removed; used as a local-backdrop probe."""
    doc = json.loads(json.dumps(built["document"]))
    keep = [s for s in doc["layers"][0]["shapes"] if _shape_slot(s["id"], built) != slot]
    doc["layers"][0]["shapes"] = keep
    if not keep:
        doc["layers"][0]["shapes"] = [shape("empty", [rect(-4, -4, -3, -3)], INK)]
    return doc


def prefix_document(built, slot):
    """Everything below `slot` in draw order, and nothing at or above it.

    Used as the real local backdrop for grading `slot`. If nothing is below it
    the plate still needs one shape, so a transparent sliver far outside the
    canvas stands in (it composites to nothing).
    """
    doc = json.loads(json.dumps(built["document"]))
    order = [p["slot"] for p in built["parts"]]
    idx = order.index(slot)
    keep = [s for s, o in zip(doc["layers"][0]["shapes"], order) if order.index(o) < idx]
    doc["layers"][0]["shapes"] = keep or [shape("empty", [rect(-4, -4, -3, -3)], INK)]
    return doc


def _shape_slot(shape_id, built):
    for s, p in zip(built["document"]["layers"][0]["shapes"], built["parts"]):
        if s["id"] == shape_id:
            return p["slot"]
    return shape_id


def filled_hole_document(built):
    """Pending probe: same icon with the enclosed hole painted in rim colour.

    The rim is the colour the eye actually compares the hole with, so that is
    the colour the probe fills with. If a page merges this fill into its own
    surface, the full and the filled render become equal inside the hole, and
    the erasure measurement records exactly that.
    """
    doc = json.loads(json.dumps(built["document"]))
    colour = INK
    for slot in ("liner", "ring"):
        shape_obj = None
        for p in built["parts"]:
            if p["slot"] != slot:
                continue
            for shp in doc["layers"][0]["shapes"]:
                if _shape_slot(shp["id"], built) == slot:
                    shape_obj = shp
                    break
            break
        if shape_obj is not None:
            colour = shape_obj["brush"]["color"]
            break
    fill = {
        "id": "hole-fill",
        "path": _ops([disc(*CANVAS, RING_IN)]),
        "fill_rule": "nonzero",
        "brush": {"kind": "solid", "color": dict(colour)},
        "transform": [1, 0, 0, 1, built["meta"]["dx"], built["meta"]["dy"]],
    }
    doc["layers"][0]["shapes"] = [fill] + doc["layers"][0]["shapes"]
    return doc


def blank_document(dx=0.0, dy=0.0):
    """A layer that draws nothing but still satisfies prism-plate's 1..32 shapes."""
    return document([shape("empty", [rect(-4, -4, -3, -3)], INK)], dx, dy)


# ---------------------------------------------------------------------------
# axis-alignment probe: a single bar whose edges sit on half-pixel boundaries

HAIRLINE = 1.0

def axis_probe(orientation, width, dx=0.0, dy=0.0):
    # Centre the bar so that at offset 0 its edges are integers: an odd-width bar
    # is centred on a pixel centre, an even-width bar on a pixel boundary.
    centre = 7.5 if int(width) % 2 == 1 else 8.0
    if orientation == "h":
        bar = rect(3, centre - width / 2.0, 13, centre + width / 2.0)
    else:
        bar = rect(centre - width / 2.0, 3, centre + width / 2.0, 13)
    return {"document": document([shape("bar", [bar], INK)], dx, dy),
            "meta": {"orientation": orientation, "width": width, "dx": dx, "dy": dy}}
