#!/usr/bin/env python3
"""棱版 · 2x2 可重排展板交付生成器。

从同一批源砖（4 块，两类）生成：
  * examples/rehang/tilespec.json   源砖定义（源稿）
  * examples/rehang/layouts.json    摆法定义（原展 / 换展 / 三套演练）
  * out/rehang/scenes/*.json        prism-plate-v1 稿本（可直接 POST 给棱版服务）
  * out/rehang/tiles|boards|zooms/*.png|svg   像素校样与矢量校样
  * out/rehang/report.json          核验报告（含演练判定与恢复建议）
  * out/rehang/placement.md         摆放顺序施工单

无 Go 环境下用 scripts/prismplate_local.py（同契约离线校样器）出图；有 Go 环境时
可将 scenes/*.json 原样提交棱版服务复现并交叉核对。

用法：python3 scripts/rehang.py
"""
import copy
import hashlib
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prismplate_local as ppl

T = 256.0                      # 砖边长（局部坐标）
THIRD, TWO3, HALF = T / 3, 2 * T / 3, T / 2
BOARD = 2 * T                  # 整版世界边长
SLOTS = {"NW": (0.0, 0.0), "NE": (T, 0.0), "SW": (0.0, T), "SE": (T, T)}
SLOT_ORDER = ["NW", "NE", "SW", "SE"]
SEAMS = [  # (缝号, 槽位A, A侧, 槽位B, B侧, 中点)
    ("s1", "NW", "E", "NE", "W", (T, T / 2)),
    ("s2", "SW", "E", "SE", "W", (T, 3 * T / 2)),
    ("s3", "NW", "S", "SW", "N", (T / 2, T)),
    ("s4", "NE", "S", "SE", "N", (3 * T / 2, T)),
]
JUNCTION = (T, T)
JUNCTION_CORNER = {"NW": "SE", "NE": "SW", "SW": "NE", "SE": "NW"}

# 旋转（顺时针，屏幕坐标 y 向下）：局部边 → 摆后边
EDGE_ROT = {
    0:   {"N": "N", "E": "E", "S": "S", "W": "W"},
    90:  {"N": "E", "E": "S", "S": "W", "W": "N"},
    180: {"N": "S", "E": "W", "S": "N", "W": "E"},
    270: {"N": "W", "E": "N", "S": "E", "W": "S"},
}
CORNER_ROT = {
    0:   {"NE": "NE", "SE": "SE", "SW": "SW", "NW": "NW"},
    90:  {"NE": "SE", "SE": "SW", "SW": "NW", "NW": "NE"},
    180: {"NE": "SW", "SE": "NW", "SW": "NE", "NW": "SE"},
    270: {"NE": "NW", "SE": "NE", "SW": "SE", "NW": "SW"},
}
ROT_NAME = {0: "0°(北)", 90: "90°(东)", 180: "180°(南)", 270: "270°(西)"}

# ---------------------------------------------------------------- 色彩（sRGB 0..1）


def C(hexs, a=1.0):
    h = hexs.lstrip("#")
    return {"r": int(h[0:2], 16) / 255, "g": int(h[2:4], 16) / 255,
            "b": int(h[4:6], 16) / 255, "a": a}


PAPER = C("#f6f2e9")
TINT = {"loop": C("#fbf8f0"), "flow": C("#edf3f6")}
INDIGO = "#2e2a6e"
INK = {"N": C("#00a6c0", 0.32), "E": C("#f29e0e", 0.32),
       "S": C("#d63a8c", 0.32), "W": C("#0e9a70", 0.32)}
INK_HEX = {"N": "#00a6c0", "E": "#f29e0e", "S": "#d63a8c", "W": "#0e9a70"}
WEDGE_HEX = {"NE": "#e4572e", "SE": "#7a5fd0", "SW": "#3a7bd5", "NW": "#e4b62e"}
MARKER_HEX = {"twin": "#a05a08", "solo": "#0b6e8c"}
TILE_MEDAL_HEX = {"t1": "#b03a2e", "t2": "#6a4fd0", "t3": "#0b6e8c", "t4": "#c07a10"}
SEAM_HEX = {"s1": "#e4572e", "s2": "#f29e0e", "s3": "#0e9a70", "s4": "#7a5fd0"}

# ---------------------------------------------------------------- 矩阵（2x3 仿射）


def mat_mul(m2, m1):  # 先施加 m1
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return [a2 * a1 + c2 * b1, b2 * a1 + d2 * b1,
            a2 * c1 + c2 * d1, b2 * c1 + d2 * d1,
            a2 * e1 + c2 * f1 + e2, b2 * e1 + d2 * f1 + f2]


def m_trans(dx, dy):
    return [1, 0, 0, 1, dx, dy]


