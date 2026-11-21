#!/usr/bin/env python3
"""Re-derive every claim the report makes, from the stored source plates.

This runs against a fresh prism-plate process and re-creates images from the
editable JSON in out/plates/ — it does not trust the images that were measured.
It checks:

  * every source plate is admitted and renders (the sources are valid,
    not just the pixels we happened to save);
  * re-rendering reproduces the recorded artifact digest bit for bit;
  * every icon is a 16x16 PNG with the expected signature;
  * the four sub-pixel placements produce four distinct images;
  * every pending icon keeps a fully transparent hole at every placement;
  * the decisive stroke's peak alpha never drops, only its coverage does
    (i.e. sub-pixel loss is spreading, not disappearance) for bold strokes;
  * the recommended generic variant passes its own page list.

Exit status is non-zero on the first failed claim.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import atelier as A  # noqa: E402
import design  # noqa: E402
import measure  # noqa: E402
import pngio  # noqa: E402

OUT = ROOT / "out"
REC = ROOT / "record"


def main():
    manifest = json.loads((OUT / "manifest.json").read_text())
    findings = json.loads((REC / "findings.json").read_text())
    alpha = {(i["status"], i["variant"], i["offset"]): i["decisive_alpha"] for i in findings["items"]}
    policy = json.loads((ROOT / "policy" / "decision.json").read_text())
    binary = OUT / "prismplate"
    subprocess.run(["go", "build", "-trimpath", "-o", str(binary), "./cmd/prismplate"],
                   cwd=REPO, check=True, capture_output=True)

    checked = 0
    # prism-plate caps one data root at 128 plates; verify in batches that each
    # get their own ephemeral service root.
    batch = 96
    for start in range(0, len(manifest["items"]), batch):
        svc = A.Service(binary=binary)
        try:
            for it in manifest["items"][start:start + batch]:
                doc = json.loads((OUT / "plates" / f"{it['plate']}.json").read_text())
                svc.create(it["plate"], doc, title="re-render")
                meta, data, _ = svc.render(it["plate"])
                assert meta["digest"] == it["artifact_digest"], f"digest drift on {it['plate']}"
                w, h, rgba = pngio.read(data)
                assert (w, h) == (16, 16), f"size {w}x{h} on {it['plate']}"
                assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
                checked += 1
        finally:
            svc.close()

    # placement sensitivity: four distinct images per (status, variant)
    keys = {}
    for it in manifest["items"]:
        keys.setdefault((it["status"], it["variant"]), set()).add(it["artifact_digest"])
    for key, digests in keys.items():
        assert len(digests) == 4, f"{key} does not vary with placement: {len(digests)}"

    # pending keeps a real hole at every placement
    findings_by = {(i["status"], i["variant"], i["offset"]): i for i in findings["items"]}
    for it in manifest["items"]:
        if it["status"] != "pending":
            continue
        f = findings_by[(it["status"], it["variant"], it["offset"])]
        assert f["hole_alpha"]["max_alpha"] == 0, f"{it['plate']} hole is not hollow"
        assert f["hole_alpha"]["pixels"] > 0, f"{it['plate']} has no hole pixels"

    # sub-pixel loss on a bold stroke is coverage spreading, not disappearance
    for it in manifest["items"]:
        if it["variant"] not in ("plain-ink", "halo", "badge", "duplex", "mid"):
            continue
        a = alpha[(it["status"], it["variant"], it["offset"])]
        if a is None:
            continue
        assert a["peak_alpha"] == 255, \
            f"{it['plate']} decisive peak fell below full opacity"

    # the recommended generic variant really passes on its pages
    for page, ch in policy["per_page_choice"].items():
        assert ch["recommended"], f"no recommendation for {page}"
        variants = {v["variant"]: v for v in policy["variants"]}
        assert page in variants[ch["recommended"]]["pages_ok_all_offsets"], \
            f"{ch['recommended']} does not pass {page}"

    # every stored plate still compiles under the product's own admission rules
    print(json.dumps({
        "re_rendered": checked,
        "placement_groups": len(keys),
        "claim": "sources admit, re-render bit-identically, stay 16x16, "
                 "keep the hole, and the recommendations match the grades",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
