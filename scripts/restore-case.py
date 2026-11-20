#!/usr/bin/env python3
"""Render the selective-restore boundary with an overlap and a hole, as PNGs.

Run inside the repository after a successful build toolchain setup:
    python3 scripts/restore-case.py --out /tmp/restore-case
It starts a throwaway service on a loopback port, uses only the public HTTP
contract, and leaves its PNGs in the given directory. It does not modify the
repository or the example input.
"""
import argparse, copy, json, pathlib, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request, zlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def png_pixels(raw):
    """Decode a non-interlaced 8-bit RGB/RGBA PNG into rows of (r,g,b,a).

    Go's encoder omits a fully opaque alpha channel, so both colour types are
    accepted; this is a test helper, not a general PNG implementation.
    """
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, width, height, channels = 8, b"", 0, 0, 0
    while pos < len(raw):
        length = struct.unpack(">I", raw[pos:pos + 4])[0]
        kind = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", body[:10])
            assert depth == 8 and color in (2, 6), "expected 8-bit RGB or RGBA"
            channels = 3 if color == 2 else 4
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    data = zlib.decompress(idat)
    stride = width * channels
    rows, prior, at = [], bytearray(stride), 0
    for _ in range(height):
        filt = data[at]
        at += 1
        line = bytearray(data[at:at + stride])
        at += stride
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = prior[x]
            c = prior[x - channels] if x >= channels else 0
            if filt == 1:
                line[x] = (line[x] + a) & 255
            elif filt == 2:
                line[x] = (line[x] + b) & 255
            elif filt == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        px = [tuple(line[i:i + channels]) + ((255,) if channels == 3 else ()) for i in range(0, stride, channels)]
        rows.append(px)
        prior = line
    return width, height, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--binary", default=None)
    args = parser.parse_args()
    out = pathlib.Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    seed = json.loads((ROOT / "examples/amber-proof.json").read_text())
    with tempfile.TemporaryDirectory(prefix="prism-restore-case-") as tmp:
        base = pathlib.Path(tmp)
        binary = pathlib.Path(args.binary) if args.binary else base / "prismplate"
        if not args.binary:
            subprocess.run(["go", "build", "-o", str(binary), "./cmd/prismplate"], cwd=ROOT, check=True)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        url = f"http://127.0.0.1:{port}"
        log = (base / "service.log").open("w")
        proc = subprocess.Popen([str(binary), "-listen", f"127.0.0.1:{port}", "-data", str(base / "state")], stdout=log, stderr=log)

        def call(method, path, data=None, status=200):
            body = json.dumps(data).encode() if data is not None else None
            req = urllib.request.Request(url + path, data=body, method=method, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=35) as r:
                    code, payload = r.status, r.read()
            except urllib.error.HTTPError as e:
                code, payload = e.code, e.read()
            if code != status:
                raise SystemExit(f"{method} {path}: expected {status}, observed {code}: {payload[:300]!r}")
            return json.loads(payload) if payload and payload[:1] in b"{}[" else payload

        try:
            for _ in range(200):
                try:
                    urllib.request.urlopen(url + "/healthz", timeout=1).close()
                    break
                except OSError:
                    time.sleep(0.05)
            call("POST", "/v1/plates", seed, status=201)
            old = seed["document"]
            draft = copy.deepcopy(old)
            draft["background"] = {"r": 0.2, "g": 0.2, "b": 0.2, "a": 1}
            draft["layers"][0]["clip"] = []
            draft["layers"][1].update(opacity=0.25, blend="normal", mask=[])
            overlap = copy.deepcopy(old["layers"][1]["shapes"][0])
            overlap["id"], overlap["path"] = "cyan-overlap", [
                {"kind": "M", "points": [{"x": 90, "y": 70}]},
                {"kind": "L", "points": [{"x": 170, "y": 70}]},
                {"kind": "L", "points": [{"x": 170, "y": 120}]},
                {"kind": "L", "points": [{"x": 90, "y": 120}]},
                {"kind": "Z"},
            ]
            draft["layers"][1]["shapes"] = [overlap]
            call("PUT", "/v1/plates/amber-plate", {"expected_revision": 1, "document": draft})
            request = {"base_revision": 2, "source_revision": 1, "context": "destination",
                       "items": [{"shape": "cyan-window", "layer": "cyan-glaze"}]}
            plan = call("POST", "/v1/plates/amber-plate/restore/preview", request)
            (out / "review.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2))
            for name, revision in (("before.png", 2), ("restored.png", 3)):
                if revision == 3:
                    call("POST", "/v1/plates/amber-plate/restore", {"expected_revision": 2, "plan_digest": plan["plan_digest"], "request": request}, status=201)
                artifact = call("POST", f"/v1/plates/amber-plate/render?revision={revision}&format=png", status=201)
                raw = call("GET", "/v1/artifacts/" + artifact["id"])
                (out / name).write_bytes(raw)
            w, h, after = png_pixels((out / "restored.png").read_bytes())
            before = png_pixels((out / "before.png").read_bytes())[2]
            # The restored object's transformed bounds: local 42..144 x 48..124
            # through matrix [1,0.12,-0.08,1,8,-4], conservatively rounded out.
            x0, y0, x1, y1 = 39, 48, 150, 139
            changed = [(x, y) for y in range(h) for x in range(w) if before[y][x] != after[y][x]]
            outside = [(x, y) for x, y in changed if not (x0 <= x <= x1 and y0 <= y <= y1)]
            # Hole (local 64..124 x 64..104, transformed) keeps the destination
            # composition: the restored ring is open there, so the pixel equals
            # the pre-restore draft. The surrounding ring stroke must differ.
            hole = (95, 91)
            overlap = (135, 90)
            checks = {
                "size": [w, h],
                "changed_pixels": len(changed),
                "changed_outside_restored_bounds": len(outside),
                "hole_pixel_unchanged": before[hole[1]][hole[0]] == after[hole[1]][hole[0]],
                "hole_rgba": list(after[hole[1]][hole[0]]),
                "overlap_pixel_changed": before[overlap[1]][overlap[0]] != after[overlap[1]][overlap[0]],
                "overlap_before_rgba": list(before[overlap[1]][overlap[0]]),
                "overlap_after_rgba": list(after[overlap[1]][overlap[0]]),
                "hole_not_source_blue": after[hole[1]][hole[0]][2] < 200,
            }
            assert checks["changed_pixels"] > 0 and checks["changed_outside_restored_bounds"] == 0
            assert checks["hole_pixel_unchanged"] and checks["overlap_pixel_changed"]
            (out / "pixels.json").write_text(json.dumps(checks, indent=2))
            print(json.dumps({"case": "selective-restore", "out": str(out), "checks": checks}, ensure_ascii=False))
        finally:
            proc.terminate()
            proc.wait(timeout=45)
            log.close()


if __name__ == "__main__":
    main()