def m_scale(s):
    return [s, 0, 0, s, 0, 0]


def m_rot(deg):
    r = math.radians(deg)
    co, si = round(math.cos(r), 12), round(math.sin(r), 12)
    return [co, si, -si, co, 0, 0]


def m_apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def m_vec(m, x, y):  # 仅线性部分（方向向量）
    return (m[0] * x + m[2] * y, m[1] * x + m[3] * y)


def world_matrix(slot, rot):
    ox, oy = SLOTS[slot]
    return mat_mul(m_trans(ox, oy),
                   mat_mul(m_trans(HALF, HALF),
                           mat_mul(m_rot(rot), m_trans(-HALF, -HALF))))


def canvas_matrix(slot, rot, scale, origin=(0.0, 0.0)):
    return mat_mul(m_scale(scale),
                   mat_mul(m_trans(-origin[0], -origin[1]),
                           world_matrix(slot, rot)))

# ---------------------------------------------------------------- 源稿：砖型与摆法


def edge_point(edge, d):
    """d 为沿边长度坐标（0..T）。"""
    return {"N": (d, 0.0), "E": (T, d), "S": (d, T), "W": (0.0, d)}[edge]


def edge_frame(edge):
    """该边的内法向 n 与沿边单位向量 u。"""
    return {"N": ((0.0, 1.0), (1.0, 0.0)), "E": ((-1.0, 0.0), (0.0, 1.0)),
            "S": ((0.0, -1.0), (1.0, 0.0)), "W": ((1.0, 0.0), (0.0, 1.0))}[edge]


def norm(v):
    n = math.hypot(v[0], v[1])
    return (v[0] / n, v[1] / n)


def petal_curve(name, p0, c, p1):
    # 两端切向一律取“指向砖内”的方向：跨缝两侧内指切向应互为反向（dot=-1）
    return {"kind": "petal", "name": name, "p0": list(p0), "c": list(c), "p1": list(p1),
            "ends": [{"at": list(p0), "tan": list(norm((c[0] - p0[0], c[1] - p0[1])))},
                     {"at": list(p1), "tan": list(norm((c[0] - p1[0], c[1] - p1[1])))}]}


def bar_curve(name, y):
    return {"kind": "bar", "name": name, "rect": [0.0, y - 7, T, y + 7],
            "ends": [{"at": [0.0, y], "tan": [1.0, 0.0]},
                     {"at": [T, y], "tan": [-1.0, 0.0]}]}


def build_spec():
    port_rule = {"twin": [THIRD, TWO3], "solo": [HALF]}
    loop_curves = [
        petal_curve("nw", (THIRD, 0), (THIRD, THIRD), (0, THIRD)),
        petal_curve("ne", (TWO3, 0), (TWO3, THIRD), (T, THIRD)),
        petal_curve("se", (T, TWO3), (TWO3, TWO3), (TWO3, T)),
        petal_curve("sw", (THIRD, T), (THIRD, TWO3), (0, TWO3)),
    ]
    flow_curves = [
        petal_curve("e1", (HALF, 0), (HALF, TWO3), (T, TWO3)),
        petal_curve("w2", (HALF, T), (HALF, TWO3), (0, TWO3)),
        bar_curve("bar", THIRD),
    ]
    types = {
        "loop": {"label": "甲型·回环", "medal_r": 22,
                 "edges": {e: {"marker": "twin", "ink": INK_HEX[e]} for e in "NESW"},
                 "corners": dict(WEDGE_HEX), "curves": loop_curves},
        "flow": {"label": "乙型·川流", "medal_r": 18,
                 "edges": {"N": {"marker": "solo", "ink": INK_HEX["N"]},
                            "E": {"marker": "twin", "ink": INK_HEX["E"]},
                            "S": {"marker": "solo", "ink": INK_HEX["S"]},
                            "W": {"marker": "twin", "ink": INK_HEX["W"]}},
                 "corners": dict(WEDGE_HEX), "curves": flow_curves},
    }
    tiles = {tid: {"type": tp, "medal": TILE_MEDAL_HEX[tid]}
             for tid, tp in (("t1", "loop"), ("t2", "loop"), ("t3", "flow"), ("t4", "flow"))}
    return {"tile_side": T, "port_rule": port_rule,
            "markers": {"twin": {"glyph": "双三角(铜)", "color": MARKER_HEX["twin"]},
                        "solo": {"glyph": "单菱(钢蓝)", "color": MARKER_HEX["solo"]}},
            "types": types, "tiles": tiles}


