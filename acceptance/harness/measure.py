#!/usr/bin/env python3
"""Measure every viewer capture against the authoritative Prism PNG.

The comparison basis is deliberately not "the raw screenshot".  Prism PNGs can
carry real alpha, while most viewers only write a flattened page.  So for each
capture we composite the Prism PNG over the same page colour the viewer used,
then compare that with the viewer's pixel.  For alpha-carrying captures we
compare the alpha channel directly.

Three metrics are recorded separately:

  point        absolute channel difference at a named probe pixel;
  page spread  how much the viewer's value changes when only the page colour
               changes.  On an opaque Prism pixel it should be zero.  On a
               transparent Prism pixel a large spread is expected and is the
               measurement of "the page backdrop leaked into the blend";
  profile      absolute difference along a 1-D scan, minimised over a +/-1 px
               shift.  A one-pixel rasterisation offset is not a compositing
               error, and the profile metric says so explicitly instead of
               hiding it inside a loose tolerance.

Inputs : acceptance/scenes/probes.json
         acceptance/artifacts/manifest.json + *.png
         acceptance/observations/render/viewers.json + captures
Output : acceptance/observations/measurements.json
"""
import json
import pathlib
import sys

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "acceptance" / "artifacts"
OBS = ROOT / "acceptance" / "observations"
RENDER = OBS / "render"
PAGE_RGB = {"white": (255, 255, 255), "black": (0, 0, 0), "green": (0, 255, 0)}
PAGES = tuple(PAGE_RGB)


def load_prism(scene):
    return Image.open(ARTIFACTS / f"{scene}.png").convert("RGBA")


def over(page, bg):
    base = Image.new("RGBA", page.size, PAGE_RGB[bg] + (255,))
    return Image.alpha_composite(base, page)


def _comp_over(page, rgb):
    base = Image.new("RGBA", page.size, tuple(rgb) + (255,))
    return Image.alpha_composite(base, page)


def chan_delta(a, b):
    return max(abs(int(x) - int(y)) for x, y in zip(a[:3], b[:3]))


def spread(values):
    return max(max(abs(x - y) for x, y in zip(a, b))
               for a in values for b in values)


def scan_metrics(comp, capture, scan, shifts=(-1, 0, 1)):
    y = scan["y"]
    per_shift = {}
    for s in shifts:
        worst = 0
        for x in range(scan["x0"], scan["x1"] + 1):
            xv = x + s
            if xv < 0 or xv >= capture.size[0]:
                worst = 999
                break
            worst = max(worst, chan_delta(comp.getpixel((x, y)), capture.getpixel((xv, y))))
        per_shift[s] = worst
    best = min(per_shift, key=lambda s: (per_shift[s], abs(s)))
    return {"best_shift": best, "min_delta": per_shift[best],
            "zero_shift_delta": per_shift[0], "per_shift": per_shift}


def alpha_delta(page, capture):
    if page.size != capture.size:
        return {"comparable": False,
                "reason": f"size {capture.size} != {page.size}"}
    worst, at = 0, None
    for y in range(page.size[1]):
        for x in range(page.size[0]):
            d = abs(page.getpixel((x, y))[3] - capture.getpixel((x, y))[3])
            if d > worst:
                worst, at = d, [x, y]
    return {"comparable": True, "max_delta": worst, "at": at}


def recover_straight(px_white, px_black):
    """Recover (alpha, straight RGB) from two captures over white and black.

    For an unassociated (straight) output pixel C over page P:
        O = a*C + (1-a)*P
    so with P=white and P=black:
        a = 1 - (O_black - O_white) / 255        (per channel; median for noise)
        C = (O_white - (1-a)*255) / a
    This lets a viewer without an alpha channel still be tested for
    premultiplication: if its colour interpolation is wrong the recovered
    straight colour will not stay on the intended ramp.
    """
    a_by_ch = [(255 - (w - b)) / 255.0 for w, b in zip(px_white[:3], px_black[:3])]
    a_by_ch.sort()
    a = a_by_ch[1]
    if a <= 0.004:
        return {"alpha": 0.0, "rgb": None}
    rgb = []
    for w in px_white[:3]:
        v = (w - (1 - a) * 255.0) / a
        rgb.append(max(0.0, min(255.0, v)))
    return {"alpha": round(a, 3), "rgb": [round(v, 1) for v in rgb]}


