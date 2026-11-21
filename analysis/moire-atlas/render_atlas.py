#!/usr/bin/env python3
"""Drive the real prismplate HTTP service to render the whole atlas.

Starts the production binary on an ephemeral port with an isolated data
root, creates one plate per scene, renders PNG and SVG through the public
API, downloads the artifacts and verifies content digests. No renderer
code is added or bypassed.
"""
import hashlib
import json
import pathlib
import signal
import socket
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
BINARY = pathlib.Path("/tmp/moire-work/bin/prismplate")


def wait_ready(url, proc):
    for _ in range(200):
        if proc.poll() is not None:
            raise RuntimeError("prismplate service exited early")
        try:
            urllib.request.urlopen(url + "/healthz", timeout=1).close()
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("startup timed out")


def call(url, method, path, data=None, status=200, raw=False):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        url + path, data=body, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        payload, code = e.read(), e.code
    if code != status:
        raise RuntimeError(f"{method} {path}: expected {status}, got {code}: {payload[:300]!r}")
    if raw:
        return payload, None
    return payload, json.loads(payload) if payload else None


def main():
    manifest = json.loads((ROOT / "cases.json").read_text())
    out_dir = ROOT / "artifacts"
    out_dir.mkdir(exist_ok=True)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    url = f"http://127.0.0.1:{port}"
    records = []
    with tempfile.TemporaryDirectory(prefix="moire-state-") as tmp:
        log = open(pathlib.Path(tmp) / "service.log", "w")
        proc = subprocess.Popen(
            [str(BINARY), "-listen", f"127.0.0.1:{port}",
             "-data", str(pathlib.Path(tmp) / "state")],
            stdout=log, stderr=log)
        try:
            wait_ready(url, proc)
            for case in manifest["cases"]:
                cid = case["id"]
                scene = json.loads((ROOT / "scenes" / f"{cid}.json").read_text())
                _, created = call(url, "POST", "/v1/plates", scene, status=201)
                assert created["revisions"][0]["number"] == 1
                scene_digest = created["revisions"][0]["digest"]
                rec = {"case": cid, "plate": scene["id"], "scene_digest": scene_digest,
                       "size": case["size"], "artifacts": {}}
                for fmt in ("png", "svg"):
                    _, meta = call(
                        url, "POST",
                        f"/v1/plates/{scene['id']}/render?revision=1&format={fmt}", status=201)
                    raw, _ = call(url, "GET", f"/v1/artifacts/{meta['id']}", status=200, raw=True)
                    digest = hashlib.sha256(raw).hexdigest()
                    assert digest == meta["digest"], "artifact digest mismatch"
                    target = out_dir / f"{cid}.{fmt}"
                    target.write_bytes(raw)
                    entry = {"id": meta["id"], "digest": digest, "bytes": len(raw)}
                    if fmt == "png":
                        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
                        w, h = struct.unpack(">II", raw[16:24])
                        entry["width"], entry["height"] = w, h
                        assert (w, h) == (case["size"], case["size"])
                    else:
                        assert b"<svg" in raw
                    rec["artifacts"][fmt] = entry
                records.append(rec)
                print(f"rendered {cid}")
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=45)
            log.close()
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "render-manifest.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2))
    print(f"OK: {len(records)} plates, both formats, digests verified")


if __name__ == "__main__":
    main()