def build_layouts():
    return {"l1": {"label": "原展", "kind": "sanctioned", "base": None,
                   "slots": {"NW": ["t1", 0], "NE": ["t3", 0], "SW": ["t2", 0], "SE": ["t4", 0]}},
            "l2": {"label": "换展", "kind": "sanctioned", "base": None,
                   "slots": {"NW": ["t2", 0], "NE": ["t1", 0], "SW": ["t3", 90], "SE": ["t4", 270]}},
            "fault": {"label": "演练·乙型砖装反(旋转180°)", "kind": "drill", "base": "l2",
                      "slots": {"NW": ["t2", 0], "NE": ["t1", 0], "SW": ["t3", 90], "SE": ["t4", 90]}},
            "fixed": {"label": "演练·单砖旋转恢复", "kind": "drill", "base": "l2",
                      "slots": {"NW": ["t2", 0], "NE": ["t1", 0], "SW": ["t3", 90], "SE": ["t4", 270]}},
            "swap": {"label": "演练·同型砖对调", "kind": "drill", "base": "l2",
                     "slots": {"NW": ["t2", 0], "NE": ["t1", 0], "SW": ["t4", 270], "SE": ["t3", 90]}}}

# ---------------------------------------------------------------- 形状生成（局部坐标）


def ops_poly(points):
    ops = [{"kind": "M", "points": [{"x": p[0], "y": p[1]} for p in points[:1]]}]
    ops += [{"kind": "L", "points": [{"x": p[0], "y": p[1]}]} for p in points[1:]]
    ops.append({"kind": "Z"})
    return ops


def ops_petal(p0, c, p1):
    return [{"kind": "M", "points": [{"x": p0[0], "y": p0[1]}]},
            {"kind": "Q", "points": [{"x": c[0], "y": c[1]}, {"x": p1[0], "y": p1[1]}]},
            {"kind": "L", "points": [{"x": p0[0], "y": p0[1]}]}, {"kind": "Z"}]


def shape(sid, ops, color):
    return {"id": sid, "fill_rule": "nonzero", "path": ops,
            "brush": {"kind": "solid", "color": color},
            "transform": [1, 0, 0, 1, 0, 0]}


def tile_shapes(spec, tid):
    tp = spec["tiles"][tid]["type"]
    ts = spec["types"][tp]
    shapes = [shape(f"{tid}-base", ops_poly([(0, 0), (T, 0), (T, T), (0, T)]), TINT[tp])]
    # 端口光晕（半透明，跨缝拼成完整菱斑）
    for edge, ed in ts["edges"].items():
        n, u = edge_frame(edge)
        for i, f in enumerate(spec["port_rule"][ed["marker"]]):
            px, py = edge_point(edge, f)
            tri = [(px + 40 * n[0], py + 40 * n[1]),
                   (px + 28 * u[0], py + 28 * u[1]),
                   (px - 28 * u[0], py - 28 * u[1])]
            shapes.append(shape(f"{tid}-halo-{edge.lower()}{i}", ops_poly(tri), INK[edge]))
    # 角部楔形色块（四砖交点拼合的风车）
    for corner, hexs in ts["corners"].items():
        cx, cy = {"NE": (T, 0), "SE": (T, T), "SW": (0, T), "NW": (0, 0)}[corner]
        e1 = {"NE": (-56, 0), "SE": (-56, 0), "SW": (56, 0), "NW": (56, 0)}[corner]
        e2 = {"NE": (0, 56), "SE": (0, -56), "SW": (0, -56), "NW": (0, 56)}[corner]
        tri = [(cx, cy), (cx + e1[0], cy + e1[1]), (cx + e2[0], cy + e2[1])]
        shapes.append(shape(f"{tid}-wedge-{corner.lower()}", ops_poly(tri), C(hexs, 0.30)))
    # 曲线（花瓣/直梁，靛青）
    for cv in ts["curves"]:
        if cv["kind"] == "petal":
            ops = ops_petal(cv["p0"], cv["c"], cv["p1"])
        else:
            x0, y0, x1, y1 = cv["rect"]
            ops = ops_poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        shapes.append(shape(f"{tid}-curve-{cv['name']}", ops, C(INDIGO, 0.88)))
    # 中心章（菱形 + 指向局部 N 的三角，三角用砖识别色）
    r = ts["medal_r"]
    dia = [(HALF, HALF - r), (HALF + r, HALF), (HALF, HALF + r), (HALF - r, HALF)]
    shapes.append(shape(f"{tid}-medal", ops_poly(dia), C(INDIGO, 0.78)))
    tr = r * 0.62
    tri = [(HALF, HALF - tr), (HALF + tr * 0.72, HALF + tr * 0.55), (HALF - tr * 0.72, HALF + tr * 0.55)]
    shapes.append(shape(f"{tid}-tri", ops_poly(tri), C(spec["tiles"][tid]["medal"], 0.95)))
    # 边标记（配对记号）
    for edge, ed in ts["edges"].items():
        n, u = edge_frame(edge)
        mx, my = edge_point(edge, HALF)
        cx, cy = mx + 20 * n[0], my + 20 * n[1]
        if ed["marker"] == "twin":
            ops = []
            for sgn in (11, -11):
                dx, dy = cx + sgn * u[0], cy + sgn * u[1]
                tri = [(dx + 8 * n[0], dy + 8 * n[1]),
                       (dx - 6 * n[0] + 6 * u[0], dy - 6 * n[1] + 6 * u[1]),
                       (dx - 6 * n[0] - 6 * u[0], dy - 6 * n[1] - 6 * u[1])]
                ops += ops_poly(tri)[:-1]  # 去掉 Z，合并为同形多环
            ops.append({"kind": "Z"})
            shapes.append(shape(f"{tid}-mark-{edge.lower()}", ops, C(MARKER_HEX["twin"])))
        else:
            dia = [(cx + 9 * n[0], cy + 9 * n[1]), (cx + 7 * u[0], cy + 7 * u[1]),
                   (cx - 9 * n[0], cy - 9 * n[1]), (cx - 7 * u[0], cy - 7 * u[1])]
            shapes.append(shape(f"{tid}-mark-{edge.lower()}", ops_poly(dia), C(MARKER_HEX["solo"])))
    return shapes

