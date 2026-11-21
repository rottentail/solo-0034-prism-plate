"""Solve recipe parameters so candidates share quantized codes on target
backgrounds, then emit final constants with verification tables.

Exact continuous-light identities used (d, s are straight linear-light values,
o = brush_alpha * opacity):
  normal:   y = (1-o)d + o s
  multiply: y = d [1 - o(1-s)]
  screen:   y = d + o s (1-d)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model

D = model.linear(0.5)  # mid-gray linear reference


def lin(rgb):
    return tuple(model.linear(v) for v in rgb)


def s8(v_lin):
    """8-bit sRGB channel code from a straight linear-light value."""
    return model.round8(model.srgb(v_lin))


def codes_normal(s_lin, o, d):
    return tuple(s8((1 - o) * d + o * s) for s in s_lin)


def codes_mult(s_lin, o, d):
    return tuple(s8(d * (1 - o * (1 - s))) for s in s_lin)


def codes_screen(s_lin, o, d):
    return tuple(s8(d + o * s * (1 - d)) for s in s_lin)


def nearest_srgb8(v_lin):
    """sRGB value rounded to 4 decimals whose linear value stays admissible."""
    return round(model.srgb(v_lin), 4)


def show(tag, target, rows):
    print(f"--- {tag} target codes {target}")
    for name, rgb, o, mode, got in rows:
        mark = "OK " if got == target else "XX "
        print(f"  {mark}{name:16s} o={o:<6} rgb={rgb} [{mode}] -> {got}")


# Family A: normal blend, match A1 (opaque) on mid-gray with two glazes.
a1 = lin((0.45, 0.60, 0.74))
o2, o3 = 0.70, 0.50
a2 = tuple((s - (1 - o2) * D) / o2 for s in a1)
a3 = tuple((s - (1 - o3) * D) / o3 for s in a1)
A = [
    ("A1-opaque", tuple(round(v, 4) for v in (0.45, 0.60, 0.74)), 1.00),
    ("A2-glaze70", tuple(nearest_srgb8(v) for v in a2), o2),
    ("A3-glaze50", tuple(nearest_srgb8(v) for v in a3), o3),
]
rows = []
for name, rgb, o in A:
    got = codes_normal(lin(rgb), o, D)
    rows.append((name, rgb, o, "normal", got))
show("Family A normal / mid-gray", codes_normal(a1, 1.0, D), rows)

# Family B: multiply, constant k = o(1-s), grayscale.
s1b, ob2, ob3 = 0.55, 0.75, 0.55
k = 1 - s1b
s2b, s3b = 1 - k / ob2, 1 - k / ob3
B = [
    ("B1-mult-full", (nearest_srgb8(s1b),) * 3, 1.00),
    ("B2-mult-glaze", (nearest_srgb8(s2b),) * 3, ob2),
    ("B3-mult-thin", (nearest_srgb8(s3b),) * 3, ob3),
]
rows = []
target_b = codes_mult((s1b,) * 3, 1.0, D)
for name, rgb, o in B:
    got = codes_mult(lin(rgb), o, D)
    rows.append((name, rgb, o, "multiply", got))
show("Family B multiply / mid-gray", target_b, rows)

# Family C: screen, constant k = o*s, grayscale.
s1c, oc2, oc3 = 0.35, 0.70, 0.50
kc = s1c
s2c, s3c = kc / oc2, kc / oc3
C = [
    ("C1-screen-full", (nearest_srgb8(s1c),) * 3, 1.00),
    ("C2-screen-glaze", (nearest_srgb8(s2c),) * 3, oc2),
    ("C3-screen-thin", (nearest_srgb8(s3c),) * 3, oc3),
]
rows = []
target_c = codes_screen((s1c,) * 3, 1.0, D)
for name, rgb, o in C:
    got = codes_screen(lin(rgb), o, D)
    rows.append((name, rgb, o, "screen", got))
show("Family C screen / mid-gray", target_c, rows)

# Family E-M: normal opaque dark color vs full-strength multiply, match mid-gray.
em_n = lin((0.28, 0.34, 0.42))
em_m = tuple(s / D for s in em_n)
EM = [
    ("EM1-normal", (0.28, 0.34, 0.42), 1.00, "normal"),
    ("EM2-multiply", tuple(nearest_srgb8(v) for v in em_m), 1.00, "multiply"),
]
rows = []
target_em = codes_normal(em_n, 1.0, D)
for name, rgb, o, mode in EM:
    f = codes_normal if mode == "normal" else codes_mult
    rows.append((name, rgb, o, mode, f(lin(rgb), o, D)))
show("Family EM normal-vs-multiply / mid-gray", target_em, rows)

# Family E-S: normal opaque light color vs full-strength screen, match mid-gray.
es_n = lin((0.62, 0.70, 0.80))
es_s = tuple((s - D) / (1 - D) for s in es_n)
ES = [
    ("ES1-normal", (0.62, 0.70, 0.80), 1.00, "normal"),
    ("ES2-screen", tuple(nearest_srgb8(v) for v in es_s), 1.00, "screen"),
]
rows = []
target_es = codes_normal(es_n, 1.0, D)
for name, rgb, o, mode in ES:
    f = codes_normal if mode == "normal" else codes_screen
    rows.append((name, rgb, o, mode, f(lin(rgb), o, D)))
show("Family ES normal-vs-screen / mid-gray", target_es, rows)
