#!/usr/bin/env python3
"""Generate prismplate scene JSON files from the moire atlas case manifest.

Only existing scene-contract features are used: closed M/L/Z rectangle
contours, solid brushes, per-shape invertible affine transforms, and
normal/multiply layers. No renderer code is added or modified.
"""
import json
import math
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
BLACK = {"r": 0.0, "g": 0.0, "b": 0.0, "a": 1.0}
WHITE = {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}
RINGS_PER_SHAPE = 28          # 32-shape/layer and 256-op admission limits


def rect_ring(x0, x1, half_len):
    """One closed vertical bar rectangle in grating local coordinates."""
    return [
        {"kind": "M", "points": [{"x": x0, "y": -half_len}]},
        {"kind": "L", "points": [{"x": x1, "y": -half_len}]},
        {"kind": "L", "points": [{"x": x1, "y": half_len}]},
        {"kind": "L", "points": [{"x": x0, "y": half_len}]},
        {"kind": "Z"},
    ]


def grating_shapes(gid, period, angle_deg, shift, size):
    """Tile closed bar rectangles across the full canvas under rotation."""
    theta = math.radians(angle_deg)
    half_span = size * 0.5 * (abs(math.cos(theta)) + abs(math.sin(theta))) + 2.0
    half_len = half_span
    bar = period / 2.0  # 50% duty cycle
    n = int(math.ceil(half_span / period))
    # Local bars occupy x in [-n*p, n*p]; shift moves the grating along its
    # local normal, which is the physical normal after rotation.
    rings = []
    for i in range(-n, n + 1):
        x0 = i * period + shift
        rings.append(rect_ring(x0, x0 + bar, half_len))
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    matrix = [cos_t, sin_t, -sin_t, cos_t, size / 2.0, size / 2.0]
    shapes = []
    for k in range(0, len(rings), RINGS_PER_SHAPE):
        chunk = rings[k:k + RINGS_PER_SHAPE]
        shapes.append({
            "id": f"{gid}-bar-{k // RINGS_PER_SHAPE:02d}",
            "path": [op for ring in chunk for op in ring],
            "fill_rule": "nonzero",
            "transform": matrix,
            "brush": {"kind": "solid", "color": dict(BLACK)},
        })
    return shapes


def build_scene(case):
    size = case["size"]
    layers = []
    blends = ["normal", "multiply"] if len(case["gratings"]) == 2 else ["normal"]
    for idx, (g, blend) in enumerate(zip(case["gratings"], blends)):
        layers.append({
            "id": f"grating-{idx + 1}",
            "opacity": 1.0,
            "blend": blend,
            "shapes": grating_shapes(
                f"g{idx + 1}", g["period"], g["angle_deg"], g["shift"], size),
        })
    return {
        "id": f"moire-{case['id'].lower()}",
        "title": case["title"],
        "document": {
            "width": size,
            "height": size,
            "background": dict(WHITE),
            "layers": layers,
        },
    }


def main():
    manifest = json.loads((ROOT / "cases.json").read_text())
    out_dir = ROOT / "scenes"
    out_dir.mkdir(exist_ok=True)
    written = []
    for case in manifest["cases"]:
        scene = build_scene(case)
        target = out_dir / f"{case['id']}.json"
        target.write_text(json.dumps(scene, ensure_ascii=False, indent=2))
        n_shapes = sum(len(l["shapes"]) for l in scene["document"]["layers"])
        n_rings = sum(
            len(s["path"]) // 5 for l in scene["document"]["layers"] for s in l["shapes"])
        written.append((case["id"], scene["document"]["width"], n_shapes, n_rings))
    for row in written:
        print("%-28s canvas=%d shapes=%d bars=%d" % row)


if __name__ == "__main__":
    main()
