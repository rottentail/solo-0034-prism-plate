#!/usr/bin/env python3
"""End-to-end verification against the real running prismplate HTTP service.

For every reproduction document under investigation/repro/ it:
  1. creates the plate,
  2. renders BOTH png and svg through the real service,
  3. downloads both artifacts into investigation/results/<plate>/,
  4. records the exact SVG text and the PNG pixel-coverage statistics,
  5. recomputes the viewer-side geometry from the real SVG tokens.

Usage:
  python3 -I scripts/verify_on_service.py [base_url]
The default base_url is http://127.0.0.1:7846.
Stdlib only; safe to run repeatedly (plate ids must not already exist).
"""

import json
import os
import pathlib
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPRO = ROOT / "investigation" / "repro"
RESULTS = ROOT / "investigation" / "results"
SCRIPTS = ROOT / "scripts"


def call(method, url, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def run(argv):
    return subprocess.run([sys.executable, "-I", *argv],
                          capture_output=True, text=True, check=True).stdout


def main(base="http://127.0.0.1:7846"):
    summary = []
    for doc_path in sorted(REPRO.glob("*.json")):
        doc = json.loads(doc_path.read_text())
        plate = doc["id"]
        outdir = RESULTS / plate
        outdir.mkdir(parents=True, exist_ok=True)

        status, body = call("POST", f"{base}/v1/plates", doc)
        # 409 means a previous run already created this plate; render is
        # content-addressed and deterministic, so re-rendering is safe.
        assert status in (201, 409), (status, body)
        artifacts = {}
        for fmt in ("png", "svg"):
            status, body = call(
                "POST",
                f"{base}/v1/plates/{plate}/render?revision=1&format={fmt}")
            assert status == 201, (status, body)
            meta = json.loads(body)
            status, blob = call("GET", f"{base}/v1/artifacts/{meta['id']}")
            assert status == 200
            target = outdir / f"artifact.{fmt}"
            target.write_bytes(blob)
            artifacts[fmt] = {"id": meta["id"], "digest": meta["digest"],
                              "bytes": len(blob)}

        png_stats = run([str(SCRIPTS / "png_coverage.py"),
                         str(outdir / "artifact.png")])
        viewer_stats = run([str(SCRIPTS / "svg_viewer_check.py"),
                            str(outdir / "artifact.svg")])
        (outdir / "png-coverage.txt").write_text(png_stats)
        (outdir / "viewer-geometry.txt").write_text(viewer_stats)
        summary.append({"plate": plate, "source": doc_path.name,
                        "artifacts": artifacts,
                        "svg_text": (outdir / "artifact.svg").read_text()})
        print(f"verified {plate}")

    (RESULTS / "service-verification.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nwrote {RESULTS / 'service-verification.json'}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:7846")