def main():
    probes_doc = json.loads((ROOT / "acceptance/scenes/probes.json").read_text())
    probes, scans = probes_doc["probes"], probes_doc["edge_scans"]
    manifest = json.loads((ARTIFACTS / "manifest.json").read_text())
    viewers_doc = json.loads((RENDER / "viewers.json").read_text())
    scenes = sorted(manifest["scenes"])

    out = {"generated_at": viewers_doc["generated_at"],
           "basis": "Prism PNG composited over the same page colour as the viewer capture",
           "scenes": {}, "viewers": {}}
    for scene in scenes:
        page = load_prism(scene)
        out["scenes"][scene] = {
            "title": manifest["scenes"][scene]["title"],
            "png_digest": manifest["scenes"][scene]["renders"]["png"]["digest"],
            "svg_digest": manifest["scenes"][scene]["renders"]["svg"]["digest"],
            "background_alpha": page.getpixel((0, 0))[3],
            "size": list(page.size),
        }

    for v in viewers_doc["viewers"]:
        entry = {"version": v.get("version"), "available": bool(v.get("ok")),
                 "alpha_capture": bool(v.get("alpha")),
                 "alpha_error": v.get("alpha_error", ""),
                 "embed_modes": v.get("embed_modes", []),
                 "error": (v.get("error") or "").split("\n")[0][:300],
                 "scenes": {}}
        out["viewers"][v["name"]] = entry
        if not v.get("ok"):
            continue
        vdir = RENDER / v["dir"]
        for scene in scenes:
            page = load_prism(scene)
            captures, missing = {}, []
            for bg in PAGES:
                p = vdir / f"{scene}.{bg}.png"
                if p.exists():
                    captures[bg] = Image.open(p).convert("RGBA")
                else:
                    missing.append(f"{scene}.{bg}.png")
            alpha_path = vdir / f"{scene}.alpha.png"
            alpha_cap = (Image.open(alpha_path).convert("RGBA")
                         if alpha_path.exists() else None)
            img_caps = {}
            for bg in PAGES:
                p = vdir / f"{scene}.img.{bg}.png"
                if p.exists():
                    img_caps[bg] = Image.open(p).convert("RGBA")
            # "post" means: take the viewer's alpha-preserving capture and
            # composite it ourselves.  This separates a viewer that computes
            # blending correctly but whose *background option* writes into the
            # blend (resvg --background) from a viewer that is simply wrong.
            post = ({bg: over(alpha_cap, bg) for bg in PAGES}
                    if alpha_cap is not None and alpha_cap.size == page.size else {})
            s = {"missing": missing, "probes": {}, "scans": [],
                 "own_page_spread_opaque": 0, "own_page_spread_transparent": 0,
                 "img_page_spread_opaque": 0, "img_page_spread_transparent": 0,
                 "post_page_spread_opaque": 0, "post_page_spread_transparent": 0}

            def spreads(bucket_prefix, caps):
                opaque = transparent = 0
                for pr in probes[scene]:
                    vals = [caps[bg].getpixel((pr["x"], pr["y"])) for bg in caps]
                    sp = spread(vals)
                    if page.getpixel((pr["x"], pr["y"]))[3] == 255:
                        opaque = max(opaque, sp)
                    else:
                        transparent = max(transparent, sp)
                s[f"{bucket_prefix}opaque"] = opaque
                s[f"{bucket_prefix}transparent"] = transparent

            if captures:
                spreads("own_page_spread_", captures)
            if img_caps:
                spreads("img_page_spread_", img_caps)
            if post:
                spreads("post_page_spread_", post)

            for pr in probes[scene]:
                rec = {"x": pr["x"], "y": pr["y"], "question": pr["question"],
                       "prism_alpha": page.getpixel((pr["x"], pr["y"]))[3],
                       "per_page": {}, "post_page": {}, "img_page": {}}
                for bg, cap in captures.items():
                    comp = over(page, bg)
                    p, c = comp.getpixel((pr["x"], pr["y"])), cap.getpixel((pr["x"], pr["y"]))
                    rec["per_page"][bg] = {"prism": list(p), "viewer": list(c),
                                           "delta": chan_delta(p, c)}
                for bg, cap in post.items():
                    p, c = over(page, bg).getpixel((pr["x"], pr["y"])), cap.getpixel((pr["x"], pr["y"]))
                    rec["post_page"][bg] = {"prism": list(p), "viewer": list(c),
                                            "delta": chan_delta(p, c)}
                for bg, cap in img_caps.items():
                    p, c = over(page, bg).getpixel((pr["x"], pr["y"])), cap.getpixel((pr["x"], pr["y"]))
                    rec["img_page"][bg] = {"prism": list(p), "viewer": list(c),
                                           "delta": chan_delta(p, c)}
                def best(key):
                    return (min(x["delta"] for x in rec[key].values()) if rec[key] else None)
                def worst(key):
                    return (max(x["delta"] for x in rec[key].values()) if rec[key] else None)
                rec["best_delta"] = best("per_page")
                rec["worst_delta"] = worst("per_page")
                rec["best_post_delta"] = best("post_page")
                rec["worst_post_delta"] = worst("post_page")
                rec["best_img_delta"] = best("img_page")
                rec["worst_img_delta"] = worst("img_page")
                s["probes"][pr["name"]] = rec
            for scan in scans.get(scene, []):
                for bg, cap in captures.items():
                    r = scan_metrics(over(page, bg), cap, scan)
                    r.update({"page": bg, "name": scan["name"],
                              "y": scan["y"], "x0": scan["x0"], "x1": scan["x1"],
                              "question": scan["question"], "mode": "own"})
                    s["scans"].append(r)
                for bg, cap in post.items():
                    r = scan_metrics(over(page, bg), cap, scan)
                    r.update({"page": bg, "name": scan["name"],
                              "y": scan["y"], "x0": scan["x0"], "x1": scan["x1"],
                              "question": scan["question"], "mode": "post"})
                    s["scans"].append(r)
            s["alpha"] = (alpha_delta(page, alpha_cap)
                          if alpha_cap is not None else {"comparable": False, "reason": "no alpha capture"})
            # ---------------------------------------------------- doc mode --
            # A standalone SVG document is shown over the browser's own page,
            # which CSS cannot set.  Recover what that page colour actually was
            # at a pixel the document leaves fully transparent (the Prism alpha
            # is 0 there), then composite the Prism PNG over *that* colour.
            # This is the only honest way to score "double-click the file".
            s["doc"] = {"comparable": False}
            doc_img = vdir / f"{scene}.doc.png"
            doc_alpha = vdir / f"{scene}.doc.alpha.png"
            bg_probe = next((pr for pr in probes[scene]
                             if page.getpixel((pr["x"], pr["y"]))[3] == 0), None)
            if doc_img.exists():
                di = Image.open(doc_img).convert("RGBA")
                if bg_probe is not None:
                    # Part of the document is transparent, so the page colour
                    # is visible and must be recovered before compositing.
                    page_rgb = list(di.getpixel((bg_probe["x"], bg_probe["y"]))[:3])
                    source = bg_probe["name"]
                elif page.getpixel((0, 0))[3] == 255:
                    # The document owns every pixel; the page cannot show
                    # through, so its colour is irrelevant and comparison is
                    # direct.
                    page_rgb = [255, 255, 255]
                    source = "document is fully opaque"
                else:
                    page_rgb = None
                    source = None
                if page_rgb is not None:
                    recs = {}
                    for pr in probes[scene]:
                        comp = _comp_over(page, page_rgb).getpixel((pr["x"], pr["y"]))
                        c = di.getpixel((pr["x"], pr["y"]))
                        recs[pr["name"]] = {"prism": list(comp), "viewer": list(c),
                                            "delta": chan_delta(comp, c)}
                    deltas = [r["delta"] for r in recs.values()]
                    s["doc"] = {"comparable": True,
                                "inferred_page_rgb": page_rgb,
                                "inferred_from": source,
                                "probes": recs,
                                "best_delta": min(deltas), "worst_delta": max(deltas)}
                    if doc_alpha.exists():
                        s["doc"]["alpha"] = alpha_delta(page, Image.open(doc_alpha).convert("RGBA"))
            # Two-page reconstruction: works even for viewers with no alpha
            # channel, which is how Firefox and ImageMagick are tested.
            recon = {}
            for pr in probes[scene]:
                w = captures.get("white")
                b = captures.get("black")
                if w and b:
                    recon[pr["name"]] = recover_straight(
                        w.getpixel((pr["x"], pr["y"])), b.getpixel((pr["x"], pr["y"])))
                    recon[pr["name"]]["prism_alpha"] = page.getpixel((pr["x"], pr["y"]))[3]
            s["recovered_straight"] = recon
            entry["scenes"][scene] = s

    (OBS / "measurements.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"measurements": str(OBS / "measurements.json"),
                      "viewers": {k: v["available"] for k, v in out["viewers"].items()}},
                     ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
