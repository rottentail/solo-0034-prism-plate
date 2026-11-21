#!/usr/bin/env python3
"""棱版接点校样条生成器（纯标准库，可复跑）。

用途：分镜师要判断接点附近看起来的减速来自原轨迹还是取样。本脚本让带非对称
镂空的棱版图形沿两段相接的三次贝塞尔曲线移动，并交付两套 48 帧带时间标记的
画面：

  * ARC  版：按路程（弧长）近似匀速取样 —— 正式校样；
  * TIME 版：等参数 t 取样 —— 对照（画面看着平顺，但按弧长远不匀速）。

曲线设计（512×512 画布，y 轴向下）：
  * 段1：P0 P1 J J      —— 接点端一阶导数 3(P3-P2)=0；
  * 段2：J J Q2 Q3      —— 接点端一阶导数 3(Q1-Q0)=0；
  * 接点两侧行进方向一致：(J-P1) 与 (Q2-J) 平行同向；
  * 段2 有明显曲率变化（下压后急转上挑）；
  * 调参 mu 使 L1/(L1+L2)=24/47，因此 48 帧弧长等距且第 25 帧(i=24)正好落在接点；
  * 图形只旋转不平移镜像，接点处朝向取极限方向，不会翻转。

渲染约定与仓库棱版引擎一致（internal/raster, internal/paint）：每像素 2x2 覆盖
采样、线性光合成、nonzero 填充、screen 叠色。PNG 为权威结果。

复跑：python3 proofs/src/generate_proofs.py
输出：proofs/out/{arc,time}/frame_XX.png、接触表、证据图、manifest.json。
"""

import hashlib
import json
import math
import os
import struct
import sys
import zlib
from array import array

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "proofs", "out")

W, H = 512, 512          # 交付画布
SS = 2                   # 每轴 2 倍采样（每像素 4 覆盖点，同 raster.go）
FRAMES = 48              # 总帧数
JOINT_FRAME = 24         # 落在接点的帧序号（0 基）
FPS = 24                 # 画面时间标记用帧率

# ---------------------------------------------------------------- 曲线几何
# 段1：P0 -> J，P2=P3=J 使 B1'(1)=0；段2：J -> Q3，Q0=Q1=J 使 B2'(0)=0。
P0 = (84.0, 128.0)
J = (256.0, 296.0)
# P1 取在 P0->J 连线 30% 处并沿法线外偏 18px：段1 曲率平缓，与段2 形成对照
_P01 = (J[0] - P0[0], J[1] - P0[1])
_L01 = math.hypot(_P01[0], _P01[1])
_UX, _UY = _P01[0] / _L01, _P01[1] / _L01
P1 = (P0[0] + 0.30 * _P01[0] + 18.0 * _UY, P0[1] + 0.30 * _P01[1] - 18.0 * _UX)
REACH = 0.85                       # Q2 = J + REACH*(J-P1)，保证离点方向一致
Q2 = (J[0] + REACH * (J[0] - P1[0]), J[1] + REACH * (J[1] - P1[1]))
Q3_DIR = (182.0, -146.0)           # Q3 = J + mu*Q3_DIR，mu 由弧长比调参决定
TARGET_RATIO = JOINT_FRAME / (FRAMES - 1)   # L1/L = 24/47


def bz(cp, t):
    u = 1.0 - t
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = cp
    return (u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3,
            u * u * u * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3)


def bz_d1(cp, t):
    u = 1.0 - t
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = cp
    return (3 * (u * u * (x1 - x0) + 2 * u * t * (x2 - x1) + t * t * (x3 - x2)),
            3 * (u * u * (y1 - y0) + 2 * u * t * (y2 - y1) + t * t * (y3 - y2)))


def bz_d2(cp, t):
    u = 1.0 - t
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = cp
    return (6 * (u * (x2 - 2 * x1 + x0) + t * (x3 - 2 * x2 + x1)),
            6 * (u * (y2 - 2 * y1 + y0) + t * (y3 - 2 * y2 + y1)))


def speed_fn(cp):
    def f(t):
        dx, dy = bz_d1(cp, t)
        return math.hypot(dx, dy)
    return f


def simpson(f, a, b, n):
    """复合辛普森积分，n 为偶数。"""
    if b <= a:
        return 0.0
    h = (b - a) / n
    s = f(a) + f(b)
    x = a
    for k in range(1, n):
        x = a + k * h
        s += (4.0 if k & 1 else 2.0) * f(x)
    return s * h / 3.0


def seg1_cp():
    return (P0, P1, J, J)


def seg2_cp(mu):
    q3 = (J[0] + mu * Q3_DIR[0], J[1] + mu * Q3_DIR[1])
    return (J, J, Q2, q3)


