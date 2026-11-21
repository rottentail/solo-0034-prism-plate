#!/usr/bin/env python3
"""Generate the small, single-purpose acceptance scenes.

Each scene isolates one acceptance question so that a viewer difference can be
attributed to a mechanism instead of to a whole picture.  Run from the repo
root:

    python3 acceptance/harness/scenes.py

The generated JSON is committed under acceptance/scenes/.  Probe points live in
acceptance/scenes/probes.json because the scene contract rejects unknown
fields.

Scene contract reminder (internal/scene/scene.go):
  document background and every stop colour are 0..1 sRGB values;
  M/L/Q/C/Z contours must close; a contour needs at least three vertices;
  layers carry optional clip and binary mask paths in page coordinates;
  shapes carry a local fill path, a fill rule and an invertible transform.
"""
import json
import math
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "acceptance" / "scenes"


def pt(x, y):
    return {"x": x, "y": y}


def solid(r, g, b, a=1.0):
    return {"kind": "solid", "color": {"r": r, "g": g, "b": b, "a": a}}


def linear(x1, y1, x2, y2, stops):
    return {
        "kind": "linear",
        "from": pt(x1, y1),
        "to": pt(x2, y2),
        "color": {"r": 0, "g": 0, "b": 0, "a": 0},
        "stops": [{"at": at, "color": {"r": c[0], "g": c[1], "b": c[2], "a": c[3]}}
                  for at, c in stops],
    }


def closed(points):
    ops = [{"kind": "M", "points": [pt(*points[0])]}]
    for p in points[1:]:
        ops.append({"kind": "L", "points": [pt(*p)]})
    ops.append({"kind": "Z"})
    return ops


def rect(x0, y0, x1, y1, reverse=False):
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return closed(list(reversed(corners)) if reverse else corners)


def diamond(cx, cy, rx, ry, reverse=False):
    corners = [(cx, cy - ry), (cx + rx, cy), (cx, cy + ry), (cx - rx, cy)]
    return closed(list(reversed(corners)) if reverse else corners)


def shape(sid, ops, brush, fill_rule="nonzero", transform=None):
    return {"id": sid, "fill_rule": fill_rule, "path": ops,
            "transform": transform or [1, 0, 0, 1, 0, 0], "brush": brush}


def layer(lid, shapes, opacity=1.0, blend="normal", clip=None, mask=None):
    out = {"id": lid, "opacity": opacity, "blend": blend, "shapes": shapes}
    if clip:
        out["clip"] = clip
    if mask:
        out["mask"] = mask
    return out


def doc(w, h, bg, layers):
    return {"width": w, "height": h,
            "background": {"r": bg[0], "g": bg[1], "b": bg[2], "a": bg[3]},
            "layers": layers}


def rotation(deg, cx, cy):
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return [c, s, -s, c, cx - c * cx + s * cy, cy - s * cx - c * cy]


GREY = (0.5, 0.5, 0.5, 1)
CLEAR = (0.0, 0.0, 0.0, 0.0)
SCENES = {}

# A. Solid fills under one affine transform.  This is the control: an opaque
# document, opaque brushes, no gradients, no blend modes.  A viewer that fails
# here has a geometry or colour-management problem, not a compositing one.
SCENES["a-solid-control"] = {
    "title": "对照：实色与仿射变换",
    "document": doc(64, 64, GREY, [
        layer("control", [
            shape("blue-box", rect(8, 8, 40, 56), solid(0.2, 0.4, 0.8)),
            shape("red-tilt", rect(36, 16, 56, 48), solid(0.9, 0.2, 0.1),
                  transform=rotation(20, 46, 32)),
        ]),
    ]),
}