# ---------------------------------------------------------------- 场景生成


def plate_body(pid, title, doc):
    return {"id": pid, "title": title, "document": doc}


def tile_scene(spec, tid):
    shapes = [dict(s, transform=m_scale(2)) for s in tile_shapes(spec, tid)]
    return {"width": 512, "height": 512, "background": PAPER,
            "layers": [{"id": f"lay-{tid}", "opacity": 1, "blend": "normal", "shapes": shapes}]}


def board_layers(spec, layout, scale, origin=(0.0, 0.0), cull=None):
    layers = []
    for slot in SLOT_ORDER:
        tid, rot = layout["slots"][slot]
        m = canvas_matrix(slot, rot, scale, origin)
        shapes = []
        for s in tile_shapes(spec, tid):
            if cull is not None:
                pts = [p for op in s["path"] for p in (op.get("points") or [])]
                xs = [m_apply(m, p["x"], p["y"])[0] for p in pts]
                ys = [m_apply(m, p["x"], p["y"])[1] for p in pts]
                if max(xs) < cull[0] or min(xs) > cull[2] or max(ys) < cull[1] or min(ys) > cull[3]:
                    continue
            shapes.append(dict(s, transform=m))
        if shapes:
            layers.append({"id": f"lay-{tid}", "opacity": 1, "blend": "normal", "shapes": shapes})
    return layers


def board_scene(spec, layout):
    return {"width": 256, "height": 256, "background": PAPER,
            "layers": board_layers(spec, layout, 0.5)}


def ann_layer(spec, layout, scale):
    """施工标注层：砖朝向三角（砖识别色）、缝检查点（缝色）、中心交点（靛青）。"""
    shapes = []
    for slot in SLOT_ORDER:
        tid, rot = layout["slots"][slot]
        cx = SLOTS[slot][0] + HALF
        cy = SLOTS[slot][1] + HALF
        d = m_vec(m_rot(rot), 0, -1)
        tip = (cx + 62 * d[0], cy + 62 * d[1])
        b1 = (cx + 34 * d[0] - 10 * d[1], cy + 34 * d[1] + 10 * d[0])
        b2 = (cx + 34 * d[0] + 10 * d[1], cy + 34 * d[1] - 10 * d[0])
        shapes.append(shape(f"ann-arrow-{tid}", ops_poly([tip, b1, b2]),
                            C(spec["tiles"][tid]["medal"], 0.9)))
    for sid, _, _, _, _, (mx, my) in SEAMS:
        dia = [(mx, my - 7), (mx + 7, my), (mx, my + 7), (mx - 7, my)]
        shapes.append(shape(f"ann-seam-{sid}", ops_poly(dia), C(SEAM_HEX[sid], 0.9)))
    jx, jy = JUNCTION
    dia = [(jx, jy - 9), (jx + 9, jy), (jx, jy + 9), (jx - 9, jy)]
    shapes.append(shape("ann-junction", ops_poly(dia), C(INDIGO, 0.9)))
    return {"id": "lay-ann", "opacity": 1, "blend": "normal",
            "shapes": [dict(s, transform=m_scale(scale)) for s in shapes]}


def board_scene_ann(spec, layout):
    doc = board_scene(spec, layout)
    doc["layers"].append(ann_layer(spec, layout, 0.5))
    return doc


