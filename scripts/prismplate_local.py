#!/usr/bin/env python3
"""棱版本地离线校样器（prism-plate-v1 契约的同语义 Python 实现）。

用途：在没有 Go 工具链的环境里，按 internal/{geom,paint,raster,vector} 的同一契约
从稿本 JSON 生成 PNG 像素校样与 SVG 矢量校样。权威实现仍是 Go 服务；本工具与其
共享同一份稿本，便于交叉核对（同一稿本在两侧应得到逐像素一致的结果，PNG 字节
编码可能不同）。

契约要点（与 Go 实现一一对应）：
  * geom.Flatten：Q 固定细分 16 段、C 固定细分 24 段；M/L/Q/C/Z 必须显式闭合。
  * Path.Contains：非零环绕 / evenodd，射线法（交点 x 严格大于采样点才计数）。
  * paint：sRGB 直通输入，内部转线性光预乘透明度；图层 normal/multiply/screen；
    组透明度在整层合成后施加。
  * raster：每像素 2x2 四点覆盖采样，采样点在 (x+(sx+0.5)/2, y+(sy+0.5)/2)。
  * vector：复用展开几何的安全 SVG，线性渐变、裁域、蒙版按图层编号。

仅依赖 Python 标准库。用法：
  python3 scripts/prismplate_local.py scene.json --png out.png --svg out.svg
  python3 scripts/prismplate_local.py --selftest
"""
import json
import math
import struct
import sys
import zlib

# ---------------------------------------------------------------- geom


def flatten(ops):
    """与 geom.Flatten 一致：M/L/Q(16)/C(24)/Z 展开为点环。"""
    out = []
    ring = []
    at = (0.0, 0.0)

    def flush():
        nonlocal ring
        if len(ring) >= 3:
            out.append(ring)
        ring = []

    for o in ops:
        kind = o["kind"]
        pts = o.get("points") or []
        if kind == "M":
            flush()
            at = (float(pts[0]["x"]), float(pts[0]["y"]))
            ring.append(at)
        elif kind == "L":
            at = (float(pts[0]["x"]), float(pts[0]["y"]))
            ring.append(at)
        elif kind == "Q":
            start = at
            cx, cy = float(pts[0]["x"]), float(pts[0]["y"])
            ex, ey = float(pts[1]["x"]), float(pts[1]["y"])
            for i in range(1, 17):
                t = i / 16
                u = 1 - t
                ring.append((u * u * start[0] + 2 * u * t * cx + t * t * ex,
                             u * u * start[1] + 2 * u * t * cy + t * t * ey))
            at = (ex, ey)
        elif kind == "C":
            start = at
            ax, ay = float(pts[0]["x"]), float(pts[0]["y"])
            bx, by = float(pts[1]["x"]), float(pts[1]["y"])
            ex, ey = float(pts[2]["x"]), float(pts[2]["y"])
            for i in range(1, 25):
                t = i / 24
                u = 1 - t
                ring.append((u ** 3 * start[0] + 3 * u * u * t * ax + 3 * u * t * t * bx + t ** 3 * ex,
                             u ** 3 * start[1] + 3 * u * u * t * ay + 3 * u * t * t * by + t ** 3 * ey))
            at = (ex, ey)
        elif kind == "Z":
            flush()
        else:
            raise ValueError("unknown contour verb %r" % kind)
    flush()
    return out


def mat_apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def contains(rings, x, y, rule):
    """与 geom.Path.Contains 一致：+x 射线，交点 x 严格大于采样点。"""
    winding = 0
    crossings = 0
    for ring in rings:
        n = len(ring)
        for i in range(n):
            ax, ay = ring[i]
            bx, by = ring[(i + 1) % n]
            if (ay <= y < by) or (by <= y < ay):
                xi = ax + (y - ay) * (bx - ax) / (by - ay)
                if xi > x:
                    crossings += 1
                    if by > ay:
                        winding += 1
                    else:
                        winding -= 1
    if rule == "evenodd":
        return crossings % 2 == 1
    return winding != 0


# ---------------------------------------------------------------- paint


def _linear(v):
    if v <= 0.04045:
        return v / 12.92
    return ((v + 0.055) / 1.055) ** 2.4


def _srgb(v):
    v = max(0.0, min(1.0, v))
    if v <= 0.0031308:
        return v * 12.92
    return 1.055 * (v ** (1 / 2.4)) - 0.055


