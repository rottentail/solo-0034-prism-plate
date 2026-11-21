#!/usr/bin/env python3
"""Independent reference implementation of the documented Prism model.

This deliberately does not import or call the Go renderer.  It re-implements
the contract from README/PROJECT_SPEC at probe pixels:

  * sRGB 0..1 values are converted to linear light;
  * colours are premultiplied and composited with the over operator;
  * a layer's shapes are flattened inside the layer, scaled by the layer
    opacity, and blended once against the backdrop;
  * multiply/screen use the standard separable blend with source-over
    alpha compositing;
  * a layer's clip and mask are binary paths in page coordinates;
  * gradient stops interpolate in premultiplied linear light along the axis.

Then it compares the model's predicted pixel with the bytes the Go service
actually returned.  Agreement is what makes the Prism PNG a legitimate oracle
for the viewer comparison; without this step "we compared against our own
output" would prove nothing about correctness.

Usage: python3 acceptance/harness/reference.py
Output: acceptance/observations/reference_check.json
"""
import json
import math
import pathlib
import sys

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENES = ROOT / "acceptance" / "scenes"
ARTIFACTS = ROOT / "acceptance" / "artifacts"
OBS = ROOT / "acceptance" / "observations"


def to_linear(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def to_srgb(v):
    v = min(1.0, max(0.0, v))
    return v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055


# Set by --space; "linear" reproduces Prism, "srgb" reproduces a renderer that
# does source-over and blend maths directly on the 0..1 sRGB values (which is
# what an SVG canvas without linear handling does).
SPACE = "linear"


def enc(v):
    return to_linear(v) if SPACE == "linear" else v


def dec(v):
    return to_srgb(v) if SPACE == "linear" else v


def flatten(ops):
    """Mirror of geom.Flatten: Q 16 segments, C 24, contours closed by Z."""
    out, ring, at = [], [], None
    for op in ops:
        kind, pts = op["kind"], op.get("points") or []
        if kind == "M":
            if len(ring) >= 3:
                out.append(ring)
            at = (pts[0]["x"], pts[0]["y"])
            ring = [at]
        elif kind == "L":
            at = (pts[0]["x"], pts[0]["y"])
            ring.append(at)
        elif kind in ("Q", "C"):
            start = at
            ctrl = [(p["x"], p["y"]) for p in pts]
            n = 16 if kind == "Q" else 24
            for i in range(1, n + 1):
                t = i / n
                u = 1 - t
                if kind == "Q":
                    (cx, cy), (ex, ey) = ctrl[0], ctrl[1]
                    ring.append((u * u * start[0] + 2 * u * t * cx + t * t * ex,
                                 u * u * start[1] + 2 * u * t * cy + t * t * ey))
                else:
                    (ax, ay), (bx, by), (ex, ey) = ctrl
                    ring.append((u ** 3 * start[0] + 3 * u * u * t * ax + 3 * u * t * t * bx + t ** 3 * ex,
                                 u ** 3 * start[1] + 3 * u * u * t * ay + 3 * u * t * t * by + t ** 3 * ey))
            at = ctrl[-1]
        elif kind == "Z":
            if len(ring) >= 3:
                out.append(ring)
            ring = []
    if len(ring) >= 3:
        out.append(ring)
    return out


def contains(path, p, rule):
    winding = crossings = 0
    for ring in path:
        n = len(ring)
        for i in range(n):
            ax, ay = ring[i]
            bx, by = ring[(i + 1) % n]
            if (ay <= p[1] and by > p[1]) or (by <= p[1] and ay > p[1]):
                x = ax + (p[1] - ay) * (bx - ax) / (by - ay)
                if x > p[0]:
                    crossings += 1
                    winding += 1 if by > ay else -1
    return crossings % 2 == 1 if rule == "evenodd" else winding != 0


def inverse(m):
    d = m[0] * m[3] - m[1] * m[2]
    return [(m[3] / d), (-m[1] / d), (-m[2] / d), (m[0] / d),
            (m[2] * m[5] - m[3] * m[4]) / d, (m[1] * m[4] - m[0] * m[5]) / d]


def apply(m, p):
    return (m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5])


def from_color(c):
    a = c["a"]
    return [enc(c["r"]) * a, enc(c["g"]) * a, enc(c["b"]) * a, a]


def sample(brush, p):
    if brush["kind"] == "solid":
        return from_color(brush["color"])
    fx, fy = brush["from"]["x"], brush["from"]["y"]
    tx, ty = brush["to"]["x"], brush["to"]["y"]
    dx, dy = tx - fx, ty - fy
    t = ((p[0] - fx) * dx + (p[1] - fy) * dy) / (dx * dx + dy * dy)
    t = min(1.0, max(0.0, t))
    stops = brush["stops"]
    for i in range(1, len(stops)):
        a, b = stops[i - 1], stops[i]
        if t <= b["at"]:
            u = (t - a["at"]) / (b["at"] - a["at"])
            x, y = from_color(a["color"]), from_color(b["color"])
            return [x[k] + (y[k] - x[k]) * u for k in range(4)]
    return from_color(stops[-1]["color"])


