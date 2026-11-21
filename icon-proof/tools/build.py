#!/usr/bin/env python3
"""Render the whole icon proof set through the real prism-plate service.

For every (status, variant, sub-pixel offset) this renders the icon itself and
one *local-backdrop* plate per drawn part: the same document with every shape
below that part and nothing at or above it. Those prefix renders are real
service output, so a part's contrast is always measured against the thing that
actually sits behind it rather than against a white page.

Outputs
-------
out/plates/*.json      canonical editable source documents (what the terminal stores)
out/instruments/*.json measurement-plate sources (prefix backdrops, reduced, probes)
out/icons/*.png        the deliverable icons
out/local/*.png        prefix renders (measurement instruments)
out/reduced/*.png      same plate with the decisive part removed / hole filled
out/probes/*.png       axis-alignment probes
out/contact/*.png      contact sheets on the four required page backdrops
out/manifest.json      plate ids, scene/artifact digests, hashes, probe profiles
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import atelier as A  # noqa: E402
import design  # noqa: E402
import measure  # noqa: E402
import pngio  # noqa: E402

OUT = ROOT / "out"
REPO = ROOT.parent
PAGES = ["light", "dark", "checker", "mid"]


def offset_tag(dx, dy):
    return f"dx{dx:.1f}_dy{dy:.1f}".replace(".", "p")


def _write_plate(svc, pid, doc, kind="plates"):
    """Persist the source document and render it through the service.

    `plates/` holds the deliverable, editable sources (one per icon). The
    measurement instruments (local-backdrop prefixes and reduced plates) go to
    `instruments/` so a reader can tell the artwork from the rulers.
    """
    (OUT / kind / f"{pid}.json").write_text(
        json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    svc.create(pid, doc, title="prism icon proof")
    return svc.render(pid)


def _one_icon(svc, status, variant, dx, dy, manifest):
    tag = offset_tag(dx, dy)
    pid = f"icon-{status}-{variant}-{tag}"
    built = design.build(status, variant, dx, dy)
    meta, data, _ = _write_plate(svc, pid, built["document"])
    (OUT / "icons" / f"{pid}.png").write_bytes(data)
    w, h, rgba = pngio.read(data)
    item = {
        "status": status, "variant": variant, "dx": dx, "dy": dy,
        "offset": tag, "plate": pid,
        "scene_digest": meta["scene_digest"],
        "artifact": meta["id"], "artifact_digest": meta["digest"],
        "png": f"icons/{pid}.png",
        "png_sha256": hashlib.sha256(data).hexdigest(),
        "size": [w, h],
        "slots": [p["slot"] for p in built["parts"]],
        "decisive": sorted(design.DECISIVE[status]),
        "hole": None,
        "local": {},
        "reduced": {},
    }
    if status == "pending":
        import measure
        item["hole"] = measure.hole_alpha(w, h, rgba, built["masks"]["hole_core"])

    # local backdrops: every prefix of the draw order, rendered for real
    for i, part in enumerate(built["parts"]):
        lp = f"local-{pid}-{i}"
        lm, ld, _ = _write_plate(svc, lp, design.prefix_document(built, part["slot"]), "instruments")
        (OUT / "local" / f"{lp}.png").write_bytes(ld)
        item["local"][part["slot"]] = {"plate": lp, "png": f"local/{lp}.png",
                                       "artifact": lm["id"], "digest": lm["digest"]}

    # reduced plate: decisive part removed (pending: its hole filled in)
    if status == "pending":
        rdoc, rname = design.filled_hole_document(built), "filled"
    else:
        dec = sorted(design.DECISIVE[status])[0]
        rdoc, rname = design.scene_without_slot(built, dec), "nodecide"
    rp = f"reduced-{pid}-{rname}"
    rm, rd, _ = _write_plate(svc, rp, rdoc, "instruments")
    (OUT / "reduced" / f"{rp}.png").write_bytes(rd)
    item["reduced"] = {"plate": rp, "png": f"reduced/{rp}.png",
                       "artifact": rm["id"], "digest": rm["digest"]}
    manifest["items"].append(item)


def icons(binary, manifest):
    # prism-plate caps one data root at 128 plates / 256 artifacts and every
    # measurement instrument is itself a real plate, so each variant gets its
    # own ephemeral service root.
    for status in design.STATUSES:
        for variant in design.VARIANTS:
            svc = A.Service(binary=binary)
            try:
                for dx, dy in design.OFFSETS:
                    _one_icon(svc, status, variant, dx, dy, manifest)
            finally:
                svc.close()


def probes(binary, manifest):
    svc = A.Service(binary=binary)
    try:
        for orientation in ("h", "v"):
            for width in (design.HAIRLINE, design.W_FINE):
                for dx, dy in design.OFFSETS:
                    tag = offset_tag(dx, dy)
                    pid = f"probe-{orientation}-w{str(width).replace('.', 'p')}-{tag}"
                    probe = design.axis_probe(orientation, width, dx, dy)
                    meta, data, _ = _write_plate(svc, pid, probe["document"], "instruments")
                    (OUT / "probes" / f"{pid}.png").write_bytes(data)
                    w, h, rgba = pngio.read(data)
                    lit = [(x, y, rgba[(y * w + x) * 4 + 3]) for y in range(h) for x in range(w)
                           if rgba[(y * w + x) * 4 + 3] > 0]
                    if orientation == "h":
                        spans = len({y for _, y, _ in lit})
                        along = len({x for x, _, _ in lit})
                    else:
                        spans = len({x for x, _, _ in lit})
                        along = len({y for _, y, _ in lit})
                    manifest["probes"].append({
                        "plate": pid, "orientation": orientation, "width": width,
                        "dx": dx, "dy": dy, "artifact": meta["id"], "digest": meta["digest"],
                        "peak_alpha": max((a for *_, a in lit), default=0),
                        "opaque_fraction": round(sum(1 for *_, a in lit if a >= 250) / len(lit), 3) if lit else 0,
                        "cross_axis_spans": spans,
                        "along_axis_pixels": along,
                        "png_sha256": hashlib.sha256(data).hexdigest(),
                    })
    finally:
        svc.close()


def _over_at(rgba, x, y, w, bg):
    i = (y * w + x) * 4
    a = rgba[i + 3] / 255.0
    return (int(round(rgba[i] * a + bg[0] * (1 - a))),
            int(round(rgba[i + 1] * a + bg[1] * (1 - a))),
            int(round(rgba[i + 2] * a + bg[2] * (1 - a))))


def contact_sheets(manifest):
    """One sheet per page backdrop and offset: rows = status, columns = variant."""
    idx = {(i["status"], i["variant"], i["offset"]): i for i in manifest["items"]}
    cell, gap, pad = 16, 2, 4
    cols, rows = len(design.VARIANTS), len(design.STATUSES)
    W = pad * 2 + cols * (cell + gap) - gap
    H = pad * 2 + rows * (cell + gap) - gap
    for page in measure.BACKDROPS:
        bgfn = measure.BACKDROPS[page]
        for offset in ("dx0p0_dy0p0", "dx0p5_dy0p5"):
            buf = bytearray(W * H * 4)
            for y in range(H):
                for x in range(W):
                    i = (y * W + x) * 4
                    buf[i:i + 4] = bytes((*bgfn(x, y), 255))
            for ri, status in enumerate(design.STATUSES):
                for ci, variant in enumerate(design.VARIANTS):
                    it = idx[(status, variant, offset)]
                    w, h, rgba = pngio.read(OUT / it["png"])
                    ox, oy = pad + ci * (cell + gap), pad + ri * (cell + gap)
                    for y in range(h):
                        for x in range(w):
                            di = ((oy + y) * W + ox + x) * 4
                            bg = bgfn(ox + x, oy + y)
                            buf[di:di + 4] = bytes((*_over_at(rgba, x, y, w, bg), 255))
            pngio.write(OUT / "contact" / f"grid_{page}_{offset}.png", W, H, bytes(buf))


def main():
    for d in ("plates", "instruments", "icons", "local", "reduced", "contact", "probes"):
        (OUT / d).mkdir(parents=True, exist_ok=True)
        # a rerun must not leave outputs from a previous variant list behind
        for stale in (OUT / d).glob("*"):
            if stale.is_file():
                stale.unlink()
    binary = OUT / "prismplate"
    subprocess.run(["go", "build", "-trimpath", "-o", str(binary), "./cmd/prismplate"],
                   cwd=REPO, check=True, capture_output=True)
    manifest = {"schema": "prism-icon-proof-v2", "items": [], "probes": []}
    icons(binary, manifest)
    probes(binary, manifest)
    contact_sheets(manifest)
    for it in manifest["items"]:
        for slot in it["local"]:
            it["local"][slot].pop("_rgba", None)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"icons={len(manifest['items'])} probes={len(manifest['probes'])}")


if __name__ == "__main__":
    main()