def color_of(c):
    return (_linear(c["r"]) * c["a"], _linear(c["g"]) * c["a"],
            _linear(c["b"]) * c["a"], c["a"])


def _scale(p, a):
    return (p[0] * a, p[1] * a, p[2] * a, p[3] * a)


def over(dst, src, mode):
    sa = src[3]
    da = dst[3]
    if mode == "normal" or sa == 0 or da == 0:
        return (src[0] + dst[0] * (1 - sa), src[1] + dst[1] * (1 - sa),
                src[2] + dst[2] * (1 - sa), sa + da * (1 - sa))
    out = []
    for k in range(3):
        a = dst[k] / da
        b = src[k] / sa
        v = a * b if mode == "multiply" else a + b - a * b
        out.append((1 - sa) * dst[k] + (1 - da) * src[k] + sa * da * v)
    return (out[0], out[1], out[2], sa + da * (1 - sa))


def sample_brush(brush, x, y):
    if brush["kind"] == "solid":
        return color_of(brush["color"])
    fx, fy = brush["from"]["x"], brush["from"]["y"]
    dx, dy = brush["to"]["x"] - fx, brush["to"]["y"] - fy
    t = ((x - fx) * dx + (y - fy) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    stops = brush["stops"]
    for i in range(1, len(stops)):
        a, z = stops[i - 1], stops[i]
        if t <= z["at"]:
            u = (t - a["at"]) / (z["at"] - a["at"])
            pa, pz = color_of(a["color"]), color_of(z["color"])
            return tuple(pa[k] + (pz[k] - pa[k]) * u for k in range(4))
    return color_of(stops[-1]["color"])


def rgba8(px):
    r, g, b, a = px
    if a <= 0:
        return (0, 0, 0, 0)

    def to8(v):
        return int(math.floor(v * 255 + 0.5))

    return (to8(_srgb(r / a)), to8(_srgb(g / a)), to8(_srgb(b / a)),
            int(math.floor(min(1.0, a) * 255 + 0.5)))


# ---------------------------------------------------------------- compile


def compile_doc(doc):
    """展开路径、预变换到画布坐标、预算校验（与 validate.Compile 同口径）。"""
    W, H = doc["width"], doc["height"]
    if not (1 <= W <= 512 and 1 <= H <= 512):
        raise ValueError("invalid canvas (maximum 512 square)")
    layers = []
    segments = 0
    ids = set()

    def claim(i):
        if not i or not isinstance(i, str) or i in ids:
            raise ValueError("invalid or duplicate node id %r" % i)
        ids.add(i)

    def prep(ops):
        nonlocal segments
        rings = flatten(ops)
        for r in rings:
            segments += len(r)
        return rings

    for lay in doc["layers"]:
        claim(lay["id"])
        if lay["blend"] not in ("normal", "multiply", "screen"):
            raise ValueError("invalid layer compositing")
        shapes = []
        for s in lay["shapes"]:
            claim(s["id"])
            m = s["transform"]
            det = m[0] * m[3] - m[1] * m[2]
            if abs(det) < 1e-10:
                raise ValueError("singular transform")
            rings = prep(s["path"])
            crings = [[mat_apply(m, px, py) for px, py in ring] for ring in rings]
            xs = [p[0] for ring in crings for p in ring]
            ys = [p[1] for ring in crings for p in ring]
            shapes.append({
                "rings": crings, "rule": s["fill_rule"], "brush": s["brush"],
                "solid": s["brush"]["kind"] == "solid",
                "const": color_of(s["brush"]["color"]) if s["brush"]["kind"] == "solid" else None,
                "bbox": (min(xs), min(ys), max(xs), max(ys)),
            })
        clip = prep(lay["clip"]) if lay.get("clip") else None
        mask = prep(lay["mask"]) if lay.get("mask") else None
        layers.append({"opacity": lay["opacity"], "blend": lay["blend"],
                       "clip": clip, "mask": mask, "shapes": shapes})
    if segments * W * H * 4 > 160_000_000:
        raise ValueError("scene exceeds bounded rendering work")
    return {"W": W, "H": H, "bg": color_of(doc["background"]),
            "layers": layers, "segments": segments}


# ---------------------------------------------------------------- raster (scanline)


def _crossings(rings, y):
    """某一子采样行上，全部环的水平交点：返回 (排序后的x列表, 后缀环绕数和)。"""
    xs = []
    ds = []
    for ring in rings:
        n = len(ring)
        for i in range(n):
            ax, ay = ring[i]
            bx, by = ring[(i + 1) % n]
            if (ay <= y < by) or (by <= y < ay):
                xs.append(ax + (y - ay) * (bx - ax) / (by - ay))
                ds.append(1 if by > ay else -1)
    order = sorted(range(len(xs)), key=lambda k: xs[k])
    xs = [xs[k] for k in order]
    ds = [ds[k] for k in order]
    suffix = [0] * (len(xs) + 1)
    for i in range(len(xs) - 1, -1, -1):
        suffix[i] = suffix[i + 1] + ds[i]
    return xs, suffix, len(xs)


def _state(xsx, suffix, total, x, rule):
    """等效 contains()：x>p 的交点决定环绕数/奇偶。"""
    import bisect
    idx = bisect.bisect_right(xsx, x)
    if rule == "evenodd":
        return (total - idx) % 2 == 1
    return suffix[idx] != 0


def render_pixels(comp):
    """逐像素 2x2 四点覆盖采样，返回 RGBA 字节行列表。"""
    W, H = comp["W"], comp["H"]
    rows = []
    # 预收集每层的环集，便于整行扫描
    for row in range(H):
        acc = [[0.0, 0.0, 0.0, 0.0] for _ in range(W)]
        for sy in range(2):
            y = row + (sy + 0.5) / 2
            # 每个环集在本行的交点
            per_layer = []
            allx = []
            for lay in comp["layers"]:
                gates = []
                for rings, rule in ((lay["clip"], "nonzero"), (lay["mask"], "nonzero")):
                    if rings:
                        xs, suffix, total = _crossings(rings, y)
                        gates.append((xs, suffix, total, rule))
                        allx.extend(x for x in xs if 0.0 < x < W)
                shs = []
                for sh in lay["shapes"]:
                    bx0, by0, bx1, by1 = sh["bbox"]
                    if by0 <= y <= by1:
                        xs, suffix, total = _crossings(sh["rings"], y)
                        shs.append((xs, suffix, total, sh["rule"], sh))
                        allx.extend(x for x in xs if 0.0 < x < W)
                    else:
                        shs.append(None)
                per_layer.append((lay, gates, shs))
            bounds = sorted(set([0.0, float(W)] + allx))
            # 逐区间求合成色，再分配给区间内的子采样点
            intervals = []  # (x0, x1, color or None->perpoint)
            for bi in range(len(bounds) - 1):
                xa, xb = bounds[bi], bounds[bi + 1]
                if xb <= 0.0 or xa >= W:
                    continue
                mid = (xa + xb) / 2
                dst = comp["bg"]
                perpoint = False
                for lay, gates, shs in per_layer:
                    inside = True
                    for xs, suffix, total, rule in gates:
                        if not _state(xs, suffix, total, mid, rule):
                            inside = False
                            break
                    if not inside:
                        continue
                    src = (0.0, 0.0, 0.0, 0.0)
                    for item in shs:
                        if item is None:
                            continue
                        xs, suffix, total, rule, sh = item
                        if _state(xs, suffix, total, mid, rule):
                            if sh["solid"]:
                                src = over(src, sh["const"], "normal")
                            else:
                                perpoint = True
                                src = over(src, sample_brush(sh["brush"], mid, y), "normal")
                    dst = over(dst, _scale(src, lay["opacity"]), lay["blend"])
                intervals.append((xa, xb, None if perpoint else dst))
            # 子采样列分配：x = i + 0.25 / i + 0.75
            ii = 0
            for i in range(W):
                for sx in (0.25, 0.75):
                    x = i + sx
                    while ii < len(intervals) - 1 and x >= intervals[ii][1]:
                        ii += 1
                    xa, xb, col = intervals[ii]
                    if not (xa <= x < xb):
                        continue
                    if col is None:
                        # 含渐变：逐点重算
                        dst = comp["bg"]
                        for lay, gates, shs in per_layer:
                            inside = True
                            for xs, suffix, total, rule in gates:
                                if not _state(xs, suffix, total, x, rule):
                                    inside = False
                                    break
                            if not inside:
                                continue
                            src = (0.0, 0.0, 0.0, 0.0)
                            for item in shs:
                                if item is None:
                                    continue
                                xs, suffix, total, rule, sh = item
                                if _state(xs, suffix, total, x, rule):
                                    src = over(src, sample_brush(sh["brush"], x, y), "normal")
                            dst = over(dst, _scale(src, lay["opacity"]), lay["blend"])
                        col = dst
                    a = acc[i]
                    a[0] += col[0] / 4
                    a[1] += col[1] / 4
                    a[2] += col[2] / 4
                    a[3] += col[3] / 4
        row_bytes = bytearray()
        for i in range(W):
            row_bytes += bytes(rgba8(acc[i]))
        rows.append(bytes(row_bytes))
    return rows


def encode_png(W, H, rows):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    raw = b"".join(b"\x00" + r for r in rows)
    ihdr = struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def render_png(doc):
    comp = compile_doc(doc)
    return encode_png(comp["W"], comp["H"], render_pixels(comp)), comp["segments"]


# ---------------------------------------------------------------- vector


def _num(x):
    return format(x, ".9g")


def _rgb(c):
    return "rgb(%s%%,%s%%,%s%%)" % (_num(c["r"] * 100), _num(c["g"] * 100), _num(c["b"] * 100))


def _path_d(rings):
    parts = []
    for ring in rings:
        for i, (x, y) in enumerate(ring):
            parts.append(("M" if i == 0 else "L") + _num(x) + " " + _num(y))
        parts.append("Z")
    return "".join(parts)


def export_svg(doc):
    """与 vector.Export 同结构：复用展开几何（局部坐标 + 矩阵变换）。"""
    W, H = doc["width"], doc["height"]
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" color-interpolation="linearRGB"><defs>' % (W, H, W, H)]
    for i, lay in enumerate(doc["layers"]):
        if lay.get("clip"):
            out.append('<clipPath id="c%d" clipPathUnits="userSpaceOnUse"><path d="%s"/></clipPath>' % (i, _path_d(flatten(lay["clip"]))))
        if lay.get("mask"):
            out.append('<clipPath id="m%d" clipPathUnits="userSpaceOnUse"><path d="%s"/></clipPath>' % (i, _path_d(flatten(lay["mask"]))))
        for j, s in enumerate(lay["shapes"]):
            br = s["brush"]
            if br["kind"] == "linear":
                out.append('<linearGradient id="b%d_%d" gradientUnits="userSpaceOnUse" x1="%s" y1="%s" x2="%s" y2="%s">' % (i, j, _num(br["from"]["x"]), _num(br["from"]["y"]), _num(br["to"]["x"]), _num(br["to"]["y"])))
                for st in br["stops"]:
                    out.append('<stop offset="%s" stop-color="%s" stop-opacity="%s"/>' % (_num(st["at"]), _rgb(st["color"]), _num(st["color"]["a"])))
                out.append('</linearGradient>')
    out.append('</defs>')
    bg = doc["background"]
    out.append('<rect width="100%%" height="100%%" fill="%s" fill-opacity="%s"/>' % (_rgb(bg), _num(bg["a"])))
    for i, lay in enumerate(doc["layers"]):
        out.append('<g opacity="%s" style="isolation:isolate;mix-blend-mode:%s"' % (_num(lay["opacity"]), lay["blend"]))
        if lay.get("clip"):
            out.append(' clip-path="url(#c%d)"' % i)
        out.append('><g')
        if lay.get("mask"):
            out.append(' clip-path="url(#m%d)"' % i)
        out.append('>')
        for j, s in enumerate(lay["shapes"]):
            br = s["brush"]
            if br["kind"] == "linear":
                fill = "url(#b%d_%d)" % (i, j)
                alpha = 1
            else:
                fill = _rgb(br["color"])
                alpha = br["color"]["a"]
            m = s["transform"]
            out.append('<path d="%s" fill="%s" fill-opacity="%s" fill-rule="%s" transform="matrix(%s %s %s %s %s %s)"/>' % (
                _path_d(flatten(s["path"])), fill, _num(alpha), s["fill_rule"],
                _num(m[0]), _num(m[1]), _num(m[2]), _num(m[3]), _num(m[4]), _num(m[5])))
        out.append('</g></g>')
    out.append('</svg>')
    return "".join(out).encode("utf-8")


# ---------------------------------------------------------------- selftest


def selftest():
    # flatten：Q=16 段、C=24 段
    r = flatten([{"kind": "M", "points": [{"x": 0, "y": 0}]},
                 {"kind": "Q", "points": [{"x": 5, "y": 0}, {"x": 10, "y": 0}]},
                 {"kind": "L", "points": [{"x": 0, "y": 0}]}, {"kind": "Z"}])
    assert len(r) == 1 and len(r[0]) == 18, r
    # contains：方框内外与边界
    sq = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]]
    assert contains(sq, 5, 5, "nonzero") and not contains(sq, 15, 5, "nonzero")
    assert not contains(sq, 10, 5, "nonzero")  # 边界点不计（x 严格大于）
    # evenodd：同心双方框，中心空心
    donut = sq + [[(2.0, 2.0), (8.0, 2.0), (8.0, 8.0), (2.0, 8.0)]]
    assert not contains(donut, 5, 5, "evenodd") and contains(donut, 1, 1, "evenodd")
    # over：normal 半透明黑压白
    dst = color_of({"r": 1, "g": 1, "b": 1, "a": 1})
    src = color_of({"r": 0, "g": 0, "b": 0, "a": 0.5})
    got = over(dst, src, "normal")
    assert abs(got[0] - 0.5) < 1e-12 and got[3] == 1.0
    # multiply：线性光下逐通道相乘
    red = color_of({"r": 1, "g": 0, "b": 0, "a": 1})
    blu = color_of({"r": 0, "g": 0, "b": 1, "a": 1})
    got = over(red, blu, "multiply")
    assert abs(got[0]) < 1e-12 and abs(got[2]) < 1e-12 and got[3] == 1.0
    # 小场景：4x4 画布，中央 2x2 黑方块
    doc = {"width": 4, "height": 4,
           "background": {"r": 1, "g": 1, "b": 1, "a": 1},
           "layers": [{"id": "lay", "opacity": 1, "blend": "normal", "shapes": [
               {"id": "box", "fill_rule": "nonzero",
                "path": [{"kind": "M", "points": [{"x": 1, "y": 1}]},
                         {"kind": "L", "points": [{"x": 3, "y": 1}]},
                         {"kind": "L", "points": [{"x": 3, "y": 3}]},
                         {"kind": "L", "points": [{"x": 1, "y": 3}]}, {"kind": "Z"}],
                "brush": {"kind": "solid", "color": {"r": 0, "g": 0, "b": 0, "a": 1}},
                "transform": [1, 0, 0, 1, 0, 0]}]}]}
    comp = compile_doc(doc)
    rows = render_pixels(comp)
    px = lambda x, y: tuple(rows[y][x * 4:x * 4 + 4])
    assert px(0, 0) == (255, 255, 255, 255), px(0, 0)
    assert px(1, 1) == (0, 0, 0, 255) and px(2, 2) == (0, 0, 0, 255)
    assert px(3, 3) == (255, 255, 255, 255)
    # 图层不透明度：整层 0.5 黑压白 → 线性 0.5 → sRGB 约 188
    doc["layers"][0]["opacity"] = 0.5
    doc["layers"][0]["shapes"][0]["path"] = [
        {"kind": "M", "points": [{"x": 0, "y": 0}]},
        {"kind": "L", "points": [{"x": 4, "y": 0}]},
        {"kind": "L", "points": [{"x": 4, "y": 4}]},
        {"kind": "L", "points": [{"x": 0, "y": 4}]}, {"kind": "Z"}]
    rows = render_pixels(compile_doc(doc))
    v = rows[0][0]
    assert v in (187, 188) and rows[0][1] == v and rows[0][3] == 255, rows[0][:4]
    print("prismplate_local selftest passed")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit(0)
    src = sys.argv[1]
    doc = json.loads(open(src, encoding="utf-8").read())["document"] if "--plate" in sys.argv else json.loads(open(src, encoding="utf-8").read())
    if "--png" in sys.argv:
        png, segs = render_png(doc)
        open(sys.argv[sys.argv.index("--png") + 1], "wb").write(png)
        print("png segments=%d" % segs, file=sys.stderr)
    if "--svg" in sys.argv:
        open(sys.argv[sys.argv.index("--svg") + 1], "wb").write(export_svg(doc))
