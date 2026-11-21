"""Forensic analysis over the real service PNGs.

Steps:
  1. Read interior pixels from every rendered PNG with the stdlib PNG reader.
  2. Assert they match the transcribed paint.go forward model channel-for-channel.
  3. For each family/background, group candidates by observed code tuple to show
     which observations are equifinal and which probes split them.
  4. Invert observations under 8-bit quantization: derive sRGB/coverage
     intervals, not point guesses.
  5. Persist machine-readable tables (json) plus a human report (md).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import model
import pngio
import recipes


def interior_code(path):
    w, h, px = pngio.read_png(path)
    x0, y0, x1, y1 = recipes.SAMPLE_BOX
    values = {px(x, y) for y in range(y0, y1) for x in range(x0, x1)}
    assert len(values) == 1, (path, "interior not flat", len(values))
    assert (w, h) == (recipes.CANVAS, recipes.CANVAS)
    return next(iter(values))


def predicted_code(rec, bg, bg_a):
    if isinstance(rec, tuple):  # Family D blank recipe marker
        return model.rgba8(model.from_color(*bg, bg_a))
    codes, _ = model.composite(
        rec["rgb"], rec["brush_a"], rec["opacity"], rec["blend"], bg, bg_a)
    return codes


def candidate_records():
    recs = []
    for fam, items in recipes.FAMILIES.items():
        for cid, rec in items:
            recs.append((fam, cid, rec))
    for cid, mode in recipes.FAMILY_D:
        recs.append(("D", cid, ("blank", mode)))
    return recs


def main():
    manifest = json.load(open(os.path.join(OUT, "render-manifest.json"),
                              encoding="utf-8"))
    by_key = {(m["candidate"], m["background"]): m for m in manifest}

    # 1+2: measure real PNGs and verify against the forward model.
    measured = {}
    mismatches = []
    for fam, cid, rec in candidate_records():
        for bg_name, bg_rgb, bg_a in recipes.BACKGROUNDS:
            entry = by_key[(cid, bg_name)]
            observed = interior_code(os.path.join(OUT, entry["png"]))
            expected = predicted_code(rec, bg_rgb, bg_a)
            measured[(cid, bg_name)] = observed
            if observed != expected:
                mismatches.append((cid, bg_name, observed, expected))
    assert not mismatches, mismatches

    # 3: equifinality / separation table per family and background.
    groups = {}
    for fam in list(recipes.FAMILIES) + ["D"]:
        members = [c for f, c, _ in candidate_records() if f == fam]
        for bg_name, _, _ in recipes.BACKGROUNDS:
            buckets = {}
            for cid in members:
                buckets.setdefault(measured[(cid, bg_name)], []).append(cid)
            groups[f"{fam}|{bg_name}"] = {
                "code_groups": [{"codes": list(k), "candidates": v}
                                for k, v in sorted(buckets.items())],
                "distinguishable": len(buckets) > 1,
            }

    # 4: quantified inversions for the worked family A on mid-gray.
    a1 = dict(recipes.FAMILY_A[0][1])
    target = measured[("A1-opaque", "bg-midgray")][:3]
    d = model.linear(0.5)
    intervals = []
    for ch, code in enumerate(("R", "G", "B")):
        lo_s, hi_s = model.code_interval(target[ch])
        # normal mode, opaque background: y = (1-o)d + o*s  =>  s = (y-(1-o)d)/o
        rows = []
        for cid, rec in recipes.FAMILY_A:
            o = rec["brush_a"] * rec["opacity"]
            lo = (model.linear(lo_s) - (1 - o) * d) / o
            hi = (model.linear(hi_s) - (1 - o) * d) / o
            rows.append({
                "candidate": cid,
                "o": round(o, 4),
                "declared_srgb": rec["rgb"][ch],
                "admissible_linear_s": [round(max(0, lo), 5),
                                        round(min(1, hi), 5)],
                "admissible_srgb_s": [
                    round(model.srgb(max(0, lo)), 4),
                    round(model.srgb(min(1, hi)), 4)],
            })
        intervals.append({"channel": code, "observed_code": target[ch],
                          "srgb_quant_interval": [round(lo_s, 5), round(hi_s, 5)],
                          "per_candidate": rows})

    # Coverage inversion from the alpha probe (straight alpha output = o+0.5(1-o)).
    coverage = []
    for fam in ("A", "B", "C"):
        members = [c for f, c, _ in candidate_records() if f == fam]
        for cid in members:
            a_code = measured[(cid, "bg-alpha50")][3]
            lo_a, hi_a = model.code_interval(a_code)
            # a_out = 0.5 + 0.5 o  =>  o = 2 a_out - 1
            o_lo = max(0.0, 2 * lo_a - 1)
            o_hi = min(1.0, 2 * hi_a - 1)
            declared = next(r for f, c, r in candidate_records()
                            if c == cid and not isinstance(r, tuple))
            coverage.append({
                "family": fam,
                "candidate": cid,
                "alpha_code": a_code,
                "coverage_o_interval": [round(o_lo, 4), round(o_hi, 4)],
                "declared_o": round(declared["brush_a"] * declared["opacity"], 4),
            })

    result = {
        "candidate_count": len(candidate_records()),
        "background_count": len(recipes.BACKGROUNDS),
        "render_count": len(manifest),
        "forward_model_agrees_with_png": not mismatches,
        "equifinality": groups,
        "family_A_midgray_srgb_inversion": intervals,
        "alpha_probe_coverage_inversion": coverage,
        "measured_codes": {f"{c}|{b}": list(v) for (c, b), v in sorted(measured.items())},
    }
    with open(os.path.join(OUT, "forensic-results.json"), "w",
              encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(json.dumps({
        "renders_checked": len(manifest),
        "model_mismatches": len(mismatches),
        "families": list(recipes.FAMILIES) + ["D"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