# B. Does the backdrop participate in multiply/screen?  The document background
# is opaque.  A normal band is composited first; multiply then covers part of
# that band and part of the bare background; screen covers the bare background.
# A viewer that drops the backdrop, or blends in a different colour space, will
# disagree at the two "over background" probes but not at "base-only".
SCENES["b-backdrop-blend"] = {
    "title": "背景参与：multiply/screen 对文档底色与已合成图层",
    "document": doc(64, 64, GREY, [
        layer("base", [shape("base-band", rect(4, 4, 60, 28), solid(0.7, 0.55, 0.3))]),
        layer("mul", [shape("mul-band", rect(4, 16, 60, 44), solid(0.8, 0.8, 0.8))],
              blend="multiply"),
        layer("scr", [shape("scr-band", rect(4, 50, 60, 60), solid(0.2, 0.35, 0.6))],
              blend="screen"),
    ]),
}

# C. Group isolation and group opacity.  Three bands with the same geometry:
#
#   iso-same  A 0.5-opacity normal layer holding two overlapping *identical*
#             opaque rectangles.  Isolated flattening gives one 50 % wash, so
#             the overlap must be pixel-identical to the single-shape area.
#             Applying the opacity shape-by-shape gives 75 % in the overlap.
#             This test is independent of colour space and of blend mode.
#   iso-op    A 0.5-opacity normal layer holding two *semi-transparent*
#             different-coloured rectangles.  Flatten-then-scale is not the
#             same as scale-then-composite twice, and the difference survives
#             in the overlap only.
#   iso-mul   Multiply group holding two semi-transparent rectangles.  Its
#             absolute value also moves with the viewer's blend colour space,
#             so it is used for attribution, not as a hard threshold.
SCENES["c-group-isolation"] = {
    "title": "组隔离与组透明度：重叠区只能混合一次",
    "document": doc(64, 96, GREY, [
        layer("base", [shape("base-fill", rect(0, 0, 64, 96), solid(0.4, 0.5, 0.6))]),
        layer("iso-mul", [
            shape("mul-a", rect(4, 4, 40, 24), solid(0.6, 0.6, 0.6, 0.6)),
            shape("mul-b", rect(24, 12, 60, 32), solid(0.85, 0.85, 0.85, 0.6)),
        ], blend="multiply"),
        layer("iso-same", [
            shape("same-c", rect(4, 38, 40, 58), solid(0.9, 0.3, 0.2)),
            shape("same-d", rect(24, 46, 60, 66), solid(0.9, 0.3, 0.2)),
        ], opacity=0.5),
        layer("iso-op", [
            shape("op-c", rect(4, 72, 40, 92), solid(0.9, 0.3, 0.2, 0.5)),
            shape("op-d", rect(24, 80, 60, 92), solid(0.2, 0.6, 0.9, 0.5)),
        ], opacity=0.5),
    ]),
}

# D. Opaque gradient.  Prism interpolates in linear light: the midpoint between
# red and green is sRGB ~188 per channel, while a naive sRGB interpolation sits
# near 128.  The document background is transparent so that the captured pixel
# is the gradient itself and the page background cannot mask the difference.
SCENES["d-gradient-opaque"] = {
    "title": "不透明渐变插值：线性光 vs sRGB",
    "document": doc(64, 64, CLEAR, [
        layer("grad", [shape("grad-band", rect(4, 4, 60, 60), linear(
            4, 32, 60, 32,
            [(0.0, (1, 0, 0, 1)), (0.5, (0, 1, 0, 1)), (1.0, (0, 0, 1, 1))],
        ))]),
    ]),
}

# E. Transparent gradient.  Stop alpha runs 1 -> 0 while the colour runs
# magenta -> green.  Prism mixes premultiplied linear values, so the straight
# colour stays magenta and only alpha falls.  A viewer that interpolates colour
# and alpha independently drifts towards the arithmetic midpoint of the two
# colours, which is greenish grey.  The doc background is transparent, so the
# alpha ramp is visible in the captured alpha channel.
SCENES["e-gradient-alpha"] = {
    "title": "透明渐变插值：预乘透明度",
    "document": doc(64, 64, CLEAR, [
        layer("grad", [shape("alpha-band", rect(4, 4, 60, 60), linear(
            4, 32, 60, 32,
            [(0.0, (1, 0, 1, 1)), (1.0, (0, 1, 0, 0))],
        ))]),
    ]),
}