def over(dst, src, mode):
    sa, da = src[3], dst[3]
    out = [0.0, 0.0, 0.0, sa + da * (1 - sa)]
    for k in range(3):
        if mode == "normal" or sa == 0 or da == 0:
            out[k] = src[k] + dst[k] * (1 - sa)
        else:
            a, b = dst[k] / da, src[k] / sa
            v = a * b if mode == "multiply" else a + b - a * b
            out[k] = (1 - sa) * dst[k] + (1 - da) * src[k] + sa * da * v
    return out


def pixel(doc, point, samples=2):
    """2x2 coverage sampling, the same grid the Go rasteriser uses."""
    acc = [0.0, 0.0, 0.0, 0.0]
    bg = doc["background"]
    for sy in range(samples):
        for sx in range(samples):
            p = (point[0] + (sx + 0.5) / samples, point[1] + (sy + 0.5) / samples)
            dst = from_color(bg)
            for layer in doc["layers"]:
                if layer.get("clip") and not contains(flatten(layer["clip"]), p, "nonzero"):
                    continue
                if layer.get("mask") and not contains(flatten(layer["mask"]), p, "nonzero"):
                    continue
                src = [0.0, 0.0, 0.0, 0.0]
                for sh in layer["shapes"]:
                    q = apply(inverse(sh["transform"]), p)
                    if contains(flatten(sh["path"]), q, sh["fill_rule"]):
                        src = over(src, sample(sh["brush"], q), "normal")
                op = layer["opacity"]
                dst = over(dst, [c * op for c in src], layer["blend"])
            for k in range(4):
                acc[k] += dst[k] / (samples * samples)
    if acc[3] <= 0:
        return (0, 0, 0, 0)
    return tuple(int(round(dec(acc[k] / acc[3]) * 255)) if k < 3
                  else int(round(min(1, acc[3]) * 255)) for k in range(4))


def main():
    global SPACE
    if "--space" in sys.argv:
        SPACE = sys.argv[sys.argv.index("--space") + 1]
    probes = json.loads((SCENES / "probes.json").read_text())["probes"]
    result = {"model": "linear-light, premultiplied, 2x2 coverage, layer-flattened groups",
              "scenes": {}}
    worst_overall = 0
    for scene_file in sorted(SCENES.glob("*.json")):
        if scene_file.name == "probes.json":
            continue
        scene = json.loads(scene_file.read_text())
        sid = scene["id"]
        png = Image.open(ARTIFACTS / f"{sid}.png").convert("RGBA")
        rows = {}
        for pr in probes[sid]:
            # pixel() adds the 2x2 sub-pixel offsets itself, so pass the
            # integer pixel corner exactly as the Go rasteriser does.
            want = pixel(scene["document"], (pr["x"], pr["y"]))
            got = png.getpixel((pr["x"], pr["y"]))
            delta = max(abs(a - b) for a, b in zip(want, got))
            rows[pr["name"]] = {"model": list(want), "png": list(got), "delta": delta,
                                "question": pr["question"]}
            worst_overall = max(worst_overall, delta)
        result["scenes"][sid] = {"probes": rows,
                                 "max_delta": max(r["delta"] for r in rows.values())}
    result["max_delta_all"] = worst_overall
    result["space"] = SPACE
    # 1 unit of 8-bit round-off is expected because the PNG is quantised per
    # sample *after* averaging while this model averages then quantises.
    result["tolerance"] = 2
    if SPACE == "linear":
        result["verdict"] = ("PASS: independent linear-light model reproduces the captured PNG"
                             if worst_overall <= 2 else
                             "FAIL: linear model does not reproduce the PNG; the PNG is not a safe oracle")
        result["role"] = "oracle validation"
    else:
        # The sRGB run is a control: it must NOT match, and its mismatch is
        # the size of the format gap the guide reports.
        result["verdict"] = ("CONTROL: sRGB-canvas model differs by %d, as expected"
                             % worst_overall)
        result["role"] = "format-gap control (expected to differ)"
    name = "reference_check.json" if SPACE == "linear" else "reference_check_srgb.json"
    (OBS / name).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    for sid, s in result["scenes"].items():
        print(f"{sid:26s} maxΔ={s['max_delta']}")
    print(result["verdict"], "| overall maxΔ =", worst_overall)
    return 0 if worst_overall <= 2 else 1


if __name__ == "__main__":
    sys.exit(main())