def zoom_scene(spec, layout, center, size=192.0, canvas=384):
    origin = (center[0] - size / 2, center[1] - size / 2)
    k = canvas / size
    layers = board_layers(spec, layout, k, origin, cull=(0, 0, canvas, canvas))
    return {"width": canvas, "height": canvas, "background": PAPER, "layers": layers}

# ---------------------------------------------------------------- 核验


def placed_edge(ts_edges, rot, side):
    for local in "NESW":
        if EDGE_ROT[rot][local] == side:
            return local, ts_edges[local]
    raise KeyError(side)


def curve_tan_lookup(ts):
    """(局部边, 端口坐标舍入) -> 曲线端切向（局部）。"""
    table = {}
    for cv in ts["curves"]:
        for end in cv["ends"]:
            x, y = end["at"]
            edge = "N" if y == 0 else "S" if y == T else "E" if x == T else "W"
            table[(edge, round(x, 6), round(y, 6))] = end["tan"]
    return table


def verify_layout(spec, name, layout):
    """结构性检查：同批砖、缝标记、端口对位、切线连续。返回 (检查表, 指纹)。"""
    checks = []
    tiles_used = sorted(v[0] for v in layout["slots"].values())
    checks.append({"check": "same_batch", "ok": tiles_used == ["t1", "t2", "t3", "t4"],
                   "detail": "使用砖=" + ",".join(tiles_used)})
    fp_seams = {}
    for sid, sa, sidea, sb, sideb, _mid in SEAMS:
        ta, ra = layout["slots"][sa]
        tb, rb = layout["slots"][sb]
        tsa = spec["types"][spec["tiles"][ta]["type"]]
        tsb = spec["types"][spec["tiles"][tb]["type"]]
        la, ea = placed_edge(tsa["edges"], ra, sidea)
        lb, eb = placed_edge(tsb["edges"], rb, sideb)
        ma, mb = world_matrix(sa, ra), world_matrix(sb, rb)
        ok_marker = ea["marker"] == eb["marker"]
        # 端口世界坐标对位
        def ports(edge_local, ed, m):
            return sorted(m_apply(m, *edge_point(edge_local, f))
                          for f in spec["port_rule"][ed["marker"]])
        pa, pb = ports(la, ea, ma), ports(lb, eb, mb)
        ok_ports = len(pa) == len(pb) and all(
            math.hypot(x[0] - y[0], x[1] - y[1]) < 1e-6 for x, y in zip(pa, pb))
        # 曲线端切向共线反向
        tan_a, tan_b = curve_tan_lookup(tsa), curve_tan_lookup(tsb)
        ok_tan = True
        tan_detail = []
        if ok_ports:
            for f in spec["port_rule"][ea["marker"]]:
                pta = edge_point(la, f)
                ptb = edge_point(lb, f)
                va = tan_a.get((la, round(pta[0], 6), round(pta[1], 6)))
                vb = tan_b.get((lb, round(ptb[0], 6), round(ptb[1], 6)))
                if va is None or vb is None:
                    ok_tan = False
                    tan_detail.append("端口缺曲线端")
                    continue
                wa = m_vec(ma, *va)
                wb = m_vec(mb, *vb)
                dot = wa[0] * wb[0] + wa[1] * wb[1]
                if abs(dot + 1) > 1e-6:
                    ok_tan = False
                    tan_detail.append(f"切向夹角异常 dot={dot:.4f}")
        checks.append({"check": f"seam_{sid}_marker", "ok": ok_marker,
                       "detail": f"{ta}.{sidea}({ea['marker']}) ↔ {tb}.{sideb}({eb['marker']})"})
        checks.append({"check": f"seam_{sid}_ports", "ok": ok_ports,
                       "detail": f"{len(pa)} 对端口" + ("对位一致" if ok_ports else "错位")})
        checks.append({"check": f"seam_{sid}_tangent", "ok": ok_tan,
                       "detail": "；".join(tan_detail) if tan_detail else "切线跨缝连续"})
        fp_seams[sid] = [ea["ink"], eb["ink"]]
    # 中心交点角序
    junction = []
    for slot in SLOT_ORDER:
        tid, rot = layout["slots"][slot]
        ts = spec["types"][spec["tiles"][tid]["type"]]
        want = JUNCTION_CORNER[slot]
        local = [c for c in ("NE", "SE", "SW", "NW") if CORNER_ROT[rot][c] == want][0]
        junction.append(ts["corners"][local])
    fingerprint = {"seams": fp_seams, "junction": junction,
                   "slots": {s: list(layout["slots"][s]) for s in SLOT_ORDER}}
    return checks, fingerprint


