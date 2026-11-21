#!/usr/bin/env python3
"""Generate the legal prism-plate source drafts used by the error study.

The drafts exercise every quantisation-sensitive construct in the document
contract while staying inside every admission rule (canvas <= 512, closed
contours with >= 3 vertices, invertible transforms, stops anchored at 0/1,
layer count 1..16, shapes per layer 1..32).

Outputs JSON into ../sources/.  The generator is deterministic and depends
only on the Python standard library.
"""

import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
SOURCES = HERE.parent / "sources"


def rect(x, y, w, h):
    """Axis-aligned closed contour (four explicit vertices)."""
    return [
        {"kind": "M", "points": [{"x": x, "y": y}]},
        {"kind": "L", "points": [{"x": x + w, "y": y}]},
        {"kind": "L", "points": [{"x": x + w, "y": y + h}]},
        {"kind": "L", "points": [{"x": x, "y": y + h}]},
        {"kind": "Z"},
    ]


IDENTITY = [1, 0, 0, 1, 0, 0]


def solid(shape_id, x, y, w, h, rgba, rule="nonzero"):
    r, g, b, a = rgba
    return {
        "id": shape_id,
        "fill_rule": rule,
        "path": rect(x, y, w, h),
        "transform": IDENTITY,
        "brush": {"kind": "solid",
                  "color": {"r": r, "g": g, "b": b, "a": a}},
    }


def linear_brush(frm, to, stops):
    return {
        "kind": "linear",
        "from": {"x": frm[0], "y": frm[1]},
        "to": {"x": to[0], "y": to[1]},
        "color": {"r": 0, "g": 0, "b": 0, "a": 0},
        "stops": [
            {"at": at,
             "color": {"r": r, "g": g, "b": b, "a": a}}
            for at, (r, g, b, a) in stops
        ],
    }


# ---------------------------------------------------------------------------
# Scene 1: quant-probe — dark + light backdrops, low-alpha veil, screen and
# multiply glazes, fractional-geometry edge band, cubic contour, nested
# shape-over-shape compositing under group opacity.
# ---------------------------------------------------------------------------

def quant_probe():
    width, height = 256, 192
    layers = []

    # Opaque light panel on the right half -> two backdrop regimes coexist.
    layers.append({
        "id": "light-panel",
        "opacity": 1.0,
        "blend": "normal",
        "shapes": [solid("panel-face", 128.0, 0.0, 128.0, 192.0,
                         (0.93, 0.94, 0.95, 1.0))],
    })

    # Low-transparency veil: alpha ramps 0 -> 0.10 -> 0 across the top band.
    veil = {
        "id": "low-alpha-veil",
        "opacity": 1.0,
        "blend": "normal",
        "shapes": [{
            "id": "veil-sheet",
            "fill_rule": "nonzero",
            "path": rect(0.0, 0.0, 256.0, 64.0),
            "transform": IDENTITY,
            "brush": linear_brush(
                (0.0, 32.0), (256.0, 32.0),
                [(0.0, (0.85, 0.12, 0.10, 0.0)),
                 (0.5, (0.85, 0.12, 0.10, 0.10)),
                 (1.0, (0.10, 0.30, 0.90, 0.0))]),
        }],
    }
    layers.append(veil)

    # Screen glaze with group opacity, masked to a window on the left.
    layers.append({
        "id": "screen-glaze",
        "opacity": 0.35,
        "blend": "screen",
        "mask": rect(8.0, 72.0, 112.0, 56.0),
        "shapes": [solid("cyan-window", 8.0, 72.0, 112.0, 56.0,
                         (0.10, 0.62, 0.85, 0.60))],
    })

    # Multiply glaze on the light side.
    layers.append({
        "id": "multiply-glaze",
        "opacity": 0.40,
        "blend": "multiply",
        "shapes": [solid("magenta-window", 136.0, 72.0, 112.0, 56.0,
                         (0.75, 0.20, 0.60, 0.55))],
    })

    # Fractional-offset hard edges: coverage grid must produce partial alpha.
    layers.append({
        "id": "edge-band",
        "opacity": 1.0,
        "blend": "normal",
        "shapes": [
            solid("edge-quarter", 60.25, 140.0, 40.5, 44.0,
                  (0.95, 0.80, 0.10, 1.0)),
            solid("edge-third", 150.3333333333, 140.0, 30.0, 44.0,
                  (0.15, 0.55, 0.25, 1.0)),
        ],
    })

    # Cubic contour crossing pixel boundaries — flattening fixed at 24 segs.
    layers.append({
        "id": "curve-sheet",
        "opacity": 0.5,
        "blend": "normal",
        "shapes": [{
            "id": "cubic-blade",
            "fill_rule": "nonzero",
            "path": [
                {"kind": "M", "points": [{"x": 10.0, "y": 186.0}]},
                {"kind": "C", "points": [
                    {"x": 40.0, "y": 120.0},
                    {"x": 96.0, "y": 128.0},
                    {"x": 120.0, "y": 178.0}]},
                {"kind": "L", "points": [{"x": 10.0, "y": 186.0}]},
                {"kind": "Z"},
            ],
            "transform": IDENTITY,
            "brush": linear_brush(
                (10.0, 150.0), (120.0, 150.0),
                [(0.0, (0.20, 0.80, 0.70, 0.90)),
                 (1.0, (0.90, 0.50, 0.20, 0.30))]),
        }],
    })

    # Nested shape-over-shape inside one layer, then group opacity 0.5.
    layers.append({
        "id": "nested-stack",
        "opacity": 0.5,
        "blend": "normal",
        "shapes": [
            solid("stack-base", 168.0, 132.0, 80.0, 52.0,
                  (0.30, 0.30, 0.80, 0.80)),
            solid("stack-top", 196.0, 146.0, 44.0, 30.0,
                  (0.95, 0.95, 0.95, 0.45)),
        ],
    })

    return {
        "id": "quant-probe",
        "title": "8位量化探针稿：低透明/深浅底/多层混合",
        "document": {
            "width": width,
            "height": height,
            "background": {"r": 0.04, "g": 0.05, "b": 0.08, "a": 1.0},
            "layers": layers,
        },
    }


