#!/usr/bin/env python3
"""Capture real Prism Plate output for the acceptance scenes.

Starts the actual Go binary on a loopback port with a temporary data root,
creates every scene, renders PNG and SVG, and stores the fetched artifact bytes
plus the artifact metadata that names the source revision and digest.

    python3 acceptance/harness/capture.py --go /path/to/go

Requires only the Go toolchain.  No network.
"""
import argparse
import hashlib
import json
import os
import pathlib
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENES = ROOT / "acceptance" / "scenes"
ARTIFACTS = ROOT / "acceptance" / "artifacts"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def call(url, method, path, data=None, status=200):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url + path, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", default=os.environ.get("GO", "go"))
    ap.add_argument("--keep", action="store_true", help="keep the temporary data root")
    args = ap.parse_args()

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="prism-accept-")
    binary = pathlib.Path(tmp) / "prismplate"
    subprocess.run([args.go, "build", "-o", str(binary), "./cmd/prismplate"],
                   cwd=ROOT, check=True)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    log = open(pathlib.Path(tmp) / "service.log", "w")
    proc = subprocess.Popen([str(binary), "-listen", f"127.0.0.1:{port}",
                             "-data", str(pathlib.Path(tmp) / "state")],
                            stdout=log, stderr=log)
    manifest = {"service": {}, "scenes": {}}
    try:
        for _ in range(200):
            if proc.poll() is not None:
                raise RuntimeError("service exited during startup")
            try:
                urllib.request.urlopen(url + "/healthz", timeout=1).close()
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("service did not become healthy")
        ver = subprocess.run([args.go, "version"], capture_output=True, text=True).stdout.strip()
        manifest["service"] = {"url": url, "go": ver, "data_root": tmp}

        for scene_file in sorted(SCENES.glob("*.json")):
            if scene_file.name == "probes.json":
                continue
            scene = json.loads(scene_file.read_text())
            status, body, _ = call(url, "POST", "/v1/plates", scene)
            if status != 201:
                raise RuntimeError(f"create {scene['id']}: {status} {body[:300]!r}")
            plate = json.loads(body)
            rev = plate["revisions"][0]
            entry = {"title": scene["title"], "scene_file": f"acceptance/scenes/{scene_file.name}",
                     "plate_id": scene["id"], "revision": 1, "scene_digest": rev["digest"],
                     "renders": {}}
            for fmt in ("png", "svg"):
                status, body, _ = call(url, "POST",
                                       f"/v1/plates/{scene['id']}/render?revision=1&format={fmt}")
                if status != 201:
                    raise RuntimeError(f"render {scene['id']} {fmt}: {status} {body[:300]!r}")
                meta = json.loads(body)
                status, raw, headers = call(url, "GET", "/v1/artifacts/" + meta["id"])
                if status != 200:
                    raise RuntimeError(f"fetch {meta['id']}: {status}")
                lc = {k.lower(): v for k, v in headers.items()}
                digest = hashlib.sha256(raw).hexdigest()
                if digest != meta["digest"]:
                    raise RuntimeError(f"digest mismatch for {meta['id']}")
                if lc.get("etag") != '"' + digest + '"':
                    raise RuntimeError(f"ETag mismatch for {meta['id']}: {lc.get('etag')!r}")
                if lc.get("x-scene-digest") != rev["digest"]:
                    raise RuntimeError(f"X-Scene-Digest mismatch for {meta['id']}")
                out = ARTIFACTS / f"{scene['id']}.{fmt}"
                out.write_bytes(raw)
                entry["renders"][fmt] = {
                    "artifact_id": meta["id"], "digest": digest,
                    "file": f"acceptance/artifacts/{out.name}",
                    "bytes": len(raw), "etag": lc.get("etag"),
                    "x_scene_digest": lc.get("x-scene-digest"),
                    "content_type": lc.get("content-type"),
                    "content_security_policy": lc.get("content-security-policy"),
                }
                # Second render must be byte-identical; record it as evidence.
                status, body, _ = call(url, "POST",
                                       f"/v1/plates/{scene['id']}/render?revision=1&format={fmt}")
                again = json.loads(body)
                entry["renders"][fmt]["repeat_digest_equal"] = again["digest"] == digest
            manifest["scenes"][scene["id"]] = entry
        manifest_path = ARTIFACTS / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"captured": len(manifest["scenes"]), "manifest": str(manifest_path)},
                         ensure_ascii=False))
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=45)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        log.close()
        if not args.keep:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