def drill_report(spec, layouts, name, layout, fp):
    base_name = layout["base"]
    base_fp = verify_layout(spec, base_name, layouts[base_name])[1]
    deviations = []
    for sid in fp["seams"]:
        if fp["seams"][sid] != base_fp["seams"][sid]:
            deviations.append({"where": f"seam_{sid}", "kind": "叠色",
                               "want": base_fp["seams"][sid], "got": fp["seams"][sid]})
    for i, slot in enumerate(SLOT_ORDER):
        if fp["junction"][i] != base_fp["junction"][i]:
            deviations.append({"where": f"junction.{slot}", "kind": "角部",
                               "want": base_fp["junction"][i], "got": fp["junction"][i]})
    for slot in SLOT_ORDER:
        if fp["slots"][slot] != base_fp["slots"][slot]:
            deviations.append({"where": f"slot.{slot}", "kind": "砖位/朝向",
                               "want": base_fp["slots"][slot], "got": fp["slots"][slot]})
    # 恢复搜索：单砖旋转
    recovery = []
    for slot in SLOT_ORDER:
        for delta in (90, 180, 270):
            cand = copy.deepcopy(layout["slots"])
            cand[slot] = [cand[slot][0], (cand[slot][1] + delta) % 360]
            _, cand_fp = verify_layout(spec, name, {"slots": cand})
            if cand_fp == base_fp:
                recovery.append({"kind": "rotate", "slot": slot, "tile": cand[slot][0],
                                 "delta": delta, "result": "恢复为基准摆法"})
    # 恢复搜索：双砖对调（连位置带朝向整体互换）
    for i in range(len(SLOT_ORDER)):
        for j in range(i + 1, len(SLOT_ORDER)):
            cand = copy.deepcopy(layout["slots"])
            cand[SLOT_ORDER[i]], cand[SLOT_ORDER[j]] = cand[SLOT_ORDER[j]], cand[SLOT_ORDER[i]]
            _, cand_fp = verify_layout(spec, name, {"slots": cand})
            if cand_fp == base_fp:
                recovery.append({"kind": "swap", "slots": [SLOT_ORDER[i], SLOT_ORDER[j]],
                                 "result": "对调后恢复为基准摆法"})
    verdict = ("与基准一致" if not deviations else
               "单砖可恢复" if any(r["kind"] == "rotate" for r in recovery) else
               "需双砖对调" if any(r["kind"] == "swap" for r in recovery) else
               "需整体重排")
    return {"base": base_name, "deviations": deviations, "recovery": recovery, "verdict": verdict}

