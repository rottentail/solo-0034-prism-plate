#!/usr/bin/env python3
"""Generate the topology casebook as ordinary, legal plate manuscripts.

Every emitted JSON is a POST /v1/plates body (id, title, document only).
Coordinates are integers or short exact binary/decimal fractions.

Group A/B/D are planar and axis-aligned so their numbers can be checked by hand.
Group C uses cubic ovals whose exact area is 10/3*a*b.  Group E is organised as
four three-member families: for one fixed directed area the members move from
separated (determinate), through contact (undetermined), to overlap (determinate
but different topology).  Every expectation is recomputed by verify.py.
"""
from __future__ import annotations

import json
import pathlib
from fractions import Fraction as F

HERE = pathlib.Path(__file__).resolve().parent
CASES = HERE.parent / "cases"

BG = {"r": 0.055, "g": 0.065, "b": 0.09, "a": 1}
TEAL = {"r": 0.12, "g": 0.62, "b": 0.70, "a": 1}
ROSE = {"r": 0.85, "g": 0.28, "b": 0.42, "a": 1}
MIRROR = [-1, 0, 0, 1, 240, 0]  # canvas x = 240 - local x


def n(v):
    v = F(v)
    if v.denominator == 1:
        return int(v)
    d, a, b = v.denominator, 0, 0
    while d % 2 == 0:
        d //= 2; a += 1
    while d % 5 == 0:
        d //= 5; b += 1
    if d != 1 or a > 12 or b > 12:
        raise ValueError(f"coordinate {v} is not exactly representable")
    return float(v)


def pt(x, y):
    return {"x": n(x), "y": n(y)}


def op(kind, *points):
    o = {"kind": kind}
    if points:
        o["points"] = [pt(*p) for p in points]
    return o


def ring(points):
    return [op("M", points[0])] + [op("L", p) for p in points[1:]] + [op("Z")]


def rect(x0, y0, x1, y1, ccw=True):
    vs = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return ring(vs if ccw else list(reversed(vs)))


def diamond(cx, cy, r, ccw=True):
    vs = [(cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r)]
    return ring(vs if ccw else list(reversed(vs)))


def oval(cx, cy, a, b, ccw=True, k=F(2, 3)):
    """Cubic oval; control factor k. Exact signed area is 10/3*a*b for k=2/3."""
    cx, cy, a, b, k = F(cx), F(cy), F(a), F(b), F(k)
    if not ccw:
        b = -b
    pts = [
        (cx + a, cy),
        (cx + a, cy + k * b), (cx + k * a, cy + b), (cx, cy + b),
        (cx - k * a, cy + b), (cx - a, cy + k * b), (cx - a, cy),
        (cx - a, cy - k * b), (cx - k * a, cy - b), (cx, cy - b),
        (cx + k * a, cy - b), (cx + a, cy - k * b), (cx + a, cy),
    ]
    out = [op("M", pts[0])]
    for i in range(4):
        out.append(op("C", pts[1 + 3 * i], pts[2 + 3 * i], pts[3 + 3 * i]))
    out.append(op("Z"))
    return out


def cat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def shape(sid, path, rule="nonzero", color=TEAL, transform=None):
    return {"id": sid, "path": path, "fill_rule": rule,
            "brush": {"kind": "solid", "color": color},
            "transform": transform or [1, 0, 0, 1, 0, 0]}


def doc(shapes, width=256, height=192):
    return {"width": width, "height": height, "background": BG,
            "layers": [{"id": "layer-main", "opacity": 1, "blend": "normal",
                        "shapes": shapes}]}


def plate(pid, title, document, group, question, claim, pairs, family=None,
          degenerate=False):
    return {
        "id": pid, "title": title, "document": document,
        "_casebook": {"group": group, "question": question, "claim": claim,
                      "pairs": pairs, "family": family, "degenerate": degenerate},
    }


