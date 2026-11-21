"""Candidate recipe families and observation backgrounds (final solved values).

A "recipe" is the hidden triple the receiver tries to infer:
  (brush sRGB color, brush alpha, layer opacity, blend mode).
The effective source coverage is o = brush_alpha * layer opacity; brush alpha
and layer opacity only enter the premultiplied pipeline through that product,
which is the root of the permanent ambiguity demonstrated by B2 vs B4.

All numeric values are valid prismplate scene inputs (unit interval, blends in
the admitted set, geometry a closed 3+ vertex contour with an identity
transform). No new renderer capability or solving endpoint is used anywhere.

Equifinality targets on the mid-gray background (0.5 sRGB, linear d):
  Family A  normal    -> RGB codes (115, 153, 189)
  Family B  multiply  -> gray code 96
  Family C  screen    -> gray code 186
  Family EM normal vs multiply -> RGB codes (71, 87, 107)
  Family ES normal vs screen   -> RGB codes (158, 179, 204)
"""

# Observation backgrounds. Opaque ones span values where the three blend modes
# diverge. bg_alpha < 1 is the translucent-alpha probe: the output alpha code
# then exposes effective coverage o directly.
BACKGROUNDS = [
    ("bg-midgray",   (0.50, 0.50, 0.50), 1.00),
    ("bg-black",     (0.00, 0.00, 0.00), 1.00),
    ("bg-white",     (1.00, 1.00, 1.00), 1.00),
    ("bg-orange",    (0.95, 0.45, 0.10), 1.00),
    ("bg-darkgray",  (0.22, 0.22, 0.22), 1.00),
    ("bg-lightgray", (0.80, 0.80, 0.80), 1.00),
    ("bg-red",       (0.80, 0.10, 0.10), 1.00),
    ("bg-alpha50",   (0.50, 0.50, 0.50), 0.50),
]

# Interior sampling region: the probe rectangle covers [4,92] on a 96px canvas,
# so points inside [12,84]^2 are fully covered in all four supersamples and
# carry no geometry/anti-aliasing confound.
SAMPLE_BOX = (12, 12, 84, 84)
CANVAS = 96


def _pt(x, y):
    return {"x": x, "y": y}


def _rect_path():
    return [
        {"kind": "M", "points": [_pt(4, 4)]},
        {"kind": "L", "points": [_pt(92, 4)]},
        {"kind": "L", "points": [_pt(92, 92)]},
        {"kind": "L", "points": [_pt(4, 92)]},
        {"kind": "Z"},
    ]


def solid_rect_doc(plate_id, title, rgb, brush_a, opacity, blend, background, bg_alpha):
    """Build a valid prismplate document: one solid full-coverage rectangle."""
    br, bg, bb = background
    return {
        "id": plate_id,
        "title": title,
        "document": {
            "width": CANVAS,
            "height": CANVAS,
            "background": {"r": br, "g": bg, "b": bb, "a": bg_alpha},
            "layers": [
                {
                    "id": "probe-layer",
                    "opacity": opacity,
                    "blend": blend,
                    "shapes": [
                        {
                            "id": "probe-fill",
                            "fill_rule": "nonzero",
                            "path": _rect_path(),
                            "transform": [1, 0, 0, 1, 0, 0],
                            "brush": {
                                "kind": "solid",
                                "color": {"r": rgb[0], "g": rgb[1], "b": rgb[2],
                                          "a": brush_a},
                            },
                        }
                    ],
                }
            ],
        },
    }