# ---------------------------------------------------------------- 交付物写出


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def main():
    ppl.selftest()
    spec = build_spec()
    layouts = build_layouts()
    (ROOT / "examples/rehang").mkdir(parents=True, exist_ok=True)
    (ROOT / "examples/rehang/tilespec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "examples/rehang/layouts.json").write_text(
        json.dumps(layouts, ensure_ascii=False, indent=1), encoding="utf-8")

    out = ROOT / "out/rehang"
    for d in ("scenes", "tiles", "boards", "zooms"):
        (out / d).mkdir(parents=True, exist_ok=True)

    # 场景清单：(plate_id, 标题, 文档, 输出目录, 输出名)
    jobs = []
    for tid in ("t1", "t2", "t3", "t4"):
        tp = spec["tiles"][tid]["type"]
        jobs.append((f"tile-{tid}", f"源砖 {tid.upper()}·{spec['types'][tp]['label']}",
                     tile_scene(spec, tid), "tiles", f"tile-{tid}"))
    jobs.append(("board-l1", "整版 L1·原展", board_scene(spec, layouts["l1"]), "boards", "board-l1-original"))
    jobs.append(("board-l1-ann", "整版 L1·原展(施工标注)", board_scene_ann(spec, layouts["l1"]), "boards", "board-l1-annotated"))
    jobs.append(("board-l2", "整版 L2·换展", board_scene(spec, layouts["l2"]), "boards", "board-l2-rehang"))
    jobs.append(("board-l2-ann", "整版 L2·换展(施工标注)", board_scene_ann(spec, layouts["l2"]), "boards", "board-l2-annotated"))
    jobs.append(("board-fault", "整版 L2·装反演练", board_scene(spec, layouts["fault"]), "boards", "board-l2-fault"))
    jobs.append(("board-fixed", "整版 L2·单砖恢复", board_scene(spec, layouts["fixed"]), "boards", "board-l2-fixed"))
    jobs.append(("board-swap", "整版 L2·对调演练", board_scene(spec, layouts["swap"]), "boards", "board-l2-swap"))
    zooms = [
        ("zoom-l1-junction", "放大·L1 中心交点", layouts["l1"], JUNCTION),
        ("zoom-l2-junction", "放大·L2 中心交点", layouts["l2"], JUNCTION),
        ("zoom-l2-s2", "放大·L2 缝②", layouts["l2"], SEAMS[1][5]),
        ("zoom-l2-s4", "放大·L2 缝④", layouts["l2"], SEAMS[3][5]),
        ("zoom-fault-junction", "放大·装反 中心交点", layouts["fault"], JUNCTION),
        ("zoom-fault-s2", "放大·装反 缝②", layouts["fault"], SEAMS[1][5]),
        ("zoom-fault-s4", "放大·装反 缝④", layouts["fault"], SEAMS[3][5]),
        ("zoom-swap-junction", "放大·对调 中心交点", layouts["swap"], JUNCTION),
    ]
    for pid, title, lay, center in zooms:
        jobs.append((pid, title, zoom_scene(spec, lay, center), "zooms", pid))

    artifacts = []
    budgets = {}
    for pid, title, doc, sub, fname in jobs:
        body = plate_body(pid, title, doc)
        scene_path = out / "scenes" / f"{pid}.json"
        scene_path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        comp = ppl.compile_doc(doc)
        budgets[pid] = {"segments": comp["segments"],
                        "limit": 160_000_000 // (4 * comp["W"] * comp["H"]),
                        "canvas": [comp["W"], comp["H"]]}
        png = ppl.encode_png(comp["W"], comp["H"], ppl.render_pixels(comp))
        svg = ppl.export_svg(doc)
        (out / sub / f"{fname}.png").write_bytes(png)
        (out / sub / f"{fname}.svg").write_bytes(svg)
        artifacts.append({"plate": pid, "title": title,
                          "scene": f"scenes/{pid}.json",
                          "png": f"{sub}/{fname}.png", "png_sha256": sha256(png),
                          "svg": f"{sub}/{fname}.svg", "svg_sha256": sha256(svg),
                          "canvas": [comp["W"], comp["H"]], "segments": comp["segments"]})
        print(f"  rendered {pid:22s} {comp['W']}x{comp['H']} segments={comp['segments']}")

    # 确定性复核：L2 重渲染字节一致；恢复版与 L2 逐字节一致
    doc_l2 = board_scene(spec, layouts["l2"])
    comp = ppl.compile_doc(doc_l2)
    again = ppl.encode_png(comp["W"], comp["H"], ppl.render_pixels(comp))
    l2_png = (out / "boards/board-l2-rehang.png").read_bytes()
    fixed_png = (out / "boards/board-l2-fixed.png").read_bytes()
    determinism = {"l2_rerender_equal": sha256(again) == sha256(l2_png),
                   "fixed_equals_l2": sha256(fixed_png) == sha256(l2_png)}

    # 布局核验与演练判定
    report_layouts = {}
    for name, lay in layouts.items():
        checks, fp = verify_layout(spec, name, lay)
        entry = {"label": lay["label"], "kind": lay["kind"],
                 "intrinsic_ok": all(c["ok"] for c in checks), "checks": checks,
                 "fingerprint": fp}
        if lay["kind"] == "drill":
            entry["drill"] = drill_report(spec, layouts, name, lay, fp)
        report_layouts[name] = entry

    report = {"generator": "scripts/rehang.py",
              "renderer": "scripts/prismplate_local.py (prism-plate-v1 契约离线校样器)",
              "sources": {"tilespec": "examples/rehang/tilespec.json",
                          "layouts": "examples/rehang/layouts.json"},
              "budgets": budgets, "determinism": determinism,
              "layouts": report_layouts, "artifacts": artifacts}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "placement.md").write_text(placement_md(spec, layouts, report_layouts), encoding="utf-8")
    print(json.dumps({"layouts": {k: {"intrinsic_ok": v["intrinsic_ok"],
                                      **({"verdict": v["drill"]["verdict"]} if "drill" in v else {})}
                                   for k, v in report_layouts.items()},
                      "determinism": determinism}, ensure_ascii=False, indent=1))