# F. Transparent document background.  Prism blends against a transparent
# backdrop, where multiply/screen collapse to the source.  A viewer that lets
# its own page backdrop into the blend gives results that change with the page
# colour.  The opaque base band makes the "backdrop is a composited layer"
# case available in the same picture.
SCENES["f-transparent-backdrop"] = {
    "title": "透明文档底：混合是否穿透到查看器底色",
    "document": doc(64, 64, CLEAR, [
        layer("base", [shape("base-band", rect(4, 4, 60, 28), solid(0.9, 0.6, 0.2))]),
        layer("mul", [shape("mul-band", rect(4, 16, 60, 44), solid(0.5, 0.5, 0.5))],
              blend="multiply"),
        layer("scr", [shape("scr-band", rect(4, 50, 60, 60), solid(0.4, 0.2, 0.8))],
              blend="screen"),
    ]),
}

# G. Knockout edges.  Three rings built three different ways: an evenodd fill
# hole, a binary clip hole (two contours, nonzero winding), and a binary mask
# hole.  The diamonds put 45-degree edges over the probe row, which is where a
# 2x2 coverage sampler and an analytic rasteriser are expected to disagree.
# Hole interiors and ring bodies are coverage-independent; only the rim scan is
# sensitive.
_RING_BODY = (0.85, 0.2, 0.15, 1)
_RING_CLIP = (0.2, 0.7, 0.3, 1)
_RING_MASK = (0.7, 0.3, 0.8, 1)
SCENES["g-knockout-edge"] = {
    "title": "镂空边缘：evenodd、二值裁域与二值蒙版",
    "document": doc(96, 64, (0.85, 0.85, 0.85, 1), [
        layer("shape-ring", [shape(
            "ring-fill",
            diamond(16, 32, 14, 18) + diamond(16, 32, 6, 8),
            solid(*_RING_BODY),
            fill_rule="evenodd",
        )]),
        layer("clip-ring", [shape("clip-fill", rect(36, 14, 60, 50), solid(*_RING_CLIP))],
              clip=rect(36, 14, 60, 50) + diamond(48, 32, 7, 9, reverse=True)),
        layer("mask-ring", [shape("mask-fill", diamond(80, 32, 14, 18), solid(*_RING_MASK))],
              mask=diamond(80, 32, 14, 18) + rect(76, 28, 84, 36, reverse=True)),
    ]),
}

