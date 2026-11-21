"""Brush sampling and deterministic coverage rasterisation.

Two samplers share one scene evaluator:

* :func:`render_float` with grid=2 — the production four-point jitterless
  grid (sample offsets (0.25,0.25),(0.75,0.25),(0.25,0.75),(0.75,0.75));
* :func:`render_float` with a larger grid — an NxN Monte-Carlo-free
  quadrature used as the continuous-coverage reference.  It is an
  estimator, not production behaviour; its convergence is reported
  explicitly and must not be treated as an exact analytic area integral.

Gradient interpolation happens on premultiplied linear-light stops, which
is the production contract.  Brush premultiplication is cached per shape so
the expensive sRGB transfer runs once per stop rather than once per sample.
"""

from . import composite as comp
from . import geometry as geo
from . import srgb


def _color(d: dict) -> comp.Straight:
    return comp.Straight(float(d["r"]), float(d["g"]),
                         float(d["b"]), float(d["a"]))


def _prepare_brush(brush: dict) -> dict:
    """Precompute everything reusable across samples for one brush."""
    if brush["kind"] == "solid":
        return {"kind": "solid",
                "px": comp.Premul.from_straight(_color(brush["color"]))}
    if brush["kind"] != "linear":
        raise ValueError("unknown brush kind")
    fx, fy = float(brush["from"]["x"]), float(brush["from"]["y"])
    tx, ty = float(brush["to"]["x"]), float(brush["to"]["y"])
    dx, dy = tx - fx, ty - fy
    return {
        "kind": "linear",
        "axis": (fx, fy, dx, dy, dx * dx + dy * dy),
        "stops": [
            (float(s["at"]), comp.Premul.from_straight(_color(s["color"])))
            for s in brush["stops"]
        ],
    }


def sample_brush(prepared: dict, p: geo.Point) -> comp.Premul:
    if prepared["kind"] == "solid":
        return prepared["px"]
    fx, fy, dx, dy, denom = prepared["axis"]
    t = ((p[0] - fx) * dx + (p[1] - fy) * dy) / denom
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    stops = prepared["stops"]
    for i in range(1, len(stops)):
        at_a, x = stops[i - 1]
        at_z, y = stops[i]
        if t <= at_z:
            u = (t - at_a) / (at_z - at_a)
            return comp.Premul(
                x.r + (y.r - x.r) * u,
                x.g + (y.g - x.g) * u,
                x.b + (y.b - x.b) * u,
                x.a + (y.a - x.a) * u,
            )
    return stops[-1][1]


def _flatten_layer_paths(doc: dict) -> tuple:
    compiled = []
    background = comp.Premul.from_straight(_color(doc["background"]))
    for layer in doc["layers"]:
        clip = geo.flatten(layer["clip"]) if layer.get("clip") else []
        mask = geo.flatten(layer["mask"]) if layer.get("mask") else []
        clip_bbox = [geo.ring_bbox(r) for r in clip]
        mask_bbox = [geo.ring_bbox(r) for r in mask]
        shapes = []
        for shape in layer["shapes"]:
            rings = geo.flatten(shape["path"])
            inv = geo.Matrix(*(float(v) for v in shape["transform"])).inverse()
            local_bbox = [geo.ring_bbox(r) for r in rings]
            shapes.append((shape, rings, inv, local_bbox,
                           _prepare_brush(shape["brush"])))
        compiled.append((layer, clip, mask, shapes, clip_bbox, mask_bbox))
    return compiled, background


def _bbox_hit(bboxes: list, p: geo.Point) -> bool:
    px, py = p
    for x0, y0, x1, y1 in bboxes:
        if x0 <= px <= x1 and y0 <= py <= y1:
            return True
    return False


def evaluate_point(compiled: list, p: geo.Point,
                   background: comp.Premul) -> comp.Premul:
    """Full per-sample scene evaluation in premultiplied linear light."""
    dst = background
    for layer, clip, mask, shapes, clip_bbox, mask_bbox in compiled:
        if clip_bbox and not _bbox_hit(clip_bbox, p):
            continue
        if clip and not geo.path_contains(clip, p, "nonzero"):
            continue
        if mask_bbox and not _bbox_hit(mask_bbox, p):
            continue
        if mask and not geo.path_contains(mask, p, "nonzero"):
            continue
        src = comp.Premul(0.0, 0.0, 0.0, 0.0)
        for shape, rings, inv, local_bbox, brush in shapes:
            q = inv.apply(p)
            if not _bbox_hit(local_bbox, q):
                continue
            if geo.path_contains(rings, q, shape["fill_rule"]):
                src = comp.over(src, sample_brush(brush, q), "normal")
        dst = comp.over(dst, src.scale_alpha(float(layer["opacity"])),
                        layer["blend"])
    return dst


def _grid_offsets(n: int):
    return [((i + 0.5) / n, (j + 0.5) / n)
            for j in range(n) for i in range(n)]


def render_float(doc: dict, grid: int = 2) -> list:
    """Render to binary64 premultiplied pixels using a grid x grid sampler.

    For grid == 2 the accumulation order deliberately mirrors the
    production rasteriser exactly — each subpixel contribution is divided
    by four and summed sequentially — so that floating-point tie cases
    (e.g. an exact 64.5 code boundary) land on the same side as production.
    For larger grids the same per-sample division convention is used.
    """
    compiled, background = _flatten_layer_paths(doc)
    offsets = _grid_offsets(grid)
    width, height = int(doc["width"]), int(doc["height"])
    n2 = float(grid * grid)
    pixels = []
    for y in range(height):
        row = []
        for x in range(width):
            acc_r = acc_g = acc_b = acc_a = 0.0
            for ox, oy in offsets:
                s = evaluate_point(compiled, (x + ox, y + oy), background)
                acc_r += s.r / n2
                acc_g += s.g / n2
                acc_b += s.b / n2
                acc_a += s.a / n2
            row.append(comp.Premul(acc_r, acc_g, acc_b, acc_a))
        pixels.append(row)
    return pixels


def encode_nrgba8(pixels: list) -> list:
    """Apply the production quantisation contract to float premul pixels."""
    out = []
    for row in pixels:
        encoded_row = []
        for px in row:
            if px.a <= 0.0:
                encoded_row.append((0, 0, 0, 0))
                continue
            straight = px.unpremul()
            encoded_row.append((
                srgb.encode8(straight.r),
                srgb.encode8(straight.g),
                srgb.encode8(straight.b),
                srgb.encode8(min(1.0, px.a)),
            ))
        out.append(encoded_row)
    return out
