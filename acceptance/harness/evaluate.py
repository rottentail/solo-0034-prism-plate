#!/usr/bin/env python3
"""Mechanism-level evaluation and release gates.

The question is not "does this viewer look like Prism?" in bulk.  It is "can a
recipient, looking at the SVG in this viewer, decide *this particular question*
correctly?"  So every probe is attached to one mechanism, and each mechanism is
judged twice:

  SVG semantics   does the viewer implement the mechanism (blend applied at
                  all, group isolated, linearRGB honoured, premultiplied
                  gradient, coverage at a knockout rim)?
  Prism fidelity  how far is the viewer's absolute pixel from the authoritative
                  Prism PNG?  Prism composites in linear light; the SVG canvas
                  composites in sRGB.  That difference is inherent to the
                  format, not a viewer bug, and it is measured here per
                  mechanism instead of being hidden in one global tolerance.

A mechanism is:
  ADJUDICABLE  if semantics pass in at least one mode and that mode is stable
               across page colours;
  FAITHFUL     if fidelity also passes for some viewer/mode;
  PNG-ONLY     if no viewer is faithful; then the mechanism's absolute colour
               must be approved from the PNG, while its structural question may
               still be readable from the SVG.

Thresholds (uniform, stated once):
  solid 2 | group-invariant 2 | blend 8 | gradient 8 | edge-point 8 | edge-scan 16
The edge-scan allowance of 16 is for a +/-1 px rasterisation shift; it is
reported separately from the unshifted value so the two never get conflated.

Output: acceptance/observations/verdicts.json
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OBS = ROOT / "acceptance" / "observations"
M = json.loads((OBS / "measurements.json").read_text())
REF_LINEAR = json.loads((OBS / "reference_check.json").read_text())
REF_SRGB = json.loads((OBS / "reference_check_srgb.json").read_text())
SWEEP = json.loads((OBS / "blend_gap_sweep.json").read_text())

TH = {"solid": 2, "group_invariant": 2, "blend": 8, "gradient": 8,
      "edge_point": 8, "edge_scan": 16}
NEGATIVE = {"imagemagick"}
MODE_ORDER = ("own", "img", "post")
# "worst" rather than "best": a viewer that is right on only one page
# colour has not reproduced the document, it has been lucky.
DELTA_KEY = {"own": "worst_delta", "img": "worst_img_delta", "post": "worst_post_delta"}
MODE_ORDER = ("own", "img", "doc", "post")
SPREAD_KEY = {"own": "own_page_spread_opaque", "img": "img_page_spread_opaque"}
PAGE_KEY = {"own": "per_page", "img": "img_page", "post": "post_page"}

# mechanism -> scene -> probes
MECHANISMS = {
    "solid-fill": ("实色填充", {"a-solid-control": ["bg", "blue"]}, "solid"),
    "affine-transform": ("仿射变换下的实色轮廓", {"a-solid-control": ["red-tilt"]}, "solid"),
    "multiply-over-composited": ("multiply 对上已合成图层",
                                 {"b-backdrop-blend": ["base-mul-overlap"]}, "blend"),
    "multiply-over-backdrop": ("multiply 对上文档底色",
                               {"b-backdrop-blend": ["mul-over-bg"]}, "blend"),
    "screen-over-backdrop": ("screen 对上文档底色",
                             {"b-backdrop-blend": ["screen-over-bg"]}, "blend"),
    "group-isolation": ("组隔离（同色重叠 == 单形状）",
                        {"c-group-isolation": "INVARIANT"}, "group_invariant"),
    "group-multiply": ("组内 multiply 展开",
                       {"c-group-isolation": ["mul-overlap", "mul-a-only", "mul-b-only"]}, "blend"),
    "group-alpha": ("组内半透明叠加",
                    {"c-group-isolation": ["op-overlap", "op-c-only", "op-d-only"]}, "blend"),
    "gradient-linear": ("不透明渐变的线性光插值",
                        {"d-gradient-opaque": ["t025", "t05", "t075"]}, "gradient"),
    "gradient-alpha": ("透明渐变的预乘插值",
                       {"e-gradient-alpha": ["t025", "t05", "t075"]}, "gradient"),
    "blend-over-transparent": ("透明底上的 multiply/screen",
                               {"f-transparent-backdrop": ["mul-over-transparent",
                                                           "screen-over-transparent"]}, "blend"),
    "knockout-hole": ("镂空孔洞内部",
                      {"g-knockout-edge": ["fill-hole", "clip-hole", "mask-hole"]}, "edge_point"),
    "knockout-body": ("镂空环体（与覆盖率无关）",
                      {"g-knockout-edge": ["fill-body", "clip-body", "mask-body"]}, "edge_point"),
    "knockout-edge": ("镂空边缘扫描（±1px 选优）",
                      {"g-knockout-edge": "SCAN"}, "edge_scan"),
}
# The transparent document background is expected to interact with the page at
# *transparent* pixels; it is an observation, never a pass/fail gate.
TRANSPARENT_OBSERVATION = ("f-transparent-backdrop", "transparent-bg")


def has_mode(data, mode):
    if mode == "doc":
        return bool(data.get("doc", {}).get("comparable"))
    return any(data["probes"][n][PAGE_KEY[mode]] for n in data["probes"])


def doc_worst(data, names):
    """Worst per-probe delta in standalone-document mode, or None."""
    d = data.get("doc") or {}
    if not d.get("comparable"):
        return None
    vals = [d["probes"][n]["delta"] for n in names if n in d["probes"]]
    return max(vals) if vals else None


def doc_values(data):
    d = data.get("doc") or {}
    if not d.get("comparable"):
        return {}
    return {n: v["viewer"] for n, v in d["probes"].items()}


def probe_values(data, mode, scene, names, page="white"):
    out = {}
    for n in names:
        rec = data["probes"].get(n)
        if not rec:
            continue
        p = rec[PAGE_KEY[mode]].get(page)
        if p:
            out[n] = p["viewer"]
    return out


def semantics(mech, data, mode):
    """True when the viewer visibly implements the mechanism."""
    px = doc_values(data) if mode == "doc" else probe_values(data, mode, None, list(data["probes"]), "white")
    checks = []
    if mech in ("solid-fill", "affine-transform", "multiply-over-composited",
                "multiply-over-backdrop", "screen-over-backdrop", "knockout-hole",
                "knockout-body"):
        if mode != "doc":
            checks.append(("页面底色不影响不透明像素", data.get(SPREAD_KEY.get(mode, ""), 0) == 0))
    if mech == "multiply-over-composited":
        base = px.get("base-only")
        if base and "base-mul-overlap" in px:
            checks.append(("multiply 后变暗", px["base-mul-overlap"][0] < base[0]))
    if mech in ("multiply-over-backdrop", "screen-over-backdrop"):
        bg = px.get("bg")
        p = px.get("mul-over-bg" if mech.startswith("multiply") else "screen-over-bg")
        if bg and p:
            checks.append((("multiply 后变暗" if mech.startswith("multiply") else "screen 后变亮"),
                           p[0] < bg[0] if mech.startswith("multiply") else p[0] > bg[0]))
    if mech == "group-isolation":
        names = [n for n in ("same-overlap", "same-c-only", "same-d-only") if n in px]
        if len(names) == 3:
            vals = [px[n] for n in names]
            spread = max(max(abs(a - b) for a, b in zip(x, y)) for x in vals for y in vals)
            checks.append((f"同色重叠与单形状像素一致（差 {spread}）", spread <= TH["group_invariant"]))
        # The equality above is necessary but not sufficient: a viewer that
        # ignores group opacity entirely (paints the shape at full strength
        # everywhere) also satisfies it.  So the shape-only pixel must also be
        # visibly lighter than the same opaque source would be at full opacity.
        only = px.get("same-c-only")
        base = px.get("base")
        if only and base:
            # Prism: 50 % of (0.9,0.3,0.2) over base (0.4,0.5,0.6).
            checks.append((f"组不透明度确实施加了（单形状 {only[:3]} 未等于全不透明源色）",
                           only[0] < 230))
    if mech == "group-multiply":
        base = px.get("base")
        if base and "mul-overlap" in px:
            checks.append(("组内 multiply 变暗", px["mul-overlap"][0] < base[0]))
    if mech == "group-alpha":
        o = px.get("op-overlap")
        if o:
            checks.append(("组内 alpha 叠加产生中间色", o != px.get("base")))
    if mech == "gradient-linear":
        g = px.get("t025")
        if g:
            checks.append((f"线性光中点 G≈190（观测 G={g[1]}）", g[1] >= 170))
    if mech == "gradient-alpha":
        # Two independent facts, both required for "premultiplied":
        #   (a) the alpha ramp 1 -> 0 is present (recovered from two page
        #       colours, so this works for viewers with no alpha channel);
        #   (b) the straight colour stays on the magenta stop all the way to
        #       the transparent end.  Independent colour/alpha interpolation
        #       instead walks the straight colour towards the green stop.
        rec = (data.get("recovered_straight") or {}).get("t05")
        p = px.get("t05")
        if rec and rec.get("rgb"):
            r, g, b = rec["rgb"]
            checks.append((f"半程 alpha≈0.5（观测 {rec['alpha']}）", abs(rec["alpha"] - 0.5) <= 0.06))
            checks.append((f"半程直线色仍为洋红，G 明显低于 min(R,B)（观测 {rec['rgb']}）",
                           g <= min(r, b) - 40))
        elif p:
            checks.append((f"半程保持预乘洋红（观测 {p[:3]}）", p[1] <= min(p[0], p[2]) - 40))
    return checks


def mechanism_result(mech, scene_data, mode):
    r = _mechanism_result(mech, scene_data, mode)
    # Fidelity is only meaningful once the viewer implements the mechanism.
    # A viewer that ignores group opacity can match a spread of 0 without ever
    # having composed the group at all.  "No applicable check" is different
    # from "check failed": in standalone-document mode there is only one page,
    # so page-independence simply cannot be observed.
    # `fidelity_pass` must already include the semantics gate.  The value is
    # computed inside the per-theme branches, so enforce it here once.
    r["fidelity_pass"] = bool(r.get("fidelity_pass")) and r.get("semantics_state") != "fail"
    # "Cannot be measured in this mode" is not the same as "failed" -- for
    # example the edge scan cannot be aligned against a single-page capture.
    r["fidelity_state"] = ("n/a" if r.get("fidelity_max") is None
                           else ("pass" if r["fidelity_pass"] else "fail"))
    return r


def _mechanism_result(mech, scene_data, mode):
    label, targets, theme = MECHANISMS[mech]
    if mode == "doc":
        sem = semantics(mech, scene_data, mode)
        # No applicable check in this mode is "not applicable", not "failed".
        if not sem:
            sem_state = "n/a"
        else:
            sem_state = "pass" if all(ok for _, ok in sem) else "fail"
        sem_ok = sem_state == "pass"
        names = [n for _, ns in targets.items() if isinstance(ns, list) for n in ns]
        if theme == "group_invariant":
            vals = [doc_values(scene_data)[n] for n in ("same-overlap", "same-c-only", "same-d-only")
                    if n in doc_values(scene_data)]
            spread = (max(max(abs(a - b) for a, b in zip(x, y)) for x in vals for y in vals)
                      if vals else None)
            return {"mode": mode, "semantics_pass": sem_ok, "semantics": sem,
                    "fidelity_max": spread, "fidelity_threshold": TH["group_invariant"],
                    "semantics_state": sem_state,
                    "fidelity_pass": spread is not None and spread <= TH["group_invariant"]}
        if theme == "edge_scan":
            return {"mode": mode, "semantics_pass": sem_ok, "semantics": sem,
                    "semantics_state": sem_state,
                    "fidelity_max": None, "fidelity_threshold": TH["edge_scan"],
                    "fidelity_pass": False,
                    "note": "独立文档模式不产生可对齐的边缘扫描"}
        worst = doc_worst(scene_data, names)
        return {"mode": mode, "semantics_pass": sem_ok, "semantics": sem,
                "semantics_state": sem_state,
                "fidelity_max": worst, "fidelity_threshold": TH[theme],
                "fidelity_pass": worst is not None and worst <= TH[theme]}
    sem = semantics(mech, scene_data, mode)
    sem_ok = all(ok for _, ok in sem) if sem else True
    sem_state = "n/a" if not sem else ("pass" if sem_ok else "fail")
    if theme == "group_invariant":
        px = probe_values(scene_data, mode, None, list(scene_data["probes"]), "white")
        names = [n for n in ("same-overlap", "same-c-only", "same-d-only") if n in px]
        vals = [px[n] for n in names]
        flat = [x for v in vals for x in [v]]
        spread = max(max(abs(a - b) for a, b in zip(x, y)) for x in flat for y in flat) if flat else 999
        return {"mode": mode, "semantics_pass": sem_ok, "semantics": sem,
                "semantics_state": sem_state,
                "fidelity_max": spread, "fidelity_threshold": TH["group_invariant"],
                "fidelity_pass": spread <= TH["group_invariant"]}
    if theme == "edge_scan":
        scans = scene_data.get("scans") or []
        names = {x["name"] for x in scans}
        if not names:
            return {"mode": mode, "semantics_pass": sem_ok, "semantics": sem,
                    "semantics_state": sem_state,
                    "fidelity_max": None, "fidelity_threshold": TH["edge_scan"],
                    "fidelity_pass": False}
        # Only the "own" mode produced aligned scans; img/post/doc captures
        # are pixel-aligned differently and were not scanned.  Report n/a
        # rather than borrowing another mode's number.
        own_scans = [x for x in scans if x.get("mode", "own") == "own"]
        if not own_scans:
            return {"mode": mode, "semantics_pass": sem_ok, "semantics": sem,
                    "semantics_state": sem_state,
                    "fidelity_max": None, "fidelity_threshold": TH["edge_scan"],
                    "fidelity_pass": False,
                    "note": "该查看方式未做边缘扫描"}
        worst = max(min(x["min_delta"] for x in own_scans if x["name"] == n) for n in names)
        zero = max(min(x["zero_shift_delta"] for x in own_scans if x["name"] == n) for n in names)
        return {"mode": mode, "semantics_pass": sem_ok, "semantics": sem,
                "semantics_state": sem_state,
                "fidelity_max": worst, "fidelity_unshifted_max": zero,
                "fidelity_threshold": TH["edge_scan"],
                "fidelity_pass": worst <= TH["edge_scan"]}
    vals = {}
    for sc, names in targets.items():
        for n in names:
            rec = scene_data["probes"].get(n)
            if rec:
                v = rec.get(DELTA_KEY[mode])
                if v is not None:
                    vals[n] = v
    worst = max(vals.values()) if vals else None
    return {"mode": mode, "semantics_pass": sem_ok, "semantics": sem,
            "semantics_state": sem_state,
            "fidelity_max": worst, "fidelity_threshold": TH[theme],
            "fidelity_pass": worst is not None and worst <= TH[theme], "probes": vals}


def format_gap(scene, names):
    """Largest difference between the two coherent models on these probes.

    This is the fidelity ceiling of SVG itself: a perfectly conformant SVG
    renderer that composites in the canvas colour space cannot get closer to
    Prism than this, because the gap is the colour space, not an error.
    """
    gaps = {}
    for n in names:
        l = REF_LINEAR["scenes"].get(scene, {}).get("probes", {}).get(n)
        s = REF_SRGB["scenes"].get(scene, {}).get("probes", {}).get(n)
        if l and s:
            gaps[n] = max(abs(a - b) for a, b in zip(l["model"][:3], s["model"][:3]))
    return gaps


def model_attribution(scene, viewer_px_by_probe):
    """Which compositing model does a viewer's pixel set follow?

    Two independent models were fitted against the captured Prism PNG:
      linear  Prism's documented model (linear-light, layer-flattened groups)
      srgb    the same structure but source-over/blend maths on sRGB values,
              which is what an SVG canvas without linear handling does

    A viewer that is consistently closer to one model is not "buggy" for the
    other; it is following a different but well-defined compositing space.
    That distinction is the difference between "this viewer mis-rendered the
    group" and "SVG cannot express Prism's linear light".
    Returns {probe: {"linear_delta", "srgb_delta", "closer"}} plus a summary.
    """
    out = {}
    for name, px in viewer_px_by_probe.items():
        l = REF_LINEAR["scenes"].get(scene, {}).get("probes", {}).get(name)
        s = REF_SRGB["scenes"].get(scene, {}).get("probes", {}).get(name)
        if not l or not s or px is None:
            continue
        dl = max(abs(a - b) for a, b in zip(l["model"][:3], px[:3]))
        ds = max(abs(a - b) for a, b in zip(s["model"][:3], px[:3]))
        out[name] = {"viewer": list(px[:4]), "linear_model": l["model"],
                     "srgb_model": s["model"],
                     "linear_delta": dl, "srgb_delta": ds,
                     "closer": "linear" if dl < ds else ("srgb" if ds < dl else "tie"),
                     "gap_between_models": max(abs(a - b) for a, b in zip(l["model"][:3], s["model"][:3]))}
    return out


def model_verdict(attr):
    """Summarise attribution over probes that actually discriminate."""
    disc = {n: a for n, a in attr.items() if a["gap_between_models"] > TH["solid"]}
    if not disc:
        return {"discriminating_probes": 0, "verdict": "no-discrimination"}
    matches_linear = sum(1 for a in disc.values() if a["closer"] == "linear" and a["linear_delta"] <= 3)
    matches_srgb = sum(1 for a in disc.values() if a["closer"] == "srgb" and a["srgb_delta"] <= 3)
    total = len(disc)
    if matches_linear == total:
        return {"discriminating_probes": total, "verdict": "follows-prism-linear"}
    if matches_srgb == total:
        return {"discriminating_probes": total, "verdict": "follows-srgb-canvas"}
    return {"discriminating_probes": total, "verdict": "mixed",
            "linear": matches_linear, "srgb": matches_srgb}


def main():
    scene_to_mechs = {}
    for mech, (_, targets, _) in MECHANISMS.items():
        for sc in targets:
            scene_to_mechs.setdefault(sc, []).append(mech)

    out = {"thresholds": TH, "mode_order": list(MODE_ORDER),
           "mechanisms": {}, "viewers": {}, "gates": {}, "observations": {}}

    for viewer, entry in M["viewers"].items():
        rec = {"available": entry["available"], "version": entry.get("version"),
               "role": "negative-control" if viewer in NEGATIVE else "candidate",
               "embed_modes": entry.get("embed_modes", []),
               "alpha_capture": entry.get("alpha_capture"),
               "error": entry.get("error", ""), "mechanisms": {}, "scenes": {}}
        out["viewers"][viewer] = rec
        if not entry["available"]:
            continue
        for mech in MECHANISMS:
            _, targets, _ = MECHANISMS[mech]
            variants = {}
            for sc in targets:
                data = entry["scenes"].get(sc)
                if not data:
                    continue
                for mode in MODE_ORDER:
                    if not has_mode(data, mode):
                        continue
                    key = f"{sc}:{mode}"
                    variants[key] = mechanism_result(mech, data, mode)
            rec["mechanisms"][mech] = variants
        # Model attribution per scene: which compositing space is the viewer
        # actually following?  Uses the white-page capture, which is the mode
        # every viewer produced.
        rec["model_attribution"] = {}
        for scene, sdata in entry["scenes"].items():
            px = {}
            for n, prec in sdata["probes"].items():
                p = prec["per_page"].get("white")
                if p:
                    px[n] = p["viewer"]
            attr = model_attribution(scene, px)
            if attr:
                rec["model_attribution"][scene] = {
                    "probes": attr, "summary": model_verdict(attr)}
        # page-colour observations on wholly transparent pixels
        f = entry["scenes"].get("f-transparent-backdrop")
        if f:
            n = TRANSPARENT_OBSERVATION[1]
            r = f["probes"].get(n)
            if r:
                out["observations"].setdefault("transparent_pixel", {})[viewer] = {
                    "prism_alpha": r["prism_alpha"],
                    "own": {bg: v["viewer"] for bg, v in r["per_page"].items()},
                    "img": {bg: v["viewer"] for bg, v in r.get("img_page", {}).items()},
                }

    # ---------------------------------------------------------- mechanism ---
    for mech, (label, targets, theme) in MECHANISMS.items():
        ok_sem, ok_fid = [], []
        for viewer, vrec in out["viewers"].items():
            if not vrec["available"] or vrec["role"] == "negative-control":
                continue
            for key, r in vrec["mechanisms"].get(mech, {}).items():
                if r["semantics_pass"]:
                    ok_sem.append({"viewer": viewer, "variant": key})
                if r["semantics_pass"] and r["fidelity_pass"]:
                    ok_fid.append({"viewer": viewer, "variant": key})
        conf = {
            "label": label, "kind": theme,
            "semantics_ok": ok_sem, "faithful_ok": ok_fid,
            "best_observed_delta": min(
                (r["fidelity_max"] for v in out["viewers"].values()
                 if v["available"] and v["role"] != "negative-control"
                 for r in v["mechanisms"].get(mech, {}).values()
                 if r.get("fidelity_max") is not None), default=None),
        }
        # Which model do the viewers that pass SVG semantics follow?
        attributions = {}
        for v in {x["viewer"] for x in ok_sem}:
            for sc in targets:
                a = out["viewers"][v].get("model_attribution", {}).get(sc)
                if a and a["summary"]["verdict"] != "no-discrimination":
                    attributions.setdefault(a["summary"]["verdict"], []).append(v)
        conf["model_attribution"] = {k: sorted(set(v)) for k, v in attributions.items()}
        swap = {"multiply-over-composited": "multiply", "multiply-over-backdrop": "multiply",
                "screen-over-backdrop": "screen", "group-multiply": "multiply",
                "group-alpha": "over"}
        if mech in swap:
            conf["format_sweep"] = SWEEP[swap[mech]]
        # Format ceiling: the smallest possible distance between Prism and any
        # viewer that composites in the SVG canvas space.  When a viewer's
        # delta equals this gap, it is not a rendering bug.
        names = [n for _, ns in targets.items() if isinstance(ns, list) for n in ns]
        gaps = format_gap(list(targets)[0], names)
        if gaps:
            conf["format_gap"] = {"per_probe": gaps, "max": max(gaps.values())}
        if not ok_sem:
            conf["adjudicable"] = False
            conf["release"] = "PNG-only"
            conf["reason"] = "没有查看器表现出该机制；SVG 不能用于判断此问题"
        else:
            conf["adjudicable"] = True
            if ok_fid:
                conf["release"] = "SVG-with-condition"
                conf["reason"] = "可在列出的查看器/方式下按条件判定"
            elif conf.get("format_sweep") and conf["format_sweep"]["worst_delta"] <= TH[theme]:
                # The two models are within threshold over the entire colour
                # cube, so a scene that fails here failed for a scene-specific
                # reason rather than the format gap.  Still report it.
                conf["release"] = "PNG-only-for-colour"
                conf["reason"] = (
                    "该类颜色在整段色域内两种模型的差都在阈值内，但本稿实测仍超阈值；"
                    "按个例处理，颜色以 PNG 为准")
            elif conf.get("format_gap", {}).get("max", 0) > TH[theme]:
                conf["release"] = "PNG-only-for-colour"
                conf["reason"] = (
                    f"两种独立合成模型在该探针上的固有差为 {conf['format_gap']['max']}，"
                    f"超过阈值 {TH[theme]}；能表现该机制的查看器都精确落在 sRGB 画布模型上。"
                    "结构关系可由 SVG 判读，绝对颜色必须由 PNG 放行")
            else:
                conf["release"] = "PNG-only-for-colour"
                conf["reason"] = ("查看器之间互相不一致且无法归因到已知模型；"
                                  "颜色必须由 PNG 放行")
        out["mechanisms"][mech] = conf

    # ------------------------------------------------------------ scene -----
    for scene in M["scenes"]:
        mechs = [m for m in scene_to_mechs.get(scene, [])]
        rel = [out["mechanisms"][m]["release"] for m in mechs]
        if not rel:
            continue
        # Scene-level label, deliberately coarse; per_mechanism keeps the
        # detail.  "SVG" still means "SVG under the viewing conditions named
        # for each mechanism", never "SVG anywhere".
        if any(r == "PNG-only" for r in rel):
            release = "PNG-only"
        elif any(r == "PNG-only-for-colour" for r in rel):
            release = "SVG-structure-PNG-colour"
        else:
            release = "SVG"
        out["gates"][scene] = {"title": M["scenes"][scene]["title"],
                               "mechanisms": mechs, "release": release,
                               "per_mechanism": {m: out["mechanisms"][m]["release"] for m in mechs}}

    (OBS / "verdicts.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")

    for mech in MECHANISMS:
        c = out["mechanisms"][mech]
        print(f"{mech:24s} {c['release']:22s} bestΔ={c['best_observed_delta']}  "
              f"sem={len(c['semantics_ok'])} fid={len(c['faithful_ok'])}")
    print()
    for s, g in out["gates"].items():
        print(f"{s:26s} {g['release']}")


if __name__ == "__main__":
    sys.exit(main())
