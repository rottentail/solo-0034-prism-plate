"""Scene fixtures for the offline sign-off provenance drill.

Every document is a plain prism-plate-v1 Document.  The build script sends these
through the real HTTP service; nothing here is a pre-rendered or hand-painted
image.  Revisions are reused across cases so that the same visual grammar keeps
the material readable while the forensic relations differ.
"""

# ---------------------------------------------------------------- primitives

def rect(x0, y0, x1, y1):
    return [
        {"kind": "M", "points": [{"x": x0, "y": y0}]},
        {"kind": "L", "points": [{"x": x1, "y": y0}]},
        {"kind": "L", "points": [{"x": x1, "y": y1}]},
        {"kind": "L", "points": [{"x": x0, "y": y1}]},
        {"kind": "Z"},
    ]


def solid(r, g, b, a=1.0):
    return {
        "kind": "solid",
        "color": {"r": r, "g": g, "b": b, "a": a},
        "from": {"x": 0, "y": 0},
        "to": {"x": 0, "y": 0},
    }


def linear(stops, start=(0, 0), end=(96, 72)):
    return {
        "kind": "linear",
        "color": {"r": 0, "g": 0, "b": 0, "a": 0},
        "from": {"x": start[0], "y": start[1]},
        "to": {"x": end[0], "y": end[1]},
        "stops": [{"at": at, "color": color} for at, color in stops],
    }


def shape(sid, path, brush, transform=None):
    return {
        "id": sid,
        "path": path,
        "fill_rule": "nonzero",
        "brush": brush,
        "transform": transform or [1, 0, 0, 1, 0, 0],
    }


def layer(lid, shapes, opacity=1.0, blend="normal", clip=None, mask=None):
    value = {"id": lid, "opacity": opacity, "blend": blend}
    if clip:
        value["clip"] = clip
    if mask:
        value["mask"] = mask
    value["shapes"] = shapes
    return value


def document(background, layers):
    return {
        "width": 96,
        "height": 72,
        "background": background,
        "layers": layers,
    }


def _ground(stops, end=(96, 72)):
    return layer("ground", [
        shape("ground-wash", rect(0, 0, 96, 72), linear(stops, end=end)),
    ])


# ---------------------------------------------------------------- case alpha
# A complete lineage with an accepted proof.  The signed sample is the service
# artifact itself, so byte identity, scene digest, artifact id and proof all line
# up.  Revision 2 changes a visible proof mark, which excludes revision 1.

ALPHA_BG = {"r": 0.045, "g": 0.06, "b": 0.085, "a": 1.0}


def alpha_r1():
    return document(ALPHA_BG, [
        _ground([(0.0, {"r": 0.12, "g": 0.24, "b": 0.32, "a": 1.0}),
                 (0.55, {"r": 0.20, "g": 0.38, "b": 0.42, "a": 1.0}),
                 (1.0, {"r": 0.08, "g": 0.14, "b": 0.22, "a": 1.0})]),
        layer("underlay", [
            shape("hidden-token", rect(10, 10, 26, 26), solid(0.94, 0.24, 0.12)),
        ]),
        layer("cover", [
            shape("cover-panel", rect(0, 0, 40, 40), solid(0.78, 0.80, 0.76)),
        ]),
        layer("marks", [
            shape("color-bar", rect(50, 10, 88, 26), solid(0.92, 0.58, 0.20)),
            shape("proof-mark", rect(60, 44, 72, 56), solid(0.18, 0.72, 0.46)),
            shape("register-dot", rect(46, 58, 48, 60), solid(0.72, 0.74, 0.80)),
        ]),
    ])


def alpha_r2():
    value = alpha_r1()
    value["layers"][3]["shapes"][1]["brush"] = solid(0.16, 0.58, 0.80)
    return value


# ---------------------------------------------------------------- case beta
# Four revisions.  R1, R3 and R4 are pixel-identical and byte-identical:
#   R3 changes the colour of a token fully hidden behind an opaque cover;
#   R4 changes the colour of a varnish layer whose alpha is exactly zero.
# R2 swaps two adjacent 1x1 tokens, so it differs at exactly two pixels but has
# the same 4x box thumbnail as R1.

BETA_BG = {"r": 0.055, "g": 0.05, "b": 0.075, "a": 1.0}
BETA_RED = (0.95, 0.25, 0.12, 1.0)
BETA_CYAN = (0.10, 0.80, 0.85, 1.0)