def build():
    cases = []

    # ---------------------------------------------------------------- A
    # Directed area under self-intersection and compound rings.
    bow = ring([(80, 80), (80, 160), (224, 16), (224, 176)])
    cases.append(plate(
        "case-a1-bowtie", "A1 领结自交：有向面积 5760，两块实心",
        doc([shape("shape-bowtie", bow)]), "A",
        "有向面积的正负/大小能否说明孔数",
        "单环自交成两瓣，绕向相反：有向面积 7680-1920，填充 7680+1920；两瓣只在交点相接",
        ["case-a2-nested-opposite", "case-a4-zero-bowtie", "case-b1-same-orientation"],
        "self-intersection", degenerate=True))

    nested = cat(rect(64, 48, 160, 128, True), rect(88, 68, 136, 108, False))
    cases.append(plate(
        "case-a2-nested-opposite", "A2 内环反向且完全包含：有向面积 5760，一个孔",
        doc([shape("shape-compound", nested)]), "A",
        "反向内环是否一定成孔",
        "外环 7680、内环 -1920；有向面积等于填充面积 5760，孔洞恰好 1 个",
        ["case-a1-bowtie", "case-b1-same-orientation"], "orientation"))

    crossing = cat(rect(104, 48, 200, 128, True), rect(88, 68, 136, 108, False))
    cases.append(plate(
        "case-a3-crossing-opposite", "A3 反向内环穿出外环：有向面积 5760，孔消失",
        doc([shape("shape-compound", crossing)]), "A",
        "同样的两环，平移是否改变孔数",
        "内环左边越出外环；有向面积仍 5760，填充 7040，孔与连通块随接触约定改变",
        ["case-a2-nested-opposite"], "orientation", degenerate=True))

    zw = ring([(64, 48), (64, 144), (192, 48), (192, 144)])
    cases.append(plate(
        "case-a4-zero-bowtie", "A4 对称领结：有向面积 0，填充 6144",
        doc([shape("shape-bowtie-zero", zw)]), "A",
        "有向面积为 0 是否等于没有填充",
        "两瓣等面积、反向绕，有向面积严格为 0；两瓣各 3072，只在交点相接",
        ["case-a1-bowtie", "case-a5-zero-disjoint", "case-e13-cancelling-rings"],
        "zero-area", degenerate=True))

    cases.append(plate(
        "case-a5-zero-disjoint", "A5 两个反向分离形状：有向面积 0，两块实心",
        doc([shape("shape-plus", rect(32, 56, 112, 96), color=TEAL),
             shape("shape-minus", rect(144, 130, 224, 170), color=ROSE,
                   transform=[-1, 0, 0, 1, 256, 0])]), "A",
        "有向面积 0 能否说明是空图",
        "两块各 3200、绕向相反，和严格为 0；两块分离，孔 0",
        ["case-a4-zero-bowtie", "case-e13-cancelling-rings"], "zero-area"))

    # ---------------------------------------------------------------- B
    # Orientation, nesting, disjointness, mirror, multi-shape union.
    cases.append(plate(
        "case-b1-same-orientation", "B1 内环同向且包含：有向面积 5760，零孔",
        doc([shape("shape-compound",
                   cat(rect(104, 56, 184, 116, True), rect(124, 74, 164, 98, True)))]),
        "B", "有向面积相同是否等于拓扑相同",
        "与 A2 同为 5760；同向内环不挖孔，填充 4800",
        ["case-a2-nested-opposite"], "orientation"))

    b2_local = cat(rect(48, 48, 128, 108, True), rect(200, 136, 240, 160, False))
    cases.append(plate(
        "case-b2-mirror-disjoint", "B2 镜像 + 外侧小环：有向面积 -3840，两块实心",
        doc([shape("shape-mirrored", b2_local, transform=MIRROR)]), "B",
        "镜像是否改变拓扑",
        "镜像行列式 -1，画布有向面积 -3840；两环分离，连通块 2、孔 0",
        ["case-b3-mirror-nested", "case-b4-direct-nested"], "mirror"))

    b3_local = cat(rect(48, 48, 128, 108, True), rect(68, 66, 108, 90, False))
    cases.append(plate(
        "case-b3-mirror-nested", "B3 镜像 + 内环包含：有向面积 -3840，一个孔",
        doc([shape("shape-mirrored", b3_local, transform=MIRROR)]), "B",
        "同有向面积是否同孔数",
        "与 B2 同为 -3840；内环被包含，连通块 1、孔 1、填充 3840",
        ["case-b2-mirror-disjoint", "case-b4-direct-nested"], "mirror"))

    cases.append(plate(
        "case-b4-direct-nested", "B4 同稿不镜像：有向面积 +3840，一个孔",
        doc([shape("shape-direct", b3_local)]), "B",
        "镜像是否只改符号",
        "与 B3 同一本地轮廓；镜像只把有向面积翻成 -3840，连通块与孔数不变",
        ["case-b3-mirror-nested"], "mirror"))

    cases.append(plate(
        "case-b5-two-shapes", "B5 两个独立形状：和 +4800，两块实心",
        doc([shape("shape-left", rect(24, 48, 84, 88), color=TEAL),
             shape("shape-right", rect(120, 104, 180, 144), color=ROSE)]), "B",
        "多形状的有向面积能否相加后判断孔数",
        "逐形状有向面积 2400+2400=4800；两块分离，连通块 2、孔 0",
        ["case-b6-one-shape"], "union"))

    cases.append(plate(
        "case-b6-one-shape", "B6 单个形状：有向面积 +4800，一块实心",
        doc([shape("shape-block", rect(88, 56, 168, 116))]), "B",
        "同有向面积是否同连通块数",
        "与 B5 同为 4800；这里一块实心，连通块 1、孔 0",
        ["case-b5-two-shapes"], "union"))

    # ---------------------------------------------------------------- C
    # Curves: exact Bezier area, compound curves, near tangency, mirror.
    outer = oval(128, 96, 96, 63)          # exact 20160
    inner = oval(128, 96, 33, 24, False)   # exact -2640
    cases.append(plate(
        "case-c1-oval-nested", "C1 曲线复合轮廓：精确有向面积 17520，一个孔",
        doc([shape("shape-ovals", cat(outer, inner))]), "C",
        "曲线稿的有向面积怎样算才不依赖像素",
        "三次曲线精确积分 17520（外 20160、内 -2640）；16/24 折线拓扑一致但面积略小",
        ["case-c2-oval-crossing", "case-c4-oval-pair", "case-c5-oval-mirror"],
        "curve"))

    cases.append(plate(
        "case-c2-oval-crossing", "C2 曲线内环穿出外环：有向面积 17520，两次横穿",
        doc([shape("shape-ovals", cat(outer, oval(200, 96, 33, 24, False)))]), "C",
        "曲线平移后面积是否变",
        "精确有向面积仍 17520；两环横向相交，填充集合随接触约定变化",
        ["case-c1-oval-nested", "case-c3-oval-near-tangent"], "curve",
        degenerate=True))

    cases.append(plate(
        "case-c6-oval-shallow-cross", "C6 曲线浅交：重叠 1e-4，有向面积 17520",
        doc([shape("shape-ovals",
                   cat(outer, oval(F(1910001, 10000), 96, 33, 24, False)))]), "C",
        "浅交与近切是否可判",
        "两曲线真实相交，交叠深度约 1e-4，小于折线误差界 b；证书无法在 b 内确认绕数翻转",
        ["case-c2-oval-crossing", "case-c3-oval-near-tangent"], "curve", degenerate=True))

    cases.append(plate(
        "case-c3-oval-near-tangent", "C3 曲线近切：间隙 1e-4，有向面积 17520",
        doc([shape("shape-ovals",
                   cat(outer, oval(F(1909999, 10000), 96, 33, 24, False)))]), "C",
        "曲线要近似到什么程度才够判定拓扑",
        "两曲线右端间隙约 1e-4，远小于 16/24 折线误差界；折线会造出交点，真实拓扑不能由折线判定",
        ["case-c1-oval-nested", "case-c2-oval-crossing"], "curve", degenerate=True))

    cases.append(plate(
        "case-c4-oval-pair", "C4 两枚分离椭圆：有向面积 17520，两块实心、零孔",
        doc([shape("shape-ovals",
                   cat(oval(60, 96, 48, 42), oval(190, 96, 60, 54)))]), "C",
        "有向面积与填充面积都相同是否拓扑相同",
        "精确面积 6720+10800=17520，填充同为 17520；与 C1 相比是 2 块、0 孔",
        ["case-c1-oval-nested", "case-c5-oval-mirror"], "curve"))

    cases.append(plate(
        "case-c5-oval-mirror", "C5 曲线稿镜像：有向面积 -17520，一个孔",
        doc([shape("shape-ovals-mirrored", cat(outer, inner), transform=MIRROR)]), "C",
        "镜像曲线是否改变孔数",
        "与 C1 同一本地稿；有向面积变号 -17520，拓扑仍是 1 块 1 孔",
        ["case-c1-oval-nested"], "curve"))

    # ---------------------------------------------------------------- D
    # Fill rule and coincident rings.
    star = ring([(128, 16), (188, 189), (37, 82), (219, 82), (68, 189)])
    cases.append(plate(
        "case-d1-star-nonzero", "D1 五角星 nonzero：有向面积 13697，一块实心",
        doc([shape("shape-star", star, rule="nonzero")]), "D",
        "有向面积能否区分填充规则",
        "中心五边形绕数 2；nonzero 仍填充，五角与中心连成 1 块、孔 0",
        ["case-d2-star-evenodd"], "fill-rule"))

    cases.append(plate(
        "case-d2-star-evenodd", "D2 五角星 evenodd：有向面积 13697，五角加中心孔",
        doc([shape("shape-star", star, rule="evenodd")]), "D",
        "同一路径换规则是否改孔数",
        "有向面积同为 13697；中心被挖空，五角只在交点相接",
        ["case-d1-star-nonzero"], "fill-rule", degenerate=True))

    dup = cat(rect(64, 48, 160, 128, True), rect(64, 48, 160, 128, True))
    cases.append(plate(
        "case-d3-duplicate-nonzero", "D3 完全重合双环 nonzero：有向面积 15360，实心 7680",
        doc([shape("shape-duplicate", dup, rule="nonzero")]), "D",
        "重合边界的有向面积是否有意义",
        "两环完全重合，有向面积是单环两倍 15360，填充仍是 7680；边界处处重合，按重合约定计数",
        ["case-d4-duplicate-evenodd"], "coincident", degenerate=True))

    cases.append(plate(
        "case-d4-duplicate-evenodd", "D4 完全重合双环 evenodd：有向面积 15360，填充为空",
        doc([shape("shape-duplicate", dup, rule="evenodd")]), "D",
        "同一退化边界换规则会怎样",
        "有向面积仍 15360，绕数处处为偶，填充为空，连通块 0、孔 0；重合边界同样是退化",
        ["case-d3-duplicate-nonzero"], "coincident", degenerate=True))

    # ---------------------------------------------------------------- E
    # Four families of three: separated -> contact -> overlap, one directed area.
    rect_hole = rect(48, 40, 144, 120)  # 96x80 = 7680
    for pid, title, cxy, claim, deg in [
        ("case-e1-hole-inside", "E1 孔完全在内：有向面积 6528，一个孔",
         (104, 80), "反向菱形环完全在外环内：孔洞 1，填充 6528", False),
        ("case-e2-hole-tangent", "E2 孔与外边界单点相切：有向面积 6528",
         (120, 80), "菱形孔尖点落在矩形右边线上：孔数取决于开/闭约定", True),
        ("case-e3-hole-crossing", "E3 孔环穿出外边界：有向面积 6528，孔消失",
         (136, 80), "内环越出外环：外凸部分按 nonzero 被填充，缺口与孔都随约定改变", True),
    ]:
        cases.append(plate(
            pid, title,
            doc([shape("shape-hole", cat(rect_hole, diamond(cxy[0], cxy[1], 24, False)))]),
            "E", "孔洞与边界接触时孔数是多少", claim,
            ["case-e1-hole-inside", "case-e3-hole-crossing"]
            if pid != "case-e1-hole-inside" else ["case-e3-hole-crossing"],
            "hole-boundary", degenerate=deg))

    for pid, title, cy, claim, deg in [
        ("case-e4-apex-apart", "E4 两菱形分离：有向面积 6400，两块实心",
         150, "两瓣间距 14，开/闭读法都给连通块 2、孔 0", False),
        ("case-e5-apex-contact", "E5 两菱形单点相接：有向面积 6400，两块还是一块",
         136, "两瓣只在一点接触；开集两块，闭包一块", True),
        ("case-e6-apex-overlap", "E6 两菱形重叠：有向面积 6400，一块实心",
         120, "重叠 16：并集一块，填充面积小于有向面积", False),
    ]:
        cases.append(plate(
            pid, title,
            doc([shape("shape-lobes", cat(diamond(128, 56, 40),
                                         diamond(128, cy, 40)))]),
            "E", "单点接触算不算同一边界", claim,
            ["case-e4-apex-apart", "case-e6-apex-overlap"]
            if pid != "case-e4-apex-apart" else ["case-e6-apex-overlap"],
            "point-contact", degenerate=deg))

    for pid, title, x0, claim, deg in [
        ("case-e7-edge-gap", "E7 两矩形留缝：有向面积 11520，两块实心",
         132, "两矩形间距 4，连通块 2、孔 0", False),
        ("case-e8-edge-shared", "E8 两矩形共边：有向面积 11520，两块还是一块",
         128, "共享一段边；开集两块，闭包一块", True),
        ("case-e9-edge-overlap", "E9 两矩形重叠：有向面积 11520，一块实心",
         120, "重叠 8：并集一块，填充 11136", False),
    ]:
        cases.append(plate(
            pid, title,
            doc([shape("shape-shared-edge", cat(rect(48, 48, 128, 128),
                                                rect(x0, 80, x0 + 80, 144)))]),
            "E", "共边重叠算不算连通", claim,
            ["case-e7-edge-gap", "case-e9-edge-overlap"]
            if pid != "case-e7-edge-gap" else ["case-e9-edge-overlap"],
            "edge-contact", degenerate=deg))

    for pid, title, cx, claim, deg in [
        ("case-e10-curve-gap", "E10 两椭圆分离：有向面积 7800，两块实心",
         172, "两椭圆间隙 6，远大于折线误差界；连通块 2、孔 0", False),
        ("case-e11-curve-tangent", "E11 两椭圆外切：有向面积 7800，两块还是一块",
         166, "两椭圆在 (127,96) 有公切线；折线在切点相接，计数依赖约定", True),
        ("case-e12-curve-overlap", "E12 两椭圆重叠：有向面积 7800，一块实心",
         158, "重叠 8：并集一块，填充面积小于有向面积", False),
    ]:
        cases.append(plate(
            pid, title,
            doc([shape("shape-tangent-ovals",
                       cat(oval(88, 96, 39, 30), oval(cx, 96, 39, 30)))]),
            "E", "曲线相切能否由折线判定", claim,
            ["case-e10-curve-gap", "case-e12-curve-overlap"]
            if pid != "case-e10-curve-gap" else ["case-e12-curve-overlap"],
            "curve-contact", degenerate=deg))

    cases.append(plate(
        "case-e13-cancelling-rings", "E13 反向完全重合双环：有向面积 0，填充为空",
        doc([shape("shape-cancel", cat(rect(64, 48, 160, 128, True),
                                       rect(64, 48, 160, 128, False)))]), "E",
        "填充为空是否等于没有退化",
        "处处绕数 0，填充集为空；但两条边界完全重合，任何区域计数都建立在重合约定上",
        ["case-a5-zero-disjoint", "case-a4-zero-bowtie"], "cancellation",
        degenerate=True))

    cases.append(plate(
        "case-e14-subpixel-ring", "E14 亚像素边框：线宽 0.02，有向面积 7.0384",
        doc([shape("shape-subpixel",
                   cat(rect(48, 40, 144, 120, True),
                       rect(F(4802, 100), F(4002, 100), F(14398, 100), F(11998, 100), False)))]),
        "E", "几何上存在的孔，像素校样看不见",
        "外环 7680、内环 -7672.9616，几何填充是宽 0.02 的边框，面积 7.0384；"
        "512 像素覆盖下边框必然消失，孔 1 个但肉眼不可见",
        ["case-e1-hole-inside"], "subpixel", degenerate=True))

    return cases


def main():
    CASES.mkdir(parents=True, exist_ok=True)
    for p in sorted(CASES.glob("*.json")):
        p.unlink()
    index = []
    for c in build():
        meta = c.pop("_casebook")
        (CASES / (c["id"] + ".json")).write_text(
            json.dumps(c, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        index.append({"id": c["id"], "title": c["title"], "file": c["id"] + ".json", **meta})
    (CASES / "index.json").write_text(
        json.dumps({"cases": index}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(index)} cases")


if __name__ == "__main__":
    main()