def tune_mu():
    """二分调参使 L1/(L1+L2)=24/47（弧长等距且第 25 帧落在接点的前提）。"""
    f1 = speed_fn(seg1_cp())
    l1 = simpson(f1, 0.0, 1.0, 4096)

    def ratio(mu):
        l2 = simpson(speed_fn(seg2_cp(mu)), 0.0, 1.0, 4096)
        return l1 / (l1 + l2)

    lo, hi = 0.30, 1.60
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if ratio(mid) > TARGET_RATIO:   # L2 偏小 -> 增大 mu
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


class ArcTable:
    """高分辨率累计弧长表：4096 区间，每区间 32 面板辛普森。"""

    N = 4096

    def __init__(self, cp):
        self.cp = cp
        f = speed_fn(cp)
        self.ts = [k / self.N for k in range(self.N + 1)]
        self.ss = [0.0]
        for k in range(self.N):
            a = self.ts[k]
            self.ss.append(self.ss[-1] + simpson(f, a, a + 1.0 / self.N, 32))
        self.total = self.ss[-1]

    def s_at(self, t):
        if t <= 0.0:
            return 0.0
        if t >= 1.0:
            return self.total
        k = int(t * self.N)
        if k >= self.N:
            k = self.N - 1
        return self.ss[k] + simpson(speed_fn(self.cp), self.ts[k], t, 16)

    def t_at(self, s):
        """按弧长反解参数 t（二分，单调）。"""
        if s <= 0.0:
            return 0.0
        if s >= self.total:
            return 1.0
        lo, hi = 0, self.N
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if self.ss[mid] <= s:
                lo = mid
            else:
                hi = mid
        a, b = self.ts[lo], self.ts[lo] + 1.0 / self.N
        base = self.ss[lo]
        f = speed_fn(self.cp)
        for _ in range(80):
            mid = 0.5 * (a + b)
            if base + simpson(f, self.ts[lo], mid, 16) < s:
                a = mid
            else:
                b = mid
        return 0.5 * (a + b)


# ---------------------------------------------------------------- 颜色与合成
# 与 internal/paint/paint.go 相同的线性光合成；目标不透明，故用直色形式。

def srgb_to_linear(v):
    if v <= 0.04045:
        return v / 12.92
    return ((v + 0.055) / 1.055) ** 2.4


def linear_to_srgb(v):
    if v <= 0.0:
        return 0.0
    if v >= 1.0:
        return 1.0
    if v <= 0.0031308:
        return v * 12.92
    return 1.055 * (v ** (1 / 2.4)) - 0.055


LUT8 = [min(255, max(0, int(round(linear_to_srgb(k / 4096.0) * 255.0)))) for k in range(4097)]


def lin(rgb):
    return (srgb_to_linear(rgb[0]), srgb_to_linear(rgb[1]), srgb_to_linear(rgb[2]))


def blend_normal(buf, i, c, a):
    buf[i] += (c[0] - buf[i]) * a
    buf[i + 1] += (c[1] - buf[i + 1]) * a
    buf[i + 2] += (c[2] - buf[i + 2]) * a


def blend_screen(buf, i, c, a):
    s0 = 1.0 - (1.0 - c[0]) * (1.0 - buf[i])
    s1 = 1.0 - (1.0 - c[1]) * (1.0 - buf[i + 1])
    s2 = 1.0 - (1.0 - c[2]) * (1.0 - buf[i + 2])
    buf[i] += (s0 - buf[i]) * a
    buf[i + 1] += (s1 - buf[i + 1]) * a
    buf[i + 2] += (s2 - buf[i + 2]) * a


# ---------------------------------------------------------------- 图形基元（2x 采样缓冲）

def point_in_poly(px, py, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > py) != (yj > py):
            if px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def fill_poly(buf, poly, color, alpha, mode="normal"):
    sw, sh = W * SS, H * SS
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0 = max(0, int(min(xs) * SS) - 1)
    x1 = min(sw - 1, int(max(xs) * SS) + 2)
    y0 = max(0, int(min(ys) * SS) - 1)
    y1 = min(sh - 1, int(max(ys) * SS) + 2)
    blend = blend_screen if mode == "screen" else blend_normal
    for sy in range(y0, y1 + 1):
        py = (sy + 0.5) / SS
        row = sy * sw * 3
        for sx in range(x0, x1 + 1):
            px = (sx + 0.5) / SS
            if point_in_poly(px, py, poly):
                blend(buf, row + sx * 3, color, alpha)


def fill_disc(buf, cx, cy, r, color, alpha):
    sw, sh = W * SS, H * SS
    x0 = max(0, int((cx - r) * SS) - 1)
    x1 = min(sw - 1, int((cx + r) * SS) + 2)
    y0 = max(0, int((cy - r) * SS) - 1)
    y1 = min(sh - 1, int((cy + r) * SS) + 2)
    r2 = r * r
    for sy in range(y0, y1 + 1):
        py = (sy + 0.5) / SS
        dy = py - cy
        row = sy * sw * 3
        for sx in range(x0, x1 + 1):
            px = (sx + 0.5) / SS
            dx = px - cx
            if dx * dx + dy * dy <= r2:
                blend_normal(buf, row + sx * 3, color, alpha)