def beta_base():
    return document(BETA_BG, [
        _ground([(0.0, {"r": 0.16, "g": 0.20, "b": 0.34, "a": 1.0}),
                 (0.55, {"r": 0.24, "g": 0.30, "b": 0.44, "a": 1.0}),
                 (1.0, {"r": 0.09, "g": 0.12, "b": 0.24, "a": 1.0})]),
        layer("underlay", [
            shape("hidden-token", rect(12, 10, 30, 22), solid(*BETA_RED)),
        ]),
        layer("cover", [
            shape("cover-panel", rect(0, 0, 48, 30), solid(0.80, 0.82, 0.78)),
        ]),
        layer("marks", [
            shape("visible-strip", rect(52, 10, 88, 26), solid(0.92, 0.58, 0.20)),
            shape("token-left", rect(60, 40, 61, 41), solid(*BETA_RED)),
            shape("token-right", rect(61, 40, 62, 41), solid(*BETA_CYAN)),
        ]),
    ])


def beta_r1():
    return beta_base()


def beta_r2_swap():
    value = beta_base()
    value["layers"][3]["shapes"][1]["brush"] = solid(*BETA_CYAN)
    value["layers"][3]["shapes"][2]["brush"] = solid(*BETA_RED)
    return value


def beta_r3_hidden():
    value = beta_base()
    value["layers"][1]["shapes"][0]["brush"] = solid(0.12, 0.42, 0.92)
    return value


def beta_r4_varnish():
    value = beta_base()
    value["layers"].append(layer("varnish", [
        shape("varnish-film", rect(0, 0, 96, 72), solid(0.90, 0.30, 0.55, 0.0)),
    ]))
    return value


# ---------------------------------------------------------------- case gamma
# Visible change between R1 and R2, plus a labelled derived encoding of the R1
# artifact.  This is the "different encoding, same pixels" lesson.  R1 is fully
# bound by artifact metadata and an accepted proof; the label explains the byte
# digest mismatch rather than treating it as tampering.

GAMMA_BG = {"r": 0.035, "g": 0.055, "b": 0.065, "a": 1.0}


def gamma_r1():
    return document(GAMMA_BG, [
        _ground([(0.0, {"r": 0.10, "g": 0.28, "b": 0.26, "a": 1.0}),
                 (0.6, {"r": 0.16, "g": 0.36, "b": 0.32, "a": 1.0}),
                 (1.0, {"r": 0.07, "g": 0.16, "b": 0.18, "a": 1.0})],
                end=(96, 40)),
        layer("cover", [
            shape("cover-panel", rect(0, 0, 34, 28), solid(0.74, 0.76, 0.72)),
        ]),
        layer("underlay", [
            shape("hidden-token", rect(8, 8, 22, 20), solid(0.90, 0.20, 0.16)),
        ]),
        layer("marks", [
            shape("proof-mark", rect(46, 30, 82, 62), solid(0.22, 0.70, 0.42)),
            shape("edition-bar", rect(46, 10, 82, 18), solid(0.88, 0.62, 0.24)),
        ]),
    ])


def gamma_r2():
    value = gamma_r1()
    value["layers"][3]["shapes"][0]["brush"] = solid(0.88, 0.44, 0.18)
    return value


# ---------------------------------------------------------------- case delta
# Thumbnail trap.  R1, R2 and R3 are all present; R1 and R3 are pixel-identical
# to each other, R2 differs at exactly two pixels inside one 4x4 thumbnail cell.
# A 4x thumbnail therefore cannot separate R1 from R2, while the full-resolution
# diff can.  The signed sample is the R2 service artifact.

DELTA_BG = {"r": 0.05, "g": 0.045, "b": 0.07, "a": 1.0}
DELTA_AMBER = (0.94, 0.56, 0.18, 1.0)
DELTA_TEAL = (0.12, 0.74, 0.70, 1.0)


def delta_base():
    return document(DELTA_BG, [
        _ground([(0.0, {"r": 0.20, "g": 0.18, "b": 0.32, "a": 1.0}),
                 (0.5, {"r": 0.30, "g": 0.26, "b": 0.40, "a": 1.0}),
                 (1.0, {"r": 0.10, "g": 0.10, "b": 0.20, "a": 1.0})]),
        layer("underlay", [
            shape("hidden-token", rect(14, 12, 28, 24), solid(0.92, 0.18, 0.14)),
        ]),
        layer("cover", [
            shape("cover-panel", rect(0, 0, 46, 30), solid(0.82, 0.84, 0.80)),
        ]),
        layer("marks", [
            shape("color-bar", rect(52, 10, 88, 26), solid(0.90, 0.60, 0.22)),
            shape("token-left", rect(60, 40, 61, 41), solid(*DELTA_AMBER)),
            shape("token-right", rect(61, 40, 62, 41), solid(*DELTA_TEAL)),
            shape("arrow", [
                {"kind": "M", "points": [{"x": 68, "y": 50}]},
                {"kind": "L", "points": [{"x": 80, "y": 62}]},
                {"kind": "L", "points": [{"x": 58, "y": 62}]},
                {"kind": "Z"},
            ], solid(0.32, 0.72, 0.66)),
        ]),
    ])


