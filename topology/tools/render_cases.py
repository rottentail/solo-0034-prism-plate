#!/usr/bin/env python3
"""Submit every casebook plate to a real service and record its PNG/SVG proofs.

The service is built from this repository and started on an ephemeral port with
an isolated data root.  No product source is modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CASES = HERE.parent / "cases"
OUT = HERE.parent / "out"
GO = os.environ.get("GO", "go")


def build_binary(dst: pathlib.Path):
    env = dict(os.environ, CGO_ENABLED="0", GOTOOLCHAIN="local")
    subprocess.run([GO, "build", "-trimpath", "-o", str(dst), "./cmd/prismplate"],
                   cwd=ROOT, check=True, env=env)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(url, method, path, data=None, status=200, raw=False):
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
    req = urllib.request.Request(url + path, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            code, payload, headers = r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        code, payload, headers = e.code, e.read(), dict(e.headers)
    if code != status:
        raise RuntimeError(f"{method} {path}: wanted {status}, got {code}: {payload[:300]!r}")
    return (payload, headers) if raw else json.loads(payload) if payload else None


def main() -> int:
    index = json.loads((CASES / "index.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="prism-topology-"))
    binary = tmp / "prismplate"
    build_binary(binary)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    log = (tmp / "service.log").open("w")
    proc = subprocess.Popen([str(binary), "-listen", f"127.0.0.1:{port}", "-data", str(tmp / "state")],
                            stdout=log, stderr=log)
    records = []
    try:
        for _ in range(200):
            if proc.poll() is not None:
                raise RuntimeError("service exited: " + (tmp / "service.log").read_text())
            try:
                urllib.request.urlopen(url + "/healthz", timeout=1).close()
                break
            except OSError:
                time.sleep(0.05)
        for entry in index["cases"]:
            cid = entry["id"]
            body = json.loads((CASES / entry["file"]).read_text())
            created = call(url, "POST", "/v1/plates", body, status=201)
            digest = created["revisions"][0]["digest"]
            rec = {"id": cid, "title": entry["title"], "group": entry["group"],
                   "scene_digest": digest, "artifacts": {}}
            for fmt in ("png", "svg"):
                art = call(url, "POST", f"/v1/plates/{cid}/render?revision=1&format={fmt}", status=201)
                payload, _ = call(url, "GET", "/v1/artifacts/" + art["id"], raw=True)
                obs = hashlib.sha256(payload).hexdigest()
                if obs != art["digest"]:
                    raise RuntimeError(f"{cid} {fmt}: digest mismatch")
                dest = OUT / f"{cid}.{fmt}"
                dest.write_bytes(payload)
                rec["artifacts"][fmt] = {"artifact": art["id"], "digest": art["digest"],
                                         "bytes": len(payload), "file": dest.name}
            records.append(rec)
            print(f"rendered {cid}")
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=45)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()
        log.close()
    (OUT / "manifest.json").write_text(json.dumps({
        "service_binary": "cmd/prismplate (this repository)",
        "build": "CGO_ENABLED=0 go build -trimpath",
        "records": records,
    }, ensure_ascii=False, indent=2) + "\n")
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"wrote {OUT/'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