def fill_ring(buf, cx, cy, r, wdt, color, alpha):
    sw, sh = W * SS, H * SS
    ro = r + wdt * 0.5
    ri = r - wdt * 0.5
    x0 = max(0, int((cx - ro) * SS) - 1)
    x1 = min(sw - 1, int((cx + ro) * SS) + 2)
    y0 = max(0, int((cy - ro) * SS) - 1)
    y1 = min(sh - 1, int((cy + ro) * SS) + 2)
    ro2, ri2 = ro * ro, ri * ri
    for sy in range(y0, y1 + 1):
        py = (sy + 0.5) / SS
        dy = py - cy
        row = sy * sw * 3
        for sx in range(x0, x1 + 1):
            px = (sx + 0.5) / SS
            dx = px - cx
            d2 = dx * dx + dy * dy
            if ri2 <= d2 <= ro2:
                blend_normal(buf, row + sx * 3, color, alpha)


# ---------------------------------------------------------------- 棱版图形（局部坐标，+x 朝行进方向）
OUTLINE = [(28.0, 0.0), (10.0, -20.0), (-14.0, -22.0), (-28.0, -8.0), (-22.0, 16.0), (2.0, 24.0)]
CENTROID = (-3.0, -1.5)
FACET_SHADE = [1.00, 0.78, 1.12, 0.88, 1.06, 0.84]
GRAD_A = (1.00, 0.70, 0.22)     # 琥珀（头部，同 examples/amber-proof.json）
GRAD_B = (0.90, 0.18, 0.14)     # 红（尾部）
RIM_COLOR = (0.42, 0.15, 0.09)
CUT_CIRCLE = (-7.0, -6.0, 5.0)                      # 镂空一：偏心圆孔
CUT_TRI = [(-28.0, -8.0), (-15.0, -1.0), (-23.0, 7.0)]  # 镂空二：尾缘三角缺口
_sa, _ca = math.sin(math.radians(25.0)), math.cos(math.radians(25.0))
_cx, _cy = 9.0, 9.0
_dx, _dy = (_ca * 4.5, _sa * 4.5), (-_sa * 1.8, _ca * 1.8)
CUT_SLOT = [(_cx + _dx[0] + _dy[0], _cy + _dx[1] + _dy[1]),
            (_cx + _dx[0] - _dy[0], _cy + _dx[1] - _dy[1]),
            (_cx - _dx[0] - _dy[0], _cy - _dx[1] - _dy[1]),
            (_cx - _dx[0] + _dy[0], _cy - _dx[1] + _dy[1])]   # 镂空三：斜置长槽
GLAZE = [(2.0, -16.0), (17.0, -9.0), (13.0, 3.0), (-3.0, -4.0)]  # 青色 screen 釉带
GLAZE_RGBA = ((0.10, 0.65, 0.84), 0.72)

FACETS = []
for _i in range(len(OUTLINE)):
    FACETS.append((CENTROID, OUTLINE[_i], OUTLINE[(_i + 1) % len(OUTLINE)]))


def facet_of(lx, ly):
    for k, tri in enumerate(FACETS):
        (ax, ay), (bx, by), (cx, cy) = tri
        d1 = (lx - bx) * (ay - by) - (ax - bx) * (ly - by)
        d2 = (lx - cx) * (by - cy) - (bx - cx) * (ly - cy)
        d3 = (lx - ax) * (cy - ay) - (cx - ax) * (ly - ay)
        if (d1 >= 0 and d2 >= 0 and d3 >= 0) or (d1 <= 0 and d2 <= 0 and d3 <= 0):
            return k
    return 0