# ---------------------------------------------------------------------------
# Scene 2: code-lattice-probe — values parked on rounding boundaries and
# alpha thresholds, over dark and light backdrops.
# ---------------------------------------------------------------------------

def code_lattice_probe():
    # 16 columns x 8 rows of 8px cells on a 128x64 canvas.  Admission caps
    # shapes per layer at 32 and layers at 16, so the 128 cells are split
    # into 4 bands of 32; the opaque light-half backdrop is its own bottom
    # layer (5 layers total).
    cell = 8
    ncol, nrow = 16, 8
    by_band: dict = {}
    for row in range(nrow):
        for col in range(ncol):
            # Base code k, with four offset classes cycling across columns.
            k = 4 + row * 30                      # spread codes 4..214
            offsets = (0.0, -1e-9, 1e-9, 0.5 / 255.0)
            delta = offsets[col % 4]
            v = max(0.0, min(1.0, k / 255.0 + delta))
            # Rows 0..1 park alpha near the 0->1 code boundary; remaining
            # rows sweep alpha in exact fractional steps.
            if row == 0:
                alpha = (0.0, 0.5 / 255.0 - 1e-12,
                         0.5 / 255.0, 0.5 / 255.0 + 1e-12)[col % 4]
            elif row == 1:
                alpha = (0.0, 0.001, 0.002, 1.0 / 255.0)[col % 4]
            else:
                alpha = min(1.0, (col + 1) / 16.0)
            by_band.setdefault(row // 2, []).append(solid(
                f"cell-{row:02d}-{col:02d}",
                float(col * cell), float(row * cell),
                float(cell), float(cell),
                (v, v, v, alpha)))
    layers = [{
        "id": "light-half",
        "opacity": 1.0,
        "blend": "normal",
        "shapes": [solid("half-backdrop", 64.0, 0.0, 64.0,
                         float(nrow * cell),
                         (0.92, 0.92, 0.90, 1.0))],    }]
    for band, shapes in sorted(by_band.items()):
        assert len(shapes) <= 32
        layers.append({
            "id": f"lattice-band-{band}",
            "opacity": 1.0,
            "blend": "normal",
            "shapes": shapes,
        })
    assert len(layers) <= 16
    return {
        "id": "code-lattice-probe",
        "title": "码值边界与alpha阈值探针稿",
        "document": {
            "width": ncol * cell,
            "height": nrow * cell,
            "background": {"r": 0.05, "g": 0.05, "b": 0.06, "a": 1.0},
            "layers": layers,
        },
    }


# ---------------------------------------------------------------------------
# Scene 3: veil-demo — identical tiny coverage, wildly different straight
# RGB.  Proves straight-RGB distance is meaningless before compositing.
# ---------------------------------------------------------------------------

def veil_demo():
    return {
        "id": "veil-demo",
        "title": "低透明直通RGB反差演示稿",
        "document": {
            "width": 128,
            "height": 64,
            "background": {"r": 0.5, "g": 0.5, "b": 0.5, "a": 1.0},
            "layers": [{
                "id": "veils",
                "opacity": 1.0,
                "blend": "normal",
                "shapes": [
                    solid("red-whisper", 0.0, 0.0, 64.0, 64.0,
                          (1.0, 0.0, 0.0, 0.002)),
                    solid("blue-whisper", 64.0, 0.0, 64.0, 64.0,
                          (0.0, 0.0, 1.0, 0.002)),
                ],
            }],
        },
    }