PROBES = {
    "a-solid-control": [
        {"name": "bg", "x": 4, "y": 4, "question": "文档底色"},
        {"name": "blue", "x": 20, "y": 32, "question": "实色填充"},
        {"name": "red-tilt", "x": 46, "y": 32, "question": "旋转后的实色填充"},
        {"name": "bg-right", "x": 60, "y": 60, "question": "两个形状之外的底色"},
    ],
    "b-backdrop-blend": [
        {"name": "bg", "x": 2, "y": 2, "question": "文档底色"},
        {"name": "base-only", "x": 32, "y": 10, "question": "只有 normal 图层"},
        {"name": "base-mul-overlap", "x": 32, "y": 22, "question": "multiply 对上已合成的 normal 图层"},
        {"name": "mul-over-bg", "x": 32, "y": 38, "question": "multiply 直接对文档底色"},
        {"name": "screen-over-bg", "x": 32, "y": 55, "question": "screen 直接对文档底色"},
    ],
    "c-group-isolation": [
        {"name": "base", "x": 32, "y": 35, "question": "两个测试图层之间的底色"},
        {"name": "mul-overlap", "x": 32, "y": 18, "question": "两个 multiply 形状重叠处：组内混合一次，再对底混合一次"},
        {"name": "mul-a-only", "x": 10, "y": 8, "question": "靠下的 multiply 形状单独存在"},
        {"name": "mul-b-only", "x": 52, "y": 28, "question": "靠上的 multiply 形状单独存在"},
        {"name": "same-overlap", "x": 32, "y": 52, "question": "两个同色不透明形状重叠、组不透明度 0.5：必须等于单形状区"},
        {"name": "same-c-only", "x": 10, "y": 42, "question": "同色组中靠下的形状单独存在"},
        {"name": "same-d-only", "x": 52, "y": 64, "question": "同色组中靠上的形状单独存在"},
        {"name": "op-overlap", "x": 32, "y": 85, "question": "两个半透明形状重叠、组不透明度 0.5"},
        {"name": "op-c-only", "x": 10, "y": 76, "question": "半透明组中靠下的形状单独存在"},
        {"name": "op-d-only", "x": 52, "y": 90, "question": "半透明组中靠上的形状单独存在"},
    ],
    "d-gradient-opaque": [
        {"name": "t0", "x": 4, "y": 32, "question": "首个色标，红"},
        {"name": "t025", "x": 18, "y": 32, "question": "线性光中点约 188；sRGB 中点约 128"},
        {"name": "t05", "x": 32, "y": 32, "question": "中间色标，绿"},
        {"name": "t075", "x": 46, "y": 32, "question": "线性光绿/蓝中点约 188；sRGB 中点约 128"},
        {"name": "t1", "x": 60, "y": 32, "question": "末个色标，蓝"},
    ],
    "e-gradient-alpha": [
        {"name": "t0", "x": 4, "y": 32, "question": "不透明洋红"},
        {"name": "t025", "x": 18, "y": 32, "question": "预乘：洋红，alpha 约 0.75"},
        {"name": "t05", "x": 32, "y": 32, "question": "预乘：洋红 alpha 0.5；独立插值会变灰绿"},
        {"name": "t075", "x": 46, "y": 32, "question": "预乘：洋红 alpha 约 0.25"},
        {"name": "t1", "x": 60, "y": 32, "question": "透明端点"},
    ],
    "f-transparent-backdrop": [
        {"name": "base", "x": 32, "y": 10, "question": "不透明 normal 图层"},
        {"name": "base-mul", "x": 32, "y": 22, "question": "multiply 对上不透明图层"},
        {"name": "mul-over-transparent", "x": 32, "y": 38, "question": "multiply 对透明底，应等于源色"},
        {"name": "screen-over-transparent", "x": 32, "y": 55, "question": "screen 对透明底，应等于源色"},
        {"name": "transparent-bg", "x": 2, "y": 2, "question": "文档底色全透明"},
    ],
    "g-knockout-edge": [
        {"name": "fill-hole", "x": 16, "y": 32, "question": "evenodd 孔洞内部"},
        {"name": "clip-hole", "x": 48, "y": 32, "question": "裁域孔洞内部"},
        {"name": "mask-hole", "x": 80, "y": 32, "question": "蒙版孔洞内部"},
        {"name": "fill-body", "x": 16, "y": 20, "question": "evenodd 环体，与覆盖率无关"},
        {"name": "clip-body", "x": 48, "y": 16, "question": "裁域环体，与覆盖率无关"},
        {"name": "mask-body", "x": 80, "y": 20, "question": "蒙版环体，与覆盖率无关"},
    ],
}

# Horizontal scans that cross a 45-degree or vertical knockout edge.  y=30 is
# two pixels above the ring centre, so the fill and clip diamonds present a
# sloped edge to the scan while the mask hole presents a vertical one.
EDGE_SCANS = {
    "g-knockout-edge": [
        {"name": "fill-inner-left", "y": 30, "x0": 7, "x1": 15,
         "question": "evenodd 孔洞左侧 45 度边"},
        {"name": "clip-inner-left", "y": 30, "x0": 39, "x1": 47,
         "question": "裁域孔洞左侧 45 度边"},
        {"name": "mask-inner-left", "y": 30, "x0": 72, "x1": 80,
         "question": "蒙版孔洞左侧竖直边"},
    ]
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, scene in SCENES.items():
        payload = {"id": name, "title": scene["title"], "document": scene["document"]}
        (OUT / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    (OUT / "probes.json").write_text(
        json.dumps({"probes": PROBES, "edge_scans": EDGE_SCANS}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"scenes": sorted(SCENES), "out": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