def draw_plate(buf, pos, phi):
    """在 2x 缓冲上绘制旋转 phi、平移到 pos 的棱版。只旋转，不镜像。"""
    sw = W * SS
    cp_, sp_ = math.cos(phi), math.sin(phi)

    def to_world(p):
        return (pos[0] + p[0] * cp_ - p[1] * sp_, pos[1] + p[0] * sp_ + p[1] * cp_)

    rim = [((x - CENTROID[0]) * 1.08 + CENTROID[0], (y - CENTROID[1]) * 1.08 + CENTROID[1])
           for x, y in OUTLINE]
    world_rim = [to_world(p) for p in rim]
    xs = [p[0] for p in world_rim]
    ys = [p[1] for p in world_rim]
    x0 = max(0, int(min(xs) * SS) - 2)
    x1 = min(sw - 1, int(max(xs) * SS) + 3)
    y0 = max(0, int(min(ys) * SS) - 2)
    y1 = min(H * SS - 1, int(max(ys) * SS) + 3)
    ccx, ccy, ccr = CUT_CIRCLE
    cc_r2 = ccr * ccr
    rim_c = lin(RIM_COLOR)
    glaze_c = lin(GLAZE_RGBA[0])
    grad_a = GRAD_A
    grad_b = GRAD_B
    for sy in range(y0, y1 + 1):
        wy = (sy + 0.5) / SS - pos[1]
        row = sy * sw * 3
        for sx in range(x0, x1 + 1):
            wx = (sx + 0.5) / SS - pos[0]
            lx = wx * cp_ + wy * sp_
            ly = -wx * sp_ + wy * cp_
            # 镂空：直接透出底层（背景与路径）
            ddx = lx - ccx
            ddy = ly - ccy
            if ddx * ddx + ddy * ddy <= cc_r2:
                continue
            if point_in_poly(lx, ly, CUT_TRI) or point_in_poly(lx, ly, CUT_SLOT):
                continue
            i = row + sx * 3
            if point_in_poly(lx, ly, OUTLINE):
                t = (lx + 28.0) / 56.0
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                shade = FACET_SHADE[facet_of(lx, ly)]
                r = min(1.0, (grad_a[0] + (grad_b[0] - grad_a[0]) * t) * shade)
                g = min(1.0, (grad_a[1] + (grad_b[1] - grad_a[1]) * t) * shade)
                b = min(1.0, (grad_a[2] + (grad_b[2] - grad_a[2]) * t) * shade)
                blend_normal(buf, i, (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b)), 1.0)
                if point_in_poly(lx, ly, GLAZE):
                    blend_screen(buf, i, glaze_c, GLAZE_RGBA[1])
            elif point_in_poly(lx, ly, rim):
                blend_normal(buf, i, rim_c, 1.0)


# ---------------------------------------------------------------- 5x7 点阵字
FONT = {
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": (".###.", "#...#", "....#", "..##.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": ("..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "D": ("###..", "#..#.", "#...#", "#...#", "#...#", "#..#.", "###.."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".####"),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": (".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "J": ("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    ".": (".....", ".....", ".....", ".....", ".....", ".##..", ".##.."),
    ":": (".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."),
    "-": (".....", ".....", ".....", "#####", ".....", ".....", "....."),
    "+": (".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."),
    "/": ("....#", "....#", "...#.", "..#..", ".#...", "#....", "#...."),
    "%": ("##..#", "##.#.", "...#.", "..#..", ".#...", "#.##.", "#..##"),
    "=": (".....", ".....", "#####", ".....", "#####", ".....", "....."),
    ">": ("#....", ".#...", "..#..", "...#.", "..#..", ".#...", "#...."),
    "<": ("....#", "...#.", "..#..", ".#...", "..#..", "...#.", "....#"),
    "(": ("...#.", "..#..", ".#...", ".#...", ".#...", "..#..", "...#."),
    ")": (".#...", "..#..", "...#.", "...#.", "...#.", "..#..", ".#..."),
    "_": (".....", ".....", ".....", ".....", ".....", ".....", "#####"),
    " ": (".....", ".....", ".....", ".....", ".....", ".....", "....."),
}


def draw_text(buf, wpx, x, y, text, rgb, scale=1, shadow=True):
    if shadow:
        draw_text(buf, wpx, x + 1, y + 1, text, (8, 10, 16), scale, shadow=False)
    cx = x
    for ch in text:
        glyph = FONT.get(ch.upper(), FONT[" "])
        for gy, rowbits in enumerate(glyph):
            for gx, bit in enumerate(rowbits):
                if bit == "#":
                    for sy_ in range(scale):
                        for sx_ in range(scale):
                            px = cx + gx * scale + sx_
                            py = y + gy * scale + sy_
                            if 0 <= px < wpx and 0 <= py < len(buf) // (wpx * 3):
                                i = (py * wpx + px) * 3
                                buf[i] = rgb[0]
                                buf[i + 1] = rgb[1]
                                buf[i + 2] = rgb[2]
        cx += 6 * scale


# ---------------------------------------------------------------- PNG 编码（确定性）

def png_encode(rgb8, wpx, hpx):
    raw = bytearray()
    stride = wpx * 3
    for yy in range(hpx):
        raw.append(0)
        raw += rgb8[yy * stride:(yy + 1) * stride]
    comp = zlib.compress(bytes(raw), 6)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", wpx, hpx, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", comp) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- 场景常量
BG = (0.040, 0.055, 0.090)          # 同 examples/amber-proof.json 背景
PATH_C = (0.45, 0.56, 0.72)
TICK_C = (0.90, 0.92, 0.95)
JOINT_C = (1.00, 0.80, 0.45)
GHOST_C = (1.00, 0.92, 0.65)
HUD_C = (224, 228, 236)
AMBER_8 = (255, 178, 56)
CYAN_8 = (64, 190, 230)