def delta_r1():
    return delta_base()


def delta_r2_swap():
    value = delta_base()
    value["layers"][3]["shapes"][1]["brush"] = solid(*DELTA_TEAL)
    value["layers"][3]["shapes"][2]["brush"] = solid(*DELTA_AMBER)
    return value


def delta_r3_hidden():
    value = delta_base()
    value["layers"][1]["shapes"][0]["brush"] = solid(0.12, 0.40, 0.90)
    return value


# ---------------------------------------------------------------- case epsilon
# Deliberately insufficient package: a full-resolution signed sample, one source
# document (R1), one R1 vector proof and an R1 render response, but no R2
# document, no R2 artifact metadata and no proof.  R1 can be excluded by a full
# resolution diff, yet no justified candidate remains.

EPSILON_BG = {"r": 0.04, "g": 0.05, "b": 0.06, "a": 1.0}


def epsilon_r1():
    return document(EPSILON_BG, [
        _ground([(0.0, {"r": 0.14, "g": 0.22, "b": 0.30, "a": 1.0}),
                 (0.6, {"r": 0.22, "g": 0.32, "b": 0.38, "a": 1.0}),
                 (1.0, {"r": 0.08, "g": 0.14, "b": 0.20, "a": 1.0})]),
        layer("cover", [
            shape("cover-panel", rect(0, 0, 30, 26), solid(0.78, 0.80, 0.76)),
        ]),
        layer("underlay", [
            shape("hidden-token", rect(8, 8, 20, 18), solid(0.90, 0.22, 0.14)),
        ]),
        layer("marks", [
            shape("proof-mark", rect(44, 28, 84, 64), solid(0.20, 0.68, 0.44)),
            shape("edition-bar", rect(44, 10, 84, 18), solid(0.90, 0.60, 0.22)),
        ]),
    ])


def epsilon_r2():
    value = epsilon_r1()
    value["layers"][3]["shapes"][0]["brush"] = solid(0.86, 0.40, 0.20)
    return value


def epsilon_lookalike():
    """A different plate that looks close to the received sample.

    It is rendered by the real service, but it is not the received sample and
    carries no provenance link to it.  The drill uses it to show that the most
    similar re-rendered image is a lead, not proof of source.
    """
    value = epsilon_r1()
    value["layers"][3]["shapes"][0]["brush"] = solid(0.82, 0.34, 0.20)
    value["layers"][1]["shapes"][0]["brush"] = solid(0.76, 0.78, 0.74)
    return value


# ---------------------------------------------------------------- case table