def placement_md(spec, layouts, report):
    L = []
    L.append("# 棱版可重排图稿 · 摆放顺序施工单\n")
    L.append("同一批 4 块源砖（2 块甲型「回环」+ 2 块乙型「川流」），换展只挪动、旋转原件，不重印。")
    L.append("本单与 `out/rehang/report.json` 同源生成；复核依据见 `docs/rehang-qc.md`。\n")
    L.append("## 交付清单\n")
    L.append("- 源稿：`examples/rehang/tilespec.json`（砖型/标记/曲线/墨色）、`examples/rehang/layouts.json`（两套可用摆法 + 三套演练）")
    L.append("- 稿本：`out/rehang/scenes/*.json`（prism-plate-v1 契约，可直接 POST 棱版服务复现）")
    L.append("- 校样：`out/rehang/tiles/`（单砖 512px）、`out/rehang/boards/`（整版 256px，含施工标注版）、`out/rehang/zooms/`（缝位/交点 384px 放大）")
    L.append("- 核验：`out/rehang/report.json`（自动判定与恢复建议）\n")
    L.append("## 源砖清单\n")
    L.append("| 砖 | 型 | 中心章识别色 | 边标记配置（局部 N/E/S/W） |")
    L.append("|---|---|---|---|")
    for tid in ("t1", "t2", "t3", "t4"):
        tp = spec["tiles"][tid]["type"]
        ts = spec["types"][tp]
        marks = "/".join(f"{e}:{ts['edges'][e]['marker']}" for e in "NESW")
        L.append(f"| {tid.upper()} | {ts['label']} | {spec['tiles'][tid]['medal']} | {marks} |")
    L.append("")
    L.append("## 边标记与配对规则\n")
    L.append("- 双三角（铜色 #a05a08）= twin 边：两个曲线端口（1/3、2/3 处）。")
    L.append("- 单菱（钢蓝 #0b6e8c）= solo 边：一个曲线端口（1/2 处）。")
    L.append("- 配对规则：同标记相拼（twin↔twin、solo↔solo）；拼上后端口自然对位、曲线切线跨缝连续。")
    L.append("- 乙型砖 solo 边在局部 N/S：摆法有效当且仅当两块乙型砖的 solo 边互相拼合（同列同向或同行对置）。")
    L.append("- 注意：边标记对 ≠ 摆对。角部色序、缝位叠色、中心章朝向还要过复核（见第「演练」节）。\n")
    for name in ("l1", "l2"):
        lay = layouts[name]
        L.append(f"## 摆法 {name.upper()} · {lay['label']}\n")
        L.append("| 序 | 槽位 | 砖 | 旋转 | 局部N朝向 | 放位后立即核验 |")
        L.append("|---|---|---|---|---|---|")
        new_seams = {"NW": "—", "NE": "缝①", "SW": "缝③", "SE": "缝②+缝④+中心交点"}
        for i, slot in enumerate(SLOT_ORDER):
            tid, rot = lay["slots"][slot]
            L.append(f"| {i+1} | {slot} | {tid.upper()} | {rot}° | {ROT_NAME[rot].split('(')[1].rstrip(')')} | {new_seams[slot]} |")
        L.append("")
        fp = report[name]["fingerprint"]
        L.append("| 缝 | 两侧 | 标记 | 叠色（左/上侧 → 右/下侧） |")
        L.append("|---|---|---|---|")
        for sid, sa, sidea, sb, sideb, _m in SEAMS:
            ta, ra = lay["slots"][sa]
            tb, rb = lay["slots"][sb]
            tsa = spec["types"][spec["tiles"][ta]["type"]]
            tsb = spec["types"][spec["tiles"][tb]["type"]]
            _, ea = placed_edge(tsa["edges"], ra, sidea)
            _, eb = placed_edge(tsb["edges"], rb, sideb)
            inks = fp["seams"][sid]
            L.append(f"| {sid} | {ta.upper()}.{sidea} ↔ {tb.upper()}.{sideb} | {ea['marker']}↔{eb['marker']} | {inks[0]} → {inks[1]} |")
        L.append("")
        L.append(f"中心交点角序（NW/NE/SW/SE 槽位象限）：{' / '.join(fp['junction'])}。")
        medals = ", ".join("%s→%s" % (lay["slots"][s][0].upper(), ROT_NAME[lay["slots"][s][1]]) for s in SLOT_ORDER)
        L.append(f"中心章：{medals}。\n")
    L.append("## 演练记录（详见 report.json）\n")
    L.append("| 演练 | 现象 | 判定 | 现场处置 |")
    L.append("|---|---|---|---|")
    for name in ("fault", "fixed", "swap"):
        d = report[name]["drill"]
        dev = "、".join(sorted(set(x["kind"] for x in d["deviations"]))) or "无"
        rec = "；".join(f"{r['kind']}:{r.get('tile') or r.get('slots')} {r.get('delta','')}" for r in d["recovery"]) or "—"
        L.append(f"| {layouts[name]['label']} | {dev} | {d['verdict']} | {rec} |")
    L.append("")
    L.append("## 摆放与复核顺序\n")
    L.append("1. 按上表顺序落砖（NW→NE→SW→SE），每落一块立即核对该步新增缝的边标记。")
    L.append("2. 四砖到位后：对缝四查（标记→端口→叠色→角序）+ 中心章朝向，对照对应摆法的整版校样与放大校样。")
    L.append("3. 任何不符先查单砖朝向（最多试 4 个朝向）；单砖无法恢复再查同型砖对调；仍不符按 docs/rehang-qc.md 决定整体重排。")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
