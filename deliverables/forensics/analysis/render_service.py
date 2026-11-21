"""Drive the real prismplate HTTP service through its existing contract only.

For every candidate recipe this creates one plate, appends one revision per
observation background, and renders every revision to PNG. Scene sources, PNG
bytes, artifact digests and scene digests are persisted so each image is
traceable back to an exact revision. No new production endpoint is used:
POST /v1/plates, PUT /v1/plates/{id}, POST .../render, GET /v1/artifacts/{id}.
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
import recipes

BINARY = os.environ.get("PRISMPLATE_BIN", "/home/node/tooling/prismplate")
OUT = os.path.abspath(os.path.join(HERE, ".."))
PLATES_DIR = os.path.join(OUT, "plates")
RENDERS_DIR = os.path.join(OUT, "renders")


def http(url, method="GET", data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null"), dict(e.headers)


def wait_health(base, proc):
    for _ in range(200):
        if proc.poll() is not None:
            raise RuntimeError("service exited early")
        try:
            urllib.request.urlopen(base + "/healthz", timeout=1).close()
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("startup timed out")


def build_candidates():
    """Yield (candidate_id, family, scene-builder) for every recipe."""
    for fam, recs in recipes.FAMILIES.items():
        for cid, rec in recs:
            yield fam, cid, ("solid", rec)
    for cid, mode in recipes.FAMILY_D:
        yield "D", cid, ("blank", mode)


def main():
    os.makedirs(PLATES_DIR, exist_ok=True)
    os.makedirs(RENDERS_DIR, exist_ok=True)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    base = f"http://127.0.0.1:{port}"
    data_root = os.path.join(OUT, ".service-state")
    proc = subprocess.Popen([BINARY, "-listen", f"127.0.0.1:{port}",
                             "-data", data_root],
                            stdout=open(os.path.join(OUT, "service.log"), "w"),
                            stderr=subprocess.STDOUT)
    manifest = []
    try:
        wait_health(base, proc)
        for fam, cid, spec in build_candidates():
            plate_id = f"fp-{cid.lower()}"
            for rev_idx, (bg_name, bg_rgb, bg_a) in enumerate(recipes.BACKGROUNDS, start=1):
                if spec[0] == "solid":
                    rec = spec[1]
                    doc = recipes.solid_rect_doc(
                        plate_id, f"forensics {cid} on {bg_name}",
                        rec["rgb"], rec["brush_a"], rec["opacity"], rec["blend"],
                        bg_rgb, bg_a)
                else:
                    doc = recipes.blank_layer_doc(
                        plate_id, f"forensics {cid} on {bg_name}",
                        spec[1], bg_rgb, bg_a)
                scene_path = os.path.join(
                    PLATES_DIR, f"{cid}__rev{rev_idx}__{bg_name}.json")
                with open(scene_path, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh, ensure_ascii=False, indent=2)
                if rev_idx == 1:
                    code, payload, _ = http(base + "/v1/plates", "POST", doc)
                    assert code == 201, (cid, code, payload)
                else:
                    code, payload, _ = http(
                        base + f"/v1/plates/{plate_id}",
                        "PUT", {"expected_revision": rev_idx - 1,
                                "document": doc["document"]})
                    assert code == 200, (cid, rev_idx, code, payload)
                code, payload, _ = http(
                    base + f"/v1/plates/{plate_id}/render?revision={rev_idx}&format=png",
                    "POST")
                assert code == 201, (cid, rev_idx, code, payload)
                artifact_id = payload["id"]
                raw = urllib.request.urlopen(
                    base + "/v1/artifacts/" + artifact_id, timeout=30).read()
                assert raw[:8] == b"\x89PNG\r\n\x1a\n"
                assert hashlib.sha256(raw).hexdigest() == payload["digest"]
                png_path = os.path.join(
                    RENDERS_DIR, f"{cid}__{bg_name}.png")
                with open(png_path, "wb") as fh:
                    fh.write(raw)
                manifest.append({
                    "family": fam,
                    "candidate": cid,
                    "plate": plate_id,
                    "revision": rev_idx,
                    "background": bg_name,
                    "background_srgb": bg_rgb,
                    "background_alpha": bg_a,
                    "artifact": artifact_id,
                    "artifact_sha256": payload["digest"],
                    "scene_digest": payload["scene_digest"],
                    "scene_source": os.path.relpath(scene_path, OUT),
                    "png": os.path.relpath(png_path, OUT),
                })
        with open(os.path.join(OUT, "render-manifest.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
        print(json.dumps({"renders": len(manifest),
                          "plates": len({m["plate"] for m in manifest})},
                         ensure_ascii=False))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise


if __name__ == "__main__":
    main()