def build_static(tab1, tab2, frames, dense1, dense2):
    """版本相关的静态底层：背景、路径、方向箭头、取样刻度、接点标记。"""
    buf = array("f", [srgb_to_linear(BG[0]), srgb_to_linear(BG[1]), srgb_to_linear(BG[2])]
                * (W * SS * H * SS))
    path_c = lin(PATH_C)
    for dense in (dense1, dense2):
        for (px, py) in dense:
            fill_disc(buf, px, py, 1.1, path_c, 0.50)
    # 方向箭头：沿弧长均布 5 枚，指示行进方向
    total = tab1.total + tab2.total
    for k in range(5):
        s = total * (k + 0.5) / 5.0
        if abs(s - tab1.total) < 16.0:
            continue
        if s <= tab1.total:
            t = tab1.t_at(s)
            pos = bz(seg1_cp(), t)
            d = bz_d1(seg1_cp(), t)
        else:
            t = tab2.t_at(s - tab1.total)
            pos = bz(seg2_cp(MU), t)
            d = bz_d1(seg2_cp(MU), t)
        n = math.hypot(d[0], d[1])
        if n < 1e-9:
            continue
        ux, uy = d[0] / n, d[1] / n
        tip = (pos[0] + 6.5 * ux, pos[1] + 6.5 * uy)
        bl = (pos[0] - 2.5 * ux - 3.2 * uy, pos[1] - 2.5 * uy + 3.2 * ux)
        br = (pos[0] - 2.5 * ux + 3.2 * uy, pos[1] - 2.5 * uy - 3.2 * ux)
        fill_poly(buf, [tip, bl, br], path_c, 0.85)
    # 取样刻度：每一帧位置一个小点（ARC 版均匀、TIME 版接点处密集）
    tick_c = lin(TICK_C)
    for fr in frames:
        fill_disc(buf, fr["x"], fr["y"], 1.4, tick_c, 0.55)
    # 端点与接点
    for p in (P0, bz(seg2_cp(MU), 1.0)):
        fill_poly(buf, [(p[0] - 2.5, p[1] - 2.5), (p[0] + 2.5, p[1] - 2.5),
                        (p[0] + 2.5, p[1] + 2.5), (p[0] - 2.5, p[1] + 2.5)], path_c, 0.9)
    joint_c = lin(JOINT_C)
    fill_ring(buf, J[0], J[1], 5.5, 1.4, joint_c, 0.95)
    fill_disc(buf, J[0], J[1], 1.6, joint_c, 0.95)
    return buf


def downsample_to_8bit(buf):
    """2x 线性光缓冲 -> 1x sRGB8（2x2 盒式平均，同 raster.go 四点覆盖）。"""
    out = bytearray(W * H * 3)
    sw = W * SS
    lut = LUT8
    for y in range(H):
        r0 = (2 * y) * sw * 3
        r1 = (2 * y + 1) * sw * 3
        orow = y * W * 3
        for x in range(W):
            c = 2 * x * 3
            i0, i1 = r0 + c, r1 + c
            o = orow + x * 3
            out[o] = lut[int((buf[i0] + buf[i0 + 3] + buf[i1] + buf[i1 + 3]) * 1024.0 + 0.5)]
            out[o + 1] = lut[int((buf[i0 + 1] + buf[i0 + 4] + buf[i1 + 1] + buf[i1 + 4]) * 1024.0 + 0.5)]
            out[o + 2] = lut[int((buf[i0 + 2] + buf[i0 + 5] + buf[i1 + 2] + buf[i1 + 5]) * 1024.0 + 0.5)]
    return out


