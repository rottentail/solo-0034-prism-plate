#!/usr/bin/env python3
"""How large is the linear-vs-sRGB gap for a blend, over the whole colour cube?

The acceptance thresholds must not be chosen per scene.  This sweep asks the
format-level question directly: over all 8-bit (backdrop, source) colour pairs,
how far apart are these operations when evaluated in linear light (what Prism
does) versus in sRGB (what an SVG canvas does)?

  multiply    d*s
  screen      d+s-d*s
  over(50%)   a*s + (1-a)*d  at alpha 0.25 / 0.50 / 0.75

If a mode's worst gap stays under the acceptance threshold, that mode can be
colour-approved from SVG in general.  If it does not, then only the specific
colours that happen to be close can be, which is not a property a recipient can
rely on for a new document.

Output: acceptance/observations/blend_gap_sweep.json
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OBS = ROOT / "acceptance" / "observations"
ALPHAS = (0.25, 0.5, 0.75)


def lin(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def srgb(v):
    v = min(1.0, max(0.0, v))
    return v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055


def blend(mode, d, s):
    if mode == "multiply":
        return d * s
    if mode == "screen":
        return d + s - d * s
    raise ValueError(mode)


def main():
    out = {}
    for mode in ("multiply", "screen", "over"):
        worst = (0.0, None)
        within = {8: 0, 16: 0, 32: 0}
        for i in range(256):
            for j in range(256):
                d, s = i / 255.0, j / 255.0
                if mode == "over":
                    # Worst over the three alphas, each computed in both spaces.
                    pairs = []
                    for a in ALPHAS:
                        cl = srgb(lin(s) * a + lin(d) * (1 - a)) * 255
                        cs = (s * a + d * (1 - a)) * 255
                        pairs.append((abs(cl - cs), round(cl), round(cs), a))
                    diff, cl, cs, a = max(pairs)
                else:
                    cl = srgb(blend(mode, lin(d), lin(s))) * 255
                    cs = blend(mode, d, s) * 255
                    diff = abs(cl - cs)
                    a = 1.0
                if diff > worst[0]:
                    worst = (diff, {"backdrop": i, "source": j, "alpha": a,
                                    "linear": cl, "srgb": cs})
                for k in within:
                    if diff <= k:
                        within[k] += 1
        total = 256 * 256
        out[mode] = {
            "worst_delta": round(worst[0], 2),
            "worst_case": worst[1],
            "share_within": {str(k): round(v / total, 4) for k, v in within.items()},
            "pairs": total,
        }
    out["threshold"] = 8
    out["reading"] = ("multiply 的固有差在整个色域内不超过阈值；screen 与半透明 over "
                      "的固有差明显超过阈值，只有颜色恰好接近时才可能通过，不能作为可依赖的性质。")
    (OBS / "blend_gap_sweep.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    for mode in ("multiply", "screen", "over"):
        d = out[mode]
        print(f"{mode:9s} worstΔ={d['worst_delta']:6.2f} at {d['worst_case']}  "
              f"≤8: {d['share_within']['8']:.1%}  ≤16: {d['share_within']['16']:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
