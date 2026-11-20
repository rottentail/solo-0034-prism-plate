#!/usr/bin/env python3
"""HTTP-level sweep: render many sub-pixel offsets through the real service and
verify the PNGs against the independent mirror.

For every offset it submits the reported plate and the no-hole control plate,
renders both as PNG and SVG, and records:

  * PNG pixels vs. analyze.render_png_pixels (independent re-derivation)
  * number of observed hole pixels (all four samples inside the outer
    silhouette, at least one inside the inner contour, alpha < 255)
  * whether the reported PNG is byte-identical to the no-hole control
  * whether the SVG still carries the inner contour and the exact transform

The output (default analysis/seam-study/http-sweep.json) is evidence that the
blind bands are a property of the running service, not just of the offline
model.

Usage: python3 verify_service.py [--binary PATH] [--step 0.05]
"""
import argparse
import json
import pathlib
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]

import sys

sys.path.insert(0, str(HERE))
import analyze  # noqa: E402
import gen_scenes  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", default=None)
    ap.add_argument("--step", type=float, default=0.05)
    ap.add_argument("--out", default=str(HERE / "http-sweep.json"))
    ap.add_argument("--keep", default=None)
    args = ap.parse_args()

    tmp = tempfile.TemporaryDirectory(prefix="seam-verify-") if args.keep is None else None
    base = pathlib.Path(args.keep) if args.keep else pathlib.Path(tmp.name)
    base.mkdir(parents=True, exist_ok=True)

    binary = args.binary
    if not binary:
        binary = str(base / "prismplate")
        subprocess.run(["go", "build", "-o", binary, "./cmd/prismplate"], cwd=ROOT, check=True)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    url = f"http://127.0.0.1:{port}"
    log = (base / "service.log").open("w")
    proc = subprocess.Popen(
        [binary, "-listen", f"127.0.0.1:{port}", "-data", str(base / "state")],
        stdout=log,
        stderr=log,
    )
    try:
        for _ in range(200):
            if proc.poll() is not None:
                raise RuntimeError("service exited")
            try:
                urllib.request.urlopen(url + "/healthz", timeout=1).close()
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("startup timed out")

        def call(method, path, data=None, status=201):
            body = json.dumps(data).encode() if data is not None else None
            req = urllib.request.Request(
                url + path, data=body, method=method,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=35) as r:
                    code, payload = r.status, r.read()
            except urllib.error.HTTPError as e:
                code, payload = e.code, e.read()
            if code != status:
                raise RuntimeError(f"{method} {path}: {code}: {payload[:300]!r}")
            return payload if status == 200 else json.loads(payload)

        def plate_at(tag, dx, with_hole):
            return {
                "id": ("seam-v" if with_hole else "nohole-v") + f"-{tag}",
                "title": "verify",
                "document": gen_scenes.document(dx, with_hole),
            }

        def render(plate, fmt):
            call("POST", "/v1/plates", plate)
            art = call(
                "POST", f"/v1/plates/{plate['id']}/render?revision=1&format={fmt}"
            )
            with urllib.request.urlopen(
                url + "/v1/artifacts/" + art["id"], timeout=35
            ) as r:
                return art, r.read()

        rows_out = []
        n = int(round(1.0 / args.step))
        for i in range(n):  # 0 .. 1-step, skipping the duplicate at 1.0
            dx = round(i * args.step, 6)
            tag = str(i)
            with_art, with_png = render(plate_at(tag, dx, True), "png")
            _, control_png = render(plate_at(tag, dx, False), "png")
            _, svg = render(plate_at("svg-" + tag, dx, True), "svg")
            width_height, disk = analyze.read_png_bytes(with_png)
            mirror_rows, _ = analyze.render_png_pixels(dx)
            mirror = [mirror_rows[y][x] for y in range(48) for x in range(48)]
            flat = [px for row in disk for px in row]
            hole = analyze.hole_pixels(disk, dx)
            inner_svg = "22.1 12L22.4 12L22.4 36L22.1 36Z"
            rows_out.append({
                "offset": dx,
                "png_digest": with_art["digest"],
                "scene_digest": with_art["scene_digest"],
                "png_size": width_height,
                "png_matches_mirror": flat == mirror,
                "observed_hole_pixels": len(hole),
                "hole_columns": sorted({p["x"] for p in hole}),
                "hole_alphas": sorted({p["a"] for p in hole}),
                "png_equals_no_hole_control": with_png == control_png,
                "svg_keeps_inner_contour": inner_svg.encode() in svg,
                "svg_has_expected_transform": (
                    f"matrix(1 0 0 1 {dx:g} 0)".encode() in svg
                ),
            })
        payload = {
            "step": args.step,
            "offsets": rows_out,
            "transitions": transitions(rows_out),
        }
        pathlib.Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"step": args.step, "offsets": len(rows_out),
                          "transitions": payload["transitions"]}, indent=2))
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=45)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        log.close()
        if tmp is not None:
            tmp.cleanup()


def transitions(rows):
    """Visible/hidden spans across the sampled offsets."""
    spans = []
    for row in rows:
        vis = row["observed_hole_pixels"] > 0
        if spans and spans[-1]["visible"] == vis:
            spans[-1]["to"] = row["offset"]
        else:
            spans.append({"visible": vis, "from": row["offset"], "to": row["offset"]})
    return spans


if __name__ == "__main__":
    main()