def render_frame(static, frames, idx, mode_label, l_total):
    fr = frames[idx]
    buf = static[:]                      # 拷贝静态底层
    ghost_c = lin(GHOST_C)
    for k in range(1, 7):                # 残影：前 6 帧位置，间距肉眼可辨
        j = idx - k
        if j < 0:
            break
        fill_disc(buf, frames[j]["x"], frames[j]["y"], 2.0, ghost_c, 0.30 * (1.0 - k / 7.0))
    if idx == JOINT_FRAME:               # 接点帧高亮
        fill_ring(buf, fr["x"], fr["y"], 11.0, 1.6, lin(JOINT_C), 0.95)
    draw_plate(buf, (fr["x"], fr["y"]), math.radians(fr["phi_deg"]))
    img = downsample_to_8bit(buf)
    # HUD：时间标记与量测
    draw_text(img, W, 10, 8, "PRISM-PLATE PROOF", HUD_C)
    draw_text(img, W, 10, 18, "MODE " + mode_label, AMBER_8 if mode_label.startswith("ARC") else CYAN_8)
    tc = idx / FPS
    draw_text(img, W, 10, 30, "F %02d/%02d T %7.4f TC %02d:%02d" % (
        idx, FRAMES - 1, idx / (FRAMES - 1), int(tc), round((tc - int(tc)) * FPS)), HUD_C)
    draw_text(img, W, 10, 40, "S %6.1fPX HDG %+7.2f" % (fr["s_arc"], fr["heading_deg"]), HUD_C)
    if idx > 0:
        p0 = frames[idx - 1]
        ds = math.hypot(fr["x"] - p0["x"], fr["y"] - p0["y"])
        draw_text(img, W, 10, 50, "DS %6.2fPX" % ds, HUD_C)
    else:
        draw_text(img, W, 10, 50, "DS --", HUD_C)
    if idx == JOINT_FRAME:
        draw_text(img, W, 10, 62, "JOINT HIT", AMBER_8)
    # 底部进度条：白=帧时间 T，琥珀=路程 S/L（ARC 版二者重合，TIME 版分离）
    bx0, bx1, by = 24, 488, 500
    for xx in range(bx0, bx1 + 1):
        for yy in range(by, by + 5):
            i = (yy * W + xx) * 3
            img[i], img[i + 1], img[i + 2] = 26, 32, 44
    sx = bx0 + (bx1 - bx0) * (fr["s_arc"] / l_total)
    for xx in range(int(sx) - 1, int(sx) + 2):
        for yy in range(by, by + 5):
            if 0 <= xx < W:
                i = (yy * W + xx) * 3
                img[i], img[i + 1], img[i + 2] = AMBER_8
    tx = bx0 + (bx1 - bx0) * (idx / (FRAMES - 1))
    for xx in range(int(tx), int(tx) + 1):
        for yy in range(by + 1, by + 4):
            if 0 <= xx < W:
                i = (yy * W + xx) * 3
                img[i], img[i + 1], img[i + 2] = 240, 244, 250
    draw_text(img, W, 8, 499, "S", AMBER_8, shadow=False)
    draw_text(img, W, 16, 499, "T", (240, 244, 250), shadow=False)
    return img


# ---------------------------------------------------------------- 证据图与接触表

def plot_panel(img, wpx, box, title, series, ymin, ymax, mean_y=None, joint_x=None):
    x0, y0, x1, y1 = box
    for xx in range(x0, x1 + 1):
        for yy in (y0, y1):
            i = (yy * wpx + xx) * 3
            img[i:i + 3] = bytes((70, 78, 96))
    for yy in range(y0, y1 + 1):
        for xx in (x0, x1):
            i = (yy * wpx + xx) * 3
            img[i:i + 3] = bytes((70, 78, 96))

    def xy(k, n, v):
        px = x0 + (x1 - x0) * (k / max(1, n - 1))
        py = y1 - (y1 - y0) * ((v - ymin) / (ymax - ymin))
        return px, py

    if mean_y is not None:
        _, my = xy(0, 1, mean_y)
        for xx in range(x0, x1 + 1, 4):
            i = (int(my) * wpx + xx) * 3
            img[i:i + 3] = bytes((96, 104, 124))
    if joint_x is not None:
        jx, _ = xy(joint_x[0], joint_x[1], ymin)
        for yy in range(y0, y1 + 1, 3):
            i = (yy * wpx + int(jx)) * 3
            img[i:i + 3] = bytes((150, 120, 60))
    for values, color in series:
        prev = None
        n = len(values)
        for k, v in enumerate(values):
            v = min(ymax, max(ymin, v))
            px, py = xy(k, n, v)
            if prev is not None:
                steps = int(max(abs(px - prev[0]), abs(py - prev[1]))) + 1
                for s in range(steps + 1):
                    ix = int(prev[0] + (px - prev[0]) * s / steps)
                    iy = int(prev[1] + (py - prev[1]) * s / steps)
                    if 0 <= ix < wpx and 0 <= iy < len(img) // (wpx * 3):
                        i = (iy * wpx + ix) * 3
                        img[i:i + 3] = bytes(color)
            prev = (px, py)
    draw_text(img, wpx, x0 + 4, y0 + 4, title, HUD_C)


