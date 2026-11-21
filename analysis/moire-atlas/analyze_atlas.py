#!/usr/bin/env python3
"""Measure the actual PNG stripes and reconcile them against theory.

For every case this script:
  1. reads the service-produced PNG (atlas_png decoder),
  2. builds three independent references (contract / oversampled / folded),
  3. measures dominant periods by DFT and beat displacements by phase,
  4. compares every measurement with the analytic prediction in cases.json.

Outputs results/measurements.json and the reference PNGs under reference/.
Pure standard library; no numpy, no prismplate imports.
"""
import json
import math
import pathlib

import atlas_png
import atlas_ref

ROOT = pathlib.Path(__file__).resolve().parent


def write_gray_png(path, img):
    """Minimal grayscale 8-bit PNG encoder (zlib) for reference images."""
    import struct
    import zlib
    h = len(img)
    w = len(img[0])
    raw = bytearray()
    for row in img:
        raw.append(0)
        for px in row:
            v = px[0] if isinstance(px, tuple) else px
            raw.append(int(v))
    def chunk(tag, payload):
        c = tag + payload
        return struct.pack(">I", len(payload)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + \
        chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    path.write_bytes(data)


def gray_matrix(rows):
    return [[atlas_png.luminance(px) for px in row] for row in rows]


def dft_magnitudes(signal):
    n = len(signal)
    mag = []
    for k in range(n // 2 + 1):
        re = sum(signal[t] * math.cos(-2 * math.pi * k * t / n) for t in range(n))
        im = sum(signal[t] * math.sin(-2 * math.pi * k * t / n) for t in range(n))
        mag.append(math.hypot(re, im))
    return mag


def dft_at(signal, k):
    n = len(signal)
    re = sum(signal[t] * math.cos(-2 * math.pi * k * t / n) for t in range(n))
    im = sum(signal[t] * math.sin(-2 * math.pi * k * t / n) for t in range(n))
    return complex(re, im)


def dominant_period(signal, f_min=0.0, f_max=0.5, exclude_zero=True):
    """Peak DFT bin restricted to a frequency band, returned as (f, period)."""
    n = len(signal)
    mag = dft_magnitudes(signal)
    k_lo = max(1 if exclude_zero else 0, int(math.ceil(f_min * n)))
    k_hi = min(n // 2, int(math.floor(f_max * n)))
    if k_hi < k_lo:
        return 0.0, float("inf"), 0.0
    best = max(range(k_lo, k_hi + 1), key=lambda k: mag[k])
    f = best / n
    return f, (1.0 / f if f > 0 else float("inf")), mag[best]


def project(mat, ux, uy):
    """Average luminance projected onto axis (ux, uy): 1D profile."""
    n = len(mat)
    coords = {}
    for y in range(n):
        for x in range(n):
            t = round(ux * x + uy * y)
            coords.setdefault(t, []).append(mat[y][x])
    tmax = max(coords)
    return [sum(coords[t]) / len(coords[t]) for t in range(tmax + 1)]


def phase_shift_period(signal_a, signal_b, freq):
    """Phase difference (in samples) at the given frequency, unwrapped."""
    n = min(len(signal_a), len(signal_b))
    k = int(round(freq * n))
    za = dft_at(signal_a[:n], k)
    zb = dft_at(signal_b[:n], k)
    dphi = math.atan2(zb.imag, zb.real) - math.atan2(za.imag, za.real)
    while dphi > math.pi:
        dphi -= 2 * math.pi
    while dphi < -math.pi:
        dphi += 2 * math.pi
    return dphi / (2 * math.pi * freq)


def mismatch(img_a, img_b):
    n = len(img_a)
    diffs = 0
    maxd = 0
    for y in range(n):
        for x in range(n):
            for c in range(3):
                d = abs(img_a[y][x][c] - img_b[y][x][c])
                maxd = max(maxd, d)
                if d > 2:  # 8-bit code-value tolerance for rounding only
                    diffs += 1
    total = n * n * 3
    return diffs, total, maxd


def theory_beat_f(case):
    """Analytic difference frequency of the two gratings (cycles/px)."""
    gs = case["gratings"]
    if len(gs) < 2:
        return None
    ks = []
    for g in gs:
        th = math.radians(g["angle_deg"])
        f = 1.0 / g["period"]
        ks.append((f * math.cos(th), f * math.sin(th)))
    dx = ks[0][0] - ks[1][0]
    dy = ks[0][1] - ks[1][1]
    return math.hypot(dx, dy)


def analyze_parallel(case, png_mat, ref_mat, folded_mat, pair_png, pair_ref):
    g = case["gratings"]
    size = case["size"]
    # Project onto the grating normal (x-axis for angle 0): row-average.
    prof = [sum(png_mat[y][x] for y in range(size)) / size for x in range(size)]
    prof_ref = [sum(ref_mat[y][x] for y in range(size)) / size for x in range(size)]
    prof_fold = [sum(folded_mat[y][x] for y in range(size)) / size for x in range(size)]
    out = {"axis": "x (grating normal)"}
    if len(g) == 1:
        f, period, _ = dominant_period(prof, 0.05, 0.50)
        out["measured_png_carrier"] = {"f": round(f, 5), "period_px": round(period, 3)}
        ff, pf, _ = dominant_period(prof_fold, 0.05, 0.50)
        out["measured_folded_carrier"] = {"f": round(ff, 5), "period_px": round(pf, 3)}
        return out
    # Carrier band: above the beat region. The beat window is centred on the
    # analytic difference frequency so the loud individual-grating carriers do
    # not mask the weaker envelope.
    fc, pc, _ = dominant_period(prof, 0.30, 0.50)
    fbeat = theory_beat_f(case)
    lo = max(0.002, fbeat * 0.5)
    hi = min(0.28, fbeat * 1.8)
    fb, pb, _ = dominant_period(prof, lo, hi)
    out["measured_png_carrier"] = {"f": round(fc, 5), "period_px": round(pc, 3)}
    out["measured_png_beat"] = {"f": round(fb, 5), "period_px": round(pb, 3)}
    fb_ref, pb_ref, _ = dominant_period(prof_ref, lo, hi)
    out["measured_oversampled_beat"] = {"f": round(fb_ref, 5), "period_px": round(pb_ref, 3)}
    ff, pf, _ = dominant_period(prof_fold, 0.30, 0.50)
    out["measured_folded_carrier"] = {"f": round(ff, 5), "period_px": round(pf, 3)}
    if pair_png is not None:
        pair_prof = [sum(pair_png[y][x] for y in range(size)) / size for x in range(size)]
        out["measured_beat_displacement_px"] = round(
            phase_shift_period(prof, pair_prof, fb), 3)
        # Carrier-level phase response: the retained true carrier and the
        # folded alias react differently to the same physical translation.
        out["carrier_phase_response_px"] = round(
            phase_shift_period(prof, pair_prof, fc), 3)
        out["folded_phase_response_px"] = round(
            phase_shift_period(prof, pair_prof, ff), 3)
    return out


def km_axis_deg(case):
    """Theoretical km (difference wave-vector) axis in degrees."""
    gs = case["gratings"]
    k = []
    for g in gs:
        th = math.radians(g["angle_deg"])
        f = 1.0 / g["period"]
        k.append((f * math.cos(th), f * math.sin(th)))
    if len(k) == 1:
        return gs[0]["angle_deg"]
    dx = k[0][0] - k[1][0]
    dy = k[0][1] - k[1][1]
    deg = math.degrees(math.atan2(dy, dx))
    return deg % 180.0


def scan_axis(mat, f_lo, f_hi):
    """Return (axis_deg, f, period) of the strongest DFT peak over axes."""
    best = None
    for deg in range(0, 180, 2):
        rad = math.radians(deg)
        prof = project(mat, math.cos(rad), math.sin(rad))
        f, period, mag = dominant_period(prof, f_lo, f_hi)
        if best is None or mag > best[0]:
            best = (mag, deg, f, period)
    return best[1], best[2], best[3]


def analyze_angled(case, png_mat, ref_mat, folded_mat, pair_png, pair_ref):
    size = case["size"]
    theory_axis = km_axis_deg(case)
    # Independent orientation check. The lowest resolvable frequency is set by
    # the projected profile length; coarse beats spanning only ~2-3 periods
    # make this scan a secondary cross-check, not the primary period source.
    f_floor = 2.0 / (size * 1.5)
    scan_deg, _, _ = scan_axis(png_mat, f_floor, 0.25)
    # Measure the beat on the theoretical km axis (robust when the canvas only
    # spans a few beat periods, where the free scan can latch onto a harmonic).
    ux, uy = math.cos(math.radians(theory_axis)), math.sin(math.radians(theory_axis))
    prof = project(png_mat, ux, uy)
    fbeat = theory_beat_f(case)
    lo = max(0.002, fbeat * 0.5)
    hi = min(0.28, fbeat * 1.8)
    beat_f, beat_period, _ = dominant_period(prof, lo, hi)
    out = {
        "theory_km_axis_deg": round(theory_axis, 2),
        "scanned_peak_axis_deg": scan_deg,
        "axis_error_deg": round(abs(scan_deg - theory_axis), 2),
        "measured_beat_f_on_theory_axis": round(beat_f, 5),
        "measured_beat_period_px": round(beat_period, 3),
    }
    # High-frequency carrier orientation (the alias artifacts live here).
    cdeg, cf, cp = scan_axis(png_mat, 0.30, 0.50)
    out["measured_png_carrier"] = {"axis_deg": cdeg, "f": round(cf, 5),
                                   "period_px": round(cp, 3)}
    # Continuous (8x) reference beat on the same axis and window.
    prof_ref = project(ref_mat, ux, uy)
    fref, pref, _ = dominant_period(prof_ref, lo, hi)
    out["measured_oversampled_beat"] = {"f": round(fref, 5), "period_px": round(pref, 3)}
    # Folded-model carrier orientation.
    fdeg, ff, fp = scan_axis(folded_mat, 0.30, 0.50)
    out["measured_folded_carrier"] = {"axis_deg": fdeg, "f": round(ff, 5),
                                      "period_px": round(fp, 3)}
    if pair_png is not None:
        # Displacement on the fixed theoretical km axis of the unshifted case.
        pa = project(png_mat, ux, uy)
        pb = project(pair_png, ux, uy)
        out["measured_beat_displacement_px"] = round(
            phase_shift_period(pa, pb, beat_f), 3)
    return out


def main():
    manifest = json.loads((ROOT / "cases.json").read_text())
    (ROOT / "reference").mkdir(exist_ok=True)
    (ROOT / "results").mkdir(exist_ok=True)
    # Cache pair PNGs for shift comparisons.
    pair_map = {
        "B2-parallel-coarse-shift": "B1-parallel-coarse",
        "C2-angled-coarse-shift": "C1-angled-coarse",
        "D2-fine-parallel-mixed-shift": "D1-fine-parallel-mixed",
        "E2-fine-angled-mixed-shift": "E1-fine-angled-mixed",
    }
    png_cache, ref_cache, fold_cache = {}, {}, {}
    results = []
    for case in manifest["cases"]:
        cid = case["id"]
        _, _, rows = atlas_png.read_png(ROOT / "artifacts" / f"{cid}.png")
        png_img = [[tuple(px[:3]) for px in row] for row in rows]
        contract = atlas_ref.render_contract(case)
        oversampled = atlas_ref.render_oversampled(case)
        folded = atlas_ref.render_folded(case)
        write_gray_png(ROOT / "reference" / f"{cid}.contract.png", contract)
        write_gray_png(ROOT / "reference" / f"{cid}.continuous8x.png", oversampled)
        write_gray_png(ROOT / "reference" / f"{cid}.folded.png", folded)
        diffs, total, maxd = mismatch(png_img, contract)
        fdiffs, _, fmax = mismatch(png_img, folded)
        png_mat, ref_mat, fold_mat = (gray_matrix(png_img),
                                      gray_matrix(oversampled),
                                      gray_matrix(folded))
        png_cache[cid], ref_cache[cid], fold_cache[cid] = png_mat, ref_mat, fold_mat
        pair_png = png_cache.get(pair_map.get(cid))
        pair_ref = ref_cache.get(pair_map.get(cid))
        if case["kind"] == "parallel" or case["kind"] == "single":
            measured = analyze_parallel(case, png_mat, ref_mat, fold_mat, pair_png, pair_ref)
        else:
            measured = analyze_angled(case, png_mat, ref_mat, fold_mat, pair_png, pair_ref)
        result = {
            "case": cid,
            "contract_agreement": {
                "mismatched_components": diffs, "total_components": total,
                "max_code_value_delta": maxd,
                "mismatch_fraction": round(diffs / total, 6)},
            "folded_agreement": {
                "mismatched_components": fdiffs,
                "max_code_value_delta": fmax,
                "mismatch_fraction": round(fdiffs / total, 6)},
            "measured": measured,
            "predicted": case["predictions"],
        }
        results.append(result)
        print(f"{cid:30s} contractΔ={maxd:3d} foldedΔ={fmax:3d}")
    (ROOT / "results" / "measurements.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2))
    print("wrote results/measurements.json and reference/*.png")


if __name__ == "__main__":
    main()