def blank_layer_doc(plate_id, title, mode, background, bg_alpha):
    """Recipes that paint nothing: zero-opacity layer vs. off-canvas shape."""
    br, bg, bb = background
    if mode == "zero-opacity":
        shape_path = _rect_path()
        opacity, color = 0.0, (0.7, 0.2, 0.9)
    else:  # off-canvas contour: admitted geometry, never covers a sample point
        shape_path = [
            {"kind": "M", "points": [_pt(200, 200)]},
            {"kind": "L", "points": [_pt(240, 200)]},
            {"kind": "L", "points": [_pt(220, 230)]},
            {"kind": "Z"},
        ]
        opacity, color = 1.0, (0.1, 0.8, 0.3)
    return {
        "id": plate_id,
        "title": title,
        "document": {
            "width": CANVAS,
            "height": CANVAS,
            "background": {"r": br, "g": bg, "b": bb, "a": bg_alpha},
            "layers": [
                {
                    "id": "probe-layer",
                    "opacity": opacity,
                    "blend": "normal",
                    "shapes": [
                        {
                            "id": "probe-fill",
                            "fill_rule": "nonzero",
                            "path": shape_path,
                            "transform": [1, 0, 0, 1, 0, 0],
                            "brush": {"kind": "solid",
                                      "color": {"r": color[0], "g": color[1],
                                                "b": color[2], "a": 1.0}},
                        }
                    ],
                }
            ],
        },
    }


# Family A: normal blend. All three share codes on mid-gray; only o*s and o
# trade against each other at one background, so extra backgrounds separate.
FAMILY_A = [
    ("A1-opaque",    dict(rgb=(0.4500, 0.6000, 0.7400), brush_a=1.00, opacity=1.00, blend="normal")),
    ("A2-glaze70",   dict(rgb=(0.4263, 0.6369, 0.8168), brush_a=1.00, opacity=0.70, blend="normal")),
    ("A3-glaze50",   dict(rgb=(0.3919, 0.6822, 0.9064), brush_a=1.00, opacity=0.50, blend="normal")),
]

# Family B: multiply. Constant k = o*(1-s) means identical output on EVERY
# opaque background, including colored ones. B2 and B4 additionally share o, so
# they remain identical on the alpha probe as well: permanent ambiguity.
FAMILY_B = [
    ("B1-mult-full",  dict(rgb=(0.7674,) * 3, brush_a=1.00, opacity=1.00, blend="multiply")),
    ("B2-mult-glaze", dict(rgb=(0.6652,) * 3, brush_a=1.00, opacity=0.75, blend="multiply")),
    ("B3-mult-thin",  dict(rgb=(0.4635,) * 3, brush_a=1.00, opacity=0.55, blend="multiply")),
    ("B4-brush-alpha",dict(rgb=(0.6652,) * 3, brush_a=0.75, opacity=1.00, blend="multiply")),
]

# Family C: screen. Constant k = o*s; identical on every opaque background,
# separated by the translucent-alpha probe through output coverage.
FAMILY_C = [
    ("C1-screen-full",  dict(rgb=(0.6262,) * 3, brush_a=1.00, opacity=1.00, blend="screen")),
    ("C2-screen-glaze", dict(rgb=(0.7354,) * 3, brush_a=1.00, opacity=0.70, blend="screen")),
    ("C3-screen-thin",  dict(rgb=(0.8543,) * 3, brush_a=1.00, opacity=0.50, blend="screen")),
]

# Family EM: an opaque normal color and a full-strength multiply tuned to the
# same mid-gray result. Multiply on black is black for both; white separates.
FAMILY_EM = [
    ("EM1-normal",   dict(rgb=(0.2800, 0.3400, 0.4200), brush_a=1.00, opacity=1.00, blend="normal")),
    ("EM2-multiply", dict(rgb=(0.5818, 0.6959, 0.8479), brush_a=1.00, opacity=1.00, blend="multiply")),
]

# Family ES: an opaque normal color and a full-strength screen tuned to the
# same mid-gray result. Screen on white is white for both; black separates.
FAMILY_ES = [
    ("ES1-normal", dict(rgb=(0.6200, 0.7000, 0.8000), brush_a=1.00, opacity=1.00, blend="normal")),
    ("ES2-screen", dict(rgb=(0.4408, 0.5818, 0.7327), brush_a=1.00, opacity=1.00, blend="screen")),
]

# Family D: nothing reaches the canvas. Every probe sees the bare background.
FAMILY_D = [
    ("D1-zero-opacity", "zero-opacity"),
    ("D2-off-canvas",   "off-canvas"),
]

FAMILIES = {
    "A": FAMILY_A,
    "B": FAMILY_B,
    "C": FAMILY_C,
    "EM": FAMILY_EM,
    "ES": FAMILY_ES,
}