CASES = [
    {
        "key": "case-a",
        "plate": "press-alpha",
        "title": "签样来源演练甲",
        "revisions": [
            ("r1", alpha_r1, "初始稿：可见校样标记为绿色。"),
            ("r2", alpha_r2, "修订稿：可见校样标记改为蓝色。"),
        ],
        "renders": [("png-r1", 1, "png"), ("png-r2", 2, "png"),
                    ("svg-r1", 1, "svg"), ("svg-r2", 2, "svg")],
        "proof": {"id": "proof-alpha-r2", "revision": 2, "format": "png",
                  "state": "accepted", "note": "校样标记与色条核对通过"},
        "signed": {"artifact": "png-r2", "name": "signed-sample.png",
                   "derived": None},
        "kit_docs": [1, 2],
        "kit_artifacts": ["png-r1", "png-r2"],
        "kit_svg": [2],
        "kit_responses": ["render-png-r1", "render-png-r2",
                          "get-artifact-png-r1", "get-artifact-png-r2",
                          "proof-open", "proof-decide", "bundle"],
        "kit_bundle": True,
        "kit_thumbnail_of": None,
        "kit_note": "该案例提供完整版面谱系封包、受签成品原始字节、成品元数据与已接受校样。",
        "expected": {
            "verdict": "provenance_established",
            "source": "press-alpha revision 2",
            "candidates": ["press-alpha revision 2"],
            "excluded": [
                {"candidate": "press-alpha revision 1",
                 "reason": "可见校样标记颜色不同，全分辨率像素差不为零。"},
            ],
            "ambiguity": [
                "服务没有身份认证，摘要链只能证明包内谱系一致，不能证明保管链或排除整包伪造。",
            ],
            "missing_evidence": [],
        },
    },
    {
        "key": "case-b",
        "plate": "press-beta",
        "title": "签样来源演练乙",
        "revisions": [
            ("r1", beta_r1, "初始稿。"),
            ("r2", beta_r2_swap, "两块相邻 1x1 色标互换。"),
            ("r3", beta_r3_hidden, "被不透明遮片完全覆盖的隐藏色标改为蓝色。"),
            ("r4", beta_r4_varnish, "新增一层 alpha=0 的透明光油，颜色任意。"),
        ],
        "renders": [("png-r1", 1, "png"), ("png-r2", 2, "png"),
                    ("png-r3", 3, "png"), ("png-r4", 4, "png"),
                    ("svg-r1", 1, "svg")],
        "proof": None,
        "signed": {"artifact": "png-r1", "name": "signed-sample.png",
                   "derived": {"kind": "rgba8", "source_artifact": "png-r1",
                               "source_name": "reference-original.png",
                               "label": "同像素 RGBA8 派生副本"}},
        "kit_docs": [1, 3],
        "kit_artifacts": ["png-r1", "png-r3"],
        "kit_svg": [],
        "kit_responses": ["render-png-r1", "render-png-r3",
                          "get-artifact-png-r1", "get-artifact-png-r3"],
        "kit_bundle": False,
        "kit_thumbnail_of": None,
        "kit_note": ("节选：仅含 R1 与 R3 源稿及对应渲染响应；R2、R4 未纳入本包。"
                     "未纳入不等于被删除或篡改。受签样本的成品元数据不在包内，"
                     "包内只有它的字节摘要。"),
        "expected": {
            "verdict": "candidates_only",
            "source": None,
            "candidates": ["press-beta revision 1", "press-beta revision 3"],
            "excluded": [],
            "ground_truth_notes": [
                "R2 与受签样本相差 2 像素，但 R2 未纳入接收包；接收者不能据此排除它。",
                "R4 与受签样本像素与字节完全相同，且未纳入接收包；接收者不能排除它。",
            ],
            "ambiguity": [
                "R1 与 R3 的全部像素与成品字节完全相同，像素证据无法区分。",
                "样本字节同时等于 R1 与 R3 的成品字节；派生记录只自述源自 R1，"
                "且不带签名，不能把两者区分开。",
                "R2、R4 未纳入接收包，不能在接收包内排除；接收者必须把它们列为不可排除项。",
            ],
            "missing_evidence": [
                "能把接收样本绑定到具体成品的校样裁决或带认证的交接记录。",
                "R2、R4 的源稿与成品元数据，用于关闭候选集。",
            ],
        },
    },
    {
        "key": "case-c",
        "plate": "press-gamma",
        "title": "签样来源演练丙",
        "revisions": [
            ("r1", gamma_r1, "初始稿：可见校样标记为绿色。"),
            ("r2", gamma_r2, "修订稿：可见校样标记改为橙色。"),
        ],
        "renders": [("png-r1", 1, "png"), ("png-r2", 2, "png"),
                    ("svg-r1", 1, "svg")],
        "proof": {"id": "proof-gamma-r1", "revision": 1, "format": "png",
                  "state": "accepted", "note": "归档副本与受签成品逐像素一致"},
        "signed": {"artifact": "png-r1", "name": "signed-sample.png",
                   "derived": {"kind": "rgba8", "source_artifact": "png-r1",
                               "source_name": "reference-original.png",
                               "label": "归档用 RGBA8 重编码副本"}},
        "kit_docs": [1, 2],
        "kit_artifacts": ["png-r1", "png-r2"],
        "kit_svg": [1],
        "kit_responses": ["render-png-r1", "get-artifact-png-r1",
                          "proof-open", "proof-decide"],
        "kit_bundle": False,
        "kit_thumbnail_of": "signed",
        "kit_note": ("受签样本是带派生记录的 RGBA8 重编码副本；"
                     "同目录提供原始 PNG 成品、元数据与已接受校样。"),
        "expected": {
            "verdict": "provenance_established",
            "source": "press-gamma revision 1",
            "candidates": ["press-gamma revision 1"],
            "excluded": [
                {"candidate": "press-gamma revision 2",
                 "reason": "可见校样标记颜色不同，全分辨率像素差不为零。"},
            ],
            "ambiguity": [
                "派生记录是自述字段，不带密码学签名；它解释编码差，但不能单独证明保管链。",
            ],
            "missing_evidence": [],
        },
    },
    {
        "key": "case-d",
        "plate": "press-delta",
        "title": "签样来源演练丁",
        "revisions": [
            ("r1", delta_r1, "初始稿。"),
            ("r2", delta_r2_swap, "两块相邻 1x1 色标互换，形成本题的受签成品。"),
            ("r3", delta_r3_hidden, "被遮片覆盖的隐藏色标改为蓝色；像素与 R1 相同。"),
        ],
        "renders": [("png-r1", 1, "png"), ("png-r2", 2, "png"),
                    ("png-r3", 3, "png")],
        "proof": {"id": "proof-delta-r2", "revision": 2, "format": "png",
                  "state": "accepted", "note": "局部色标换位后复核通过"},
        "signed": {"artifact": "png-r2", "name": "signed-sample.png",
                   "derived": None},
        "kit_docs": [1, 2, 3],
        "kit_artifacts": ["png-r1", "png-r2", "png-r3"],
        "kit_svg": [],
        "kit_responses": ["render-png-r1", "render-png-r2", "render-png-r3",
                          "get-artifact-png-r1", "get-artifact-png-r2",
                          "get-artifact-png-r3", "proof-open", "proof-decide"],
        "kit_bundle": False,
        "kit_thumbnail_of": "signed+r1",
        "extra_plates": [
            {
                "id": "press-delta-unfiled",
                "title": "未纳入包的另一版面（像素同 R2）",
                "document": delta_r2_swap,
                "render_name": "unfiled-png",
                "kit_file": "unfiled/other-plate-same-pixels.png",
            },
        ],
        "kit_note": ("随包提供 R1、R2、R3 源稿与受签成品的原始字节。"
                     "另附 4x 缩略图：受签样本与 R1 的缩略图完全相同。"
                     "另有一张外部重渲染图：它来自另一版面，源稿与谱系均不在本包内，"
                     "仅作为对照材料。"),
        "expected": {
            "verdict": "provenance_established",
            "source": "press-delta revision 2",
            "candidates": ["press-delta revision 2"],
            "excluded": [
                {"candidate": "press-delta revision 1",
                 "reason": "缩略图相同，但全分辨率在 (60,40)、(61,40) 两像素不同。"},
                {"candidate": "press-delta revision 3",
                 "reason": "与 R1 像素相同，因此同样被上述两像素差排除。"},
            ],
            "ambiguity": [
                "缩略图不是身份证据；若只给缩略图，R1 与受签样本不可区分。",
                "包外另有一张像素相同的重渲染图；没有谱系或校样把它与受签样本绑定。",
            ],
            "missing_evidence": [],
        },
    },
    {
        "key": "case-e",
        "plate": "press-epsilon",
        "title": "签样来源演练戊",
        "revisions": [
            ("r1", epsilon_r1, "初始稿：可见校样标记为绿色。"),
            ("r2", epsilon_r2, "修订稿：可见校样标记改为橙色，本包未提供该源稿。"),
        ],
        "renders": [("png-r1", 1, "png"), ("png-r2", 2, "png"),
                    ("svg-r1", 1, "svg")],
        "proof": None,
        "signed": {"artifact": "png-r2", "name": "signed-sample.png",
                   "derived": None},
        "kit_docs": [1],
        "kit_artifacts": ["png-r1"],
        "kit_svg": [1],
        "kit_responses": ["render-png-r1", "get-artifact-png-r1"],
        "kit_bundle": False,
        "kit_thumbnail_of": "signed",
        "extra_plates": [
            {
                "id": "press-epsilon-lookalike",
                "title": "近似重渲染（非受签版面）",
                "document": epsilon_lookalike,
                "render_name": "lookalike-png",
                "kit_file": "lookalike/rebased-approximation.png",
            },
        ],
        "kit_note": ("节选：只有 R1 源稿、R1 矢量校样和 R1 渲染响应；"
                     "受签样本的源稿、成品元数据与校样均不在包内。"
                     "附一张由另一版面重渲染的近似图，仅供筛查。"
                     "资料缺失不是篡改证据。"),
        "expected": {
            "verdict": "indeterminate",
            "source": None,
            "candidates": [],
            "excluded": [
                {"candidate": "press-epsilon revision 1",
                 "reason": "全分辨率像素差不为零，可以排除 R1。"},
            ],
            "ambiguity": [
                "R1 被排除后，包内没有可验证的候选；SVG 只是矢量校样，不能证明像素来源。",
                "缩略图只能用于筛查，不能用来认定来源。",
            ],
            "missing_evidence": [
                "受签样本对应的源稿或版面封包。",
                "成品元数据（artifact id、scene_digest、字节摘要）或校样裁决。",
                "若样本经过重编码，需要可核对的派生记录与原始成品。",
            ],
        },
    },
]
