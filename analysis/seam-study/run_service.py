#!/usr/bin/env python3
"""Run the seam plate through the real HTTP service for each offset and keep PNG/SVG.

Requires a built prismplate binary (or builds one on demand), like
scripts/walkthrough.py. For every offset the reported plate and a control plate
with the inner contour removed are rendered; the control is what the canvas
would look like if the hole really had disappeared.

Usage: python3 run_service.py [--binary PATH] [--keep DIR]
Writes, under analysis/seam-study/samples/:
  offset-<dx>.png/svg, nohole-<dx>.png/svg, responses-<dx>.json

Use a dedicated data root (--keep) because the plate ids are fixed.
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
OFFSETS = ("0", "0.25", "0.5")


def generate_scenes(target):
    """Call gen_scenes.main() in-process with target as its only argument."""
    import sys

    sys.path.insert(0, str(HERE))
    import gen_scenes

    saved = sys.argv
    sys.argv = ["gen_scenes.py", str(target)]
    try:
        gen_scenes.main()
    finally:
        sys.argv = saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", default=None)
    ap.add_argument("--keep", default=None, help="keep the data root here")
    args = ap.parse_args()

    tmp = tempfile.TemporaryDirectory(prefix="seam-study-") if args.keep is None else None
    base = pathlib.Path(args.keep) if args.keep else pathlib.Path(tmp.name)
    base.mkdir(parents=True, exist_ok=True)

    # Scenes are generated in a scratch dir outside the repository.
    scenes = pathlib.Path(tempfile.mkdtemp(prefix="seam-scenes-"))
    generate_scenes(scenes)

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

        def call(method, path, data=None, status=200, raw=False):
            body = json.dumps(data).encode() if data is not None else None
            req = urllib.request.Request(
                url + path,
                data=body,
                method=method,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=35) as r:
                    code, payload = r.status, r.read()
            except urllib.error.HTTPError as e:
                code, payload = e.code, e.read()
            if code != status:
                raise RuntimeError(
                    f"{method} {path}: expected {status}, got {code}: {payload[:300]!r}"
                )
            return payload if raw else json.loads(payload)

        out = HERE / "samples"
        out.mkdir(exist_ok=True)
        for dx in OFFSETS:
            entry = {"offset": float(dx)}
            for prefix, filename, key in (
                ("seam", f"offset-{dx}.json", "with_hole"),
                ("seam-nohole", f"nohole-{dx}.json", "no_hole_control"),
            ):
                plate = json.loads((scenes / filename).read_text())
                call("POST", "/v1/plates", plate, status=201)
                rec = {"plate": plate["id"]}
                for fmt in ("png", "svg"):
                    art = call(
                        "POST",
                        f"/v1/plates/{plate['id']}/render?revision=1&format={fmt}",
                        status=201,
                    )
                    raw = call("GET", "/v1/artifacts/" + art["id"], raw=True)
                    tag = "offset" if prefix == "seam" else "nohole"
                    (out / f"{tag}-{dx}.{fmt}").write_bytes(raw)
                    rec[fmt] = {
                        "artifact": art["id"],
                        "digest": art["digest"],
                        "scene_digest": art["scene_digest"],
                        "bytes": len(raw),
                    }
                entry[key] = rec
            (out / f"responses-{dx}.json").write_text(
                json.dumps(entry, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(entry, ensure_ascii=False))
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


if __name__ == "__main__":
    main()