def build_evidence_chart(frames_arc, frames_time, tab1, tab2):
    wpx, hpx = 512, 600
    img = bytearray(wpx * hpx * 3)
    bg8 = [int(round(c * 255)) for c in BG]
    for i in range(0, len(img), 3):
        img[i], img[i + 1], img[i + 2] = bg8

    def chords(frames):
        out = []
        for k in range(1, len(frames)):
            a, b = frames[k - 1], frames[k]
            out.append(math.hypot(b["x"] - a["x"], b["y"] - a["y"]))
        return out

    ds_a, ds_t = chords(frames_arc), chords(frames_time)
    ymax = max(max(ds_a), max(ds_t)) * 1.15
    mean = (tab1.total + tab2.total) / (FRAMES - 1)
    plot_panel(img, wpx, (40, 24, 504, 190), "DS CHORD PX PER INTERVAL (AMBER=ARC CYAN=TIME)",
               [(ds_a, AMBER_8), (ds_t, CYAN_8)], 0.0, ymax, mean_y=mean, joint_x=(23, 47))
    draw_text(img, wpx, 40, 196, "MEAN %5.2fPX" % mean, HUD_C)

    hd_a = [f["heading_deg"] for f in frames_arc]
    hd_t = [f["heading_deg"] for f in frames_time]
    hmin = min(min(hd_a), min(hd_t)) - 5
    hmax = max(max(hd_a), max(hd_t)) + 5
    plot_panel(img, wpx, (40, 224, 504, 360), "HEADING DEG PER FRAME (NO FLIP)",
               [(hd_a, AMBER_8), (hd_t, CYAN_8)], hmin, hmax, joint_x=(24, 48))

    # 曲率沿路程分布：接点处几何曲率发散（|s-sJ|^-1/2），面板按可读上限截断
    total = tab1.total + tab2.total
    samples = []
    for src, tab, cp in ((0, tab1, seg1_cp()), (1, tab2, seg2_cp(MU))):
        off = tab1.total if src else 0.0
        for k in range(1, 240):
            t = 0.002 + 0.996 * k / 240
            d1 = bz_d1(cp, t)
            d2 = bz_d2(cp, t)
            sp = math.hypot(d1[0], d1[1])
            kap = abs(d1[0] * d2[1] - d1[1] * d2[0]) / (sp ** 3)
            samples.append((off + tab.s_at(t), kap))
    samples.sort()
    nb = 240
    grid = []
    si = 0
    for b in range(nb):
        s = total * (b + 0.5) / nb
        while si + 1 < len(samples) and samples[si + 1][0] < s:
            si += 1
        if si + 1 < len(samples):
            s0, k0 = samples[si]
            s1, k1 = samples[si + 1]
            w = 0.0 if s1 == s0 else (s - s0) / (s1 - s0)
            grid.append(k0 + (k1 - k0) * w)
        else:
            grid.append(samples[-1][1])
    kcap = 0.055
    plot_panel(img, wpx, (40, 408, 504, 560), "CURVATURE 1/PX VS S (CLIP %.3F)" % kcap,
               [(grid, AMBER_8)], 0.0, kcap, joint_x=(nb * tab1.total / total - 0.5, nb))
    draw_text(img, wpx, 40, 566, "VERT MARK = JOINT  S=0..%dPX" % total, HUD_C)
    return img


