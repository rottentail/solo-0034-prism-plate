#!/usr/bin/env python3
"""Submit every audit source to the *production* service and archive its PNG.

The production binary is built from the untouched repository (go build of
./cmd/prismplate) and run against an isolated, throwaway data root on a
loopback port.  Nothing in the audit tree is imported by the service; this
script only speaks its documented HTTP JSON API.
"""

import hashlib
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
PACK = HERE.parent
REPO = PACK.parent.parent
SOURCES = PACK / "sources"
PNG_DIR = PACK / "png"
BIN = PACK / "bin" / "prismplate"


def http(url, method="GET", payload=None):
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main():
    import os
    go = os.environ.get("GO_BIN", "go")
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    BIN.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([go, "build", "-o", str(BIN), "./cmd/prismplate"],
                   cwd=REPO, check=True)

    with tempfile.TemporaryDirectory(prefix="prism-audit-") as tmp:
        root = pathlib.Path(tmp)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        url = f"http://127.0.0.1:{port}"
        log = (root / "service.log").open("wb")
        proc = subprocess.Popen([str(BIN), "-listen",
                                 f"127.0.0.1:{port}", "-data",
                                 str(root / "state")],
                                stdout=log, stderr=log)
        try:
            for _ in range(200):
                if proc.poll() is not None:
                    raise RuntimeError("service exited early")
                try:
                    urllib.request.urlopen(url + "/healthz", timeout=1).close()
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("startup timed out")

            manifest = []
            for src in sorted(SOURCES.glob("*.json")):
                doc = json.loads(src.read_text())
                status, raw = http(url + "/v1/plates", "POST", doc)
                if status != 201:
                    raise RuntimeError(
                        f"{src.name} rejected by production admission "
                        f"({status}): {raw[:300]!r}")
                status, raw = http(
                    f"{url}/v1/plates/{doc['id']}/render"
                    "?revision=1&format=png", "POST")
                if status != 201:
                    raise RuntimeError(
                        f"render failed for {src.name}: {raw[:300]!r}")
                job = json.loads(raw)
                status, png_bytes = http(
                    f"{url}/v1/artifacts/{job['id']}")
                if status != 200:
                    raise RuntimeError(f"artifact fetch failed: {status}")
                digest = hashlib.sha256(png_bytes).hexdigest()
                if digest != job["digest"]:
                    raise RuntimeError("digest mismatch on " + src.name)
                out = PNG_DIR / (src.stem + ".png")
                out.write_bytes(png_bytes)
                manifest.append({
                    "source": src.name,
                    "plate_id": doc["id"],
                    "artifact_id": job["id"],
                    "png": out.name,
                    "sha256": digest,
                    "bytes": len(png_bytes),
                    "admission": "production HTTP 201",
                })
                print(f"archived {out.name} ({len(png_bytes)} bytes)")
            (PNG_DIR / "production-manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=30)
            log.close()


if __name__ == "__main__":
    main()