# ---------------------------------------------------------------------------
# Scene 4: alpha-channel-probe — a transparent canvas (background alpha 0)
# so the emitted PNG keeps an alpha channel (colour type 6).  Exercises the
# alpha 8-bit lattice, the 0->1 alpha boundary and the production contract
# that fully transparent pixels encode RGB as (0,0,0).
# ---------------------------------------------------------------------------

def alpha_channel_probe():
    width = height = 64
    return {
        "id": "alpha-channel-probe",
        "title": "alpha通道与透明清零契约探针",
        "document": {
            "width": width,
            "height": height,
            # Fully transparent background: uncovered pixels must encode
            # (0,0,0,0), exercising the alpha=0 RGB-clearing rule.
            "background": {"r": 0.05, "g": 0.05, "b": 0.06, "a": 0.0},
            "layers": [{
                "id": "alpha-sweep",
                "opacity": 1.0,
                "blend": "normal",
                "shapes": [{
                    "id": "sweep-sheet",
                    "fill_rule": "nonzero",
                    "path": rect(0.0, 0.0, 64.0, 40.0),
                    "transform": IDENTITY,
                    "brush": linear_brush(
                        (0.0, 20.0), (64.0, 20.0),
                        [(0.0, (0.90, 0.20, 0.15, 0.0)),
                         (0.25, (0.90, 0.20, 0.15, 0.005)),
                         (0.5, (0.90, 0.20, 0.15, 0.02)),
                         (1.0, (0.10, 0.30, 0.90, 0.60))]),
                }, {
                    "id": "half-block",
                    "fill_rule": "nonzero",
                    "path": rect(8.0, 44.0, 24.0, 16.0),
                    "transform": IDENTITY,
                    "brush": {"kind": "solid",
                              "color": {"r": 0.4, "g": 0.7, "b": 0.3,
                                        "a": 0.5}},
                }],
            }],
        },
    }


def write(doc, name):
    out = SOURCES / name
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return out


def main():
    SOURCES.mkdir(parents=True, exist_ok=True)
    paths = [
        write(quant_probe(), "quant-probe.json"),
        write(code_lattice_probe(), "code-lattice-probe.json"),
        write(veil_demo(), "veil-demo.json"),
        write(alpha_channel_probe(), "alpha-channel-probe.json"),
    ]

    # Design variants of quant-probe: each changes one real design decision.
    base = quant_probe()
    doc = base["document"]

    v_edge = json.loads(json.dumps(base))
    band = v_edge["document"]["layers"][4]["shapes"][0]
    band["path"] = rect(60.75, 140.0, 40.5, 44.0)   # 0.5 px edge shift
    v_edge["id"] = "quant-probe-edge-shift"
    v_edge["title"] = "设计变更变体：硬边平移0.5px"
    paths.append(write(v_edge, "quant-probe-edge-shift.json"))

    v_color = json.loads(json.dumps(base))
    # Recolour the *visible* middle veil stop (alpha 0.10), not an endpoint
    # whose alpha is zero (a premultiplied zero-alpha stop carries no colour).
    mid = v_color["document"]["layers"][1]["shapes"][0]["brush"]["stops"][1]
    assert mid["at"] == 0.5 and mid["color"]["a"] == 0.10
    mid["color"]["r"] = 0.55
    v_color["id"] = "quant-probe-recolour"
    v_color["title"] = "设计变更变体：可见薄纱色标改色"
    paths.append(write(v_color, "quant-probe-recolour.json"))

    v_blend = json.loads(json.dumps(base))
    v_blend["document"]["layers"][2]["blend"] = "multiply"
    v_blend["id"] = "quant-probe-blend-swap"
    v_blend["title"] = "设计变更变体：screen改multiply"
    paths.append(write(v_blend, "quant-probe-blend-swap.json"))

    v_op = json.loads(json.dumps(base))
    v_op["document"]["layers"][2]["opacity"] = 0.60
    v_op["id"] = "quant-probe-opacity"
    v_op["title"] = "设计变更变体：组透明度0.35改0.60"
    paths.append(write(v_op, "quant-probe-opacity.json"))

    # Sub-design-change control: an opaque panel tint chosen to land within
    # the 1-code quantisation envelope after rounding.  It is a real design
    # edit (a different source colour) whose pixels are indistinguishable
    # from quantisation noise under any single global code threshold.
    v_tint = json.loads(json.dumps(base))
    panel = v_tint["document"]["layers"][0]["shapes"][0]
    panel["brush"]["color"]["g"] = 0.94 + 1.0 / 255.0
    v_tint["id"] = "quant-probe-micro-tint"
    v_tint["title"] = "设计变更变体：面板单码级改色（落入量化包络）"
    paths.append(write(v_tint, "quant-probe-micro-tint.json"))

    for p in paths:
        print("wrote", p.relative_to(HERE.parent))


if __name__ == "__main__":
    main()