def contact_sheet(frames_img, path):
    cols, rows, cell = 8, 6, 128
    gap = 2
    wpx = cols * cell + (cols + 1) * gap
    hpx = rows * cell + (rows + 1) * gap
    img = bytearray(wpx * hpx * 3)
    for i in range(0, len(img), 3):
        img[i], img[i + 1], img[i + 2] = 10, 13, 22
    for idx, src in enumerate(frames_img):
        cx = gap + (idx % cols) * (cell + gap)
        cy = gap + (idx // cols) * (cell + gap)
        for ty in range(cell):
            for tx in range(cell):
                acc = [0, 0, 0]
                for sy_ in range(4):
                    for sx_ in range(4):
                        i = ((ty * 4 + sy_) * W + (tx * 4 + sx_)) * 3
                        acc[0] += src[i]
                        acc[1] += src[i + 1]
                        acc[2] += src[i + 2]
                o = ((cy + ty) * wpx + (cx + tx)) * 3
                img[o] = acc[0] // 16
                img[o + 1] = acc[1] // 16
                img[o + 2] = acc[2] // 16
        draw_text(img, wpx, cx + 3, cy + 3, "F%02d" % idx, (250, 240, 220))
    with open(path, "wb") as f:
        f.write(png_encode(img, wpx, hpx))


# ---------------------------------------------------------------- 主流程

def unwrap(headings):
    out = [headings[0]]
    for hd in headings[1:]:
        prev = out[-1]
        while hd - prev > 180.0:
            hd -= 360.0
        while hd - prev < -180.0:
            hd += 360.0
        out.append(hd)
    return out


def heading_at(seg, t):
    """接点处一阶导数为零，朝向取极限方向（两侧一致，不翻转）。"""
    if seg == 1:
        if t >= 1.0 - 1e-12:
            d = (J[0] - P1[0], J[1] - P1[1])
        else:
            d = bz_d1(seg1_cp(), t)
    else:
        if t <= 1e-12:
            d = (Q2[0] - J[0], Q2[1] - J[1])
        else:
            d = bz_d1(seg2_cp(MU), t)
    return math.degrees(math.atan2(d[1], d[0]))


def build_frames(tab1, tab2, mode):
    total = tab1.total + tab2.total
    frames = []
    for i in range(FRAMES):
        if mode == "arc":
            s = total * i / (FRAMES - 1)
            if abs(s - tab1.total) < 1e-9 * total:
                seg, t = 2, 0.0
            elif s < tab1.total:
                seg, t = 1, tab1.t_at(s)
            else:
                seg, t = 2, tab2.t_at(s - tab1.total)
        else:
            if i <= JOINT_FRAME:
                seg, t = 1, i / JOINT_FRAME
                if i == JOINT_FRAME:
                    seg, t = 2, 0.0
            else:
                seg, t = 2, (i - JOINT_FRAME) / (FRAMES - 1 - JOINT_FRAME)
        if seg == 2 and t == 0.0:
            x, y = J                      # 接点帧：位置精确落在 J
            s_arc = tab1.total
        elif seg == 1:
            x, y = bz(seg1_cp(), t)
            s_arc = tab1.s_at(t)
        else:
            x, y = bz(seg2_cp(MU), t)
            s_arc = tab1.total + tab2.s_at(t)
        frames.append({"i": i, "seg": seg, "t": t, "s_arc": s_arc,
                       "x": x, "y": y, "heading_raw": heading_at(seg, t),
                       "time_norm": i / (FRAMES - 1)})
    heads = unwrap([f["heading_raw"] for f in frames])
    h0 = heads[0]
    for f, hd in zip(frames, heads):
        f["heading_deg"] = hd
        f["phi_deg"] = hd - h0          # 渲染转角：首帧为正立，只旋转不镜像
        del f["heading_raw"]
    return frames


def main():
    global MU
    os.makedirs(os.path.join(OUT, "arc"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "time"), exist_ok=True)

    MU = tune_mu()
    tab1 = ArcTable(seg1_cp())
    tab2 = ArcTable(seg2_cp(MU))
    total = tab1.total + tab2.total
    print("mu=%.12f  L1=%.6f  L2=%.6f  L=%.6f  L1/L=%.12f (target %.12f)"
          % (MU, tab1.total, tab2.total, total, tab1.total / total, TARGET_RATIO))

    frames_arc = build_frames(tab1, tab2, "arc")
    frames_time = build_frames(tab1, tab2, "time")

    dense1 = [bz(seg1_cp(), k / 2400.0) for k in range(2401)]
    dense2 = [bz(seg2_cp(MU), k / 2400.0) for k in range(2401)]
    static_arc = build_static(tab1, tab2, frames_arc, dense1, dense2)
    static_time = build_static(tab1, tab2, frames_time, dense1, dense2)

    manifest = {
        "canvas": [W, H],
        "fps": FPS,
        "frames_total": FRAMES,
        "joint_frame": JOINT_FRAME,
        "generator": "proofs/src/generate_proofs.py",
        "curve": {
            "seg1": {"P0": P0, "P1": P1, "P2": J, "P3": J},
            "seg2": {"Q0": J, "Q1": J, "Q2": Q2, "Q3": seg2_cp(MU)[3]},
            "joint": J,
            "note": "B1'(1)=3(P3-P2)=0, B2'(0)=3(Q1-Q0)=0; "
                    "(J-P1) parallel same-sense (Q2-J); seg2 has strong curvature swing",
        },
        "tuning": {"target_L1_over_L": TARGET_RATIO, "mu": MU,
                   "L1": tab1.total, "L2": tab2.total, "L": total,
                   "achieved_L1_over_L": tab1.total / total},
        "versions": {"arc": frames_arc, "time": frames_time},
        "png_sha256": {},
    }

    sheets = {}
    for mode, frames, static in (("arc", frames_arc, static_arc),
                                 ("time", frames_time, static_time)):
        label = "ARC-LENGTH" if mode == "arc" else "EQUAL-TIME"
        imgs = []
        for idx in range(FRAMES):
            img = render_frame(static, frames, idx, label, total)
            data = png_encode(img, W, H)
            rel = "%s/frame_%02d.png" % (mode, idx)
            with open(os.path.join(OUT, rel), "wb") as f:
                f.write(data)
            manifest["png_sha256"][rel] = hashlib.sha256(data).hexdigest()
            imgs.append(img)
        sheets[mode] = imgs
        print("rendered %s x%d" % (mode, FRAMES))

    contact_sheet(sheets["arc"], os.path.join(OUT, "contact_arc.png"))
    contact_sheet(sheets["time"], os.path.join(OUT, "contact_time.png"))
    chart = build_evidence_chart(frames_arc, frames_time, tab1, tab2)
    with open(os.path.join(OUT, "evidence_chart.png"), "wb") as f:
        f.write(png_encode(chart, 512, 600))
    for rel in ("contact_arc.png", "contact_time.png", "evidence_chart.png"):
        with open(os.path.join(OUT, rel), "rb") as f:
            manifest["png_sha256"][rel] = hashlib.sha256(f.read()).hexdigest()

    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print("manifest.json written; frames at proofs/out/{arc,time}/")


MU = None
if __name__ == "__main__":
    main()
