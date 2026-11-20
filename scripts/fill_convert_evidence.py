#!/usr/bin/env python3
"""Generate real image and geometry evidence for nonzero<->evenodd conversion.

The script boots the actual HTTP service against an ephemeral data root, builds
several documents covering nesting, opposite winding, tangency, self-intersection
and mirroring, calls the convert-fill endpoint, renders PNGs before and after the
candidate is committed, and writes a machine-readable evidence manifest.

Only the Python standard library is used. PNG IHDR dimensions and CRC-protected
chunks are parsed directly; a small inflate+filter decoder recovers pixels so the
image comparison needs no third-party imaging dependency.
"""
import argparse, copy, json, os, pathlib, signal, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request, zlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def rect_ops(x0, y0, x1, y1):
    p = lambda x, y: {"x": x, "y": y}
    return [{"kind": "M", "points": [p(x0, y0)]}, {"kind": "L", "points": [p(x1, y0)]},
            {"kind": "L", "points": [p(x1, y1)]}, {"kind": "L", "points": [p(x0, y1)]}, {"kind": "Z"}]


def solid(rule, ops, transform=(1, 0, 0, 1, 0, 0), color=(0.9, 0.35, 0.2, 1)):
    return {"id": "target", "fill_rule": rule, "path": ops, "transform": list(transform),
            "brush": {"kind": "solid", "color": dict(zip("rgba", color))}}


def document(shapes, size=96, clip=None):
    layer = {"id": "l1", "opacity": 1, "blend": "normal", "shapes": shapes}
    if clip is not None:
        layer["clip"] = clip
    return {"width": size, "height": size, "background": {"r": 0.05, "g": 0.06, "b": 0.1, "a": 1},
            "layers": [layer]}


def decode_png(raw):
    """Return (width, height, rgba byte rows) for an 8-bit RGBA or RGB PNG."""
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    pos, width, height, bit, color_type, idat = 8, 0, 0, 0, 0, []
    while pos < len(raw):
        length, kind = struct.unpack(">I4s", raw[pos:pos + 8])
        chunk = raw[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, bit, color_type = struct.unpack(">IIBB", chunk[:10])
            assert bit == 8
        elif kind == b"IDAT":
            idat.append(chunk)
        elif kind == b"IEND":
            break
    channels = {2: 3, 6: 4}[color_type]
    data = zlib.decompress(b"".join(idat))
    stride = width * channels
    out, prev, row, i = [], bytearray(stride), 0, 0
    while row < height:
        ftype = data[i]
        i += 1
        line = bytearray(data[i:i + stride])
        i += stride
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            if ftype == 1:
                line[x] = (line[x] + a) & 255
            elif ftype == 2:
                line[x] = (line[x] + b) & 255
            elif ftype == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif ftype == 4:
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
        out.append(bytes(line))
        prev = line
        row += 1
    if channels == 3:
        rgba = []
        for r in out:
            row = bytearray(width * 4)
            for i in range(width):
                row[4 * i:4 * i + 4] = bytes((r[3 * i], r[3 * i + 1], r[3 * i + 2], 255))
            rgba.append(bytes(row))
        out = rgba
    return width, height, b"".join(out)


def rect_ops_ccw(x0, y0, x1, y1):
    # Counter-clockwise (y-down) closed ring with correct verb order.
    p = lambda x, y: {"x": x, "y": y}
    return [{"kind": "M", "points": [p(x0, y0)]}, {"kind": "L", "points": [p(x0, y1)]},
            {"kind": "L", "points": [p(x1, y1)]}, {"kind": "L", "points": [p(x1, y0)]}, {"kind": "Z"}]


SCENARIOS = {
    "nested-hole": lambda: document([solid("evenodd", rect_ops(8, 8, 88, 88) + rect_ops(24, 24, 72, 72))]),
    "overlap": lambda: document([solid("nonzero", rect_ops(10, 20, 58, 76) + rect_ops(38, 20, 86, 76))]),
    "opposite-winding": lambda: document([solid("nonzero", rect_ops(8, 8, 88, 88) + rect_ops_ccw(24, 24, 72, 72))]),
    "tangent": lambda: document([solid("nonzero", rect_ops(8, 30, 48, 70) + rect_ops(48, 40, 88, 80))]),
    "bowtie": lambda: document([solid("evenodd", [
        {"kind": "M", "points": [{"x": 12, "y": 12}]}, {"kind": "L", "points": [{"x": 84, "y": 84}]},
        {"kind": "L", "points": [{"x": 84, "y": 12}]}, {"kind": "L", "points": [{"x": 12, "y": 84}]}, {"kind": "Z"}])]),
    "mirror": lambda: document([solid("evenodd", rect_ops(8, 8, 88, 88) + rect_ops(24, 24, 72, 72),
                                      transform=(-1, 0, 0, 1, 96, 0))]),
    "clipped": lambda: document([solid("nonzero", rect_ops(8, 8, 88, 88) + rect_ops(24, 24, 72, 72))],
                                clip=rect_ops(0, 0, 60, 96)),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "var" / "fill-convert-evidence"))
    args = ap.parse_args()
    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prism-evidence-") as tmp:
        base = pathlib.Path(tmp)
        binary = base / "prismplate"
        subprocess.run(["go", "build", "-o", str(binary), "./cmd/prismplate"], cwd=ROOT, check=True)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        url = f"http://127.0.0.1:{port}"
        log = open(base / "service.log", "w")
        proc = subprocess.Popen([str(binary), "-listen", f"127.0.0.1:{port}", "-data", str(base / "state")],
                                stdout=log, stderr=log)

        def call(method, path, data=None, status=200):
            body = None if data is None else json.dumps(data).encode()
            req = urllib.request.Request(url + path, data=body, method=method,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=35) as r:
                    payload = r.read()
                    code = r.status
            except urllib.error.HTTPError as e:
                payload, code = e.read(), e.code
            if code != status:
                raise RuntimeError(f"{method} {path}: want {status} got {code}: {payload[:200]!r}")
            return json.loads(payload) if payload else None

        try:
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError("service exited")
                try:
                    urllib.request.urlopen(url + "/healthz", timeout=1).close()
                    break
                except OSError:
                    time.sleep(0.05)
            manifest = []
            for name, build in SCENARIOS.items():
                plate = f"evidence-{name}"
                doc = build()
                src_rule = doc["layers"][0]["shapes"][0]["fill_rule"]
                target = "evenodd" if src_rule == "nonzero" else "nonzero"
                call("POST", "/v1/plates", {"id": plate, "title": f"填充规则证据 {name}", "document": doc}, 201)

                def render(rev):
                    art = call("POST", f"/v1/plates/{plate}/render?revision={rev}&format=png", status=201)
                    raw = urllib.request.urlopen(url + "/v1/artifacts/" + art["id"], timeout=35).read()
                    return art, raw

                art_before, png_before = render(1)
                conv = call("POST", f"/v1/plates/{plate}/convert-fill?revision=1",
                            {"layer_id": "l1", "shape_id": "target", "target_rule": target}, 200)
                report = conv["report"]
                # Original stays immutable: still one revision, original rule intact.
                plate_state = call("GET", f"/v1/plates/{plate}")
                assert len(plate_state["revisions"]) == 1
                assert plate_state["revisions"][0]["document"]["layers"][0]["shapes"][0]["fill_rule"] == src_rule
                # Commit the candidate through the ordinary revision flow.
                call("PUT", f"/v1/plates/{plate}",
                     {"expected_revision": 1, "document": conv["candidate_document"]}, 200)
                art_after, png_after = render(2)

                w1, h1, px1 = decode_png(png_before)
                w2, h2, px2 = decode_png(png_after)
                assert (w1, h1) == (w2, h2)
                diff = sum(1 for a, b in zip(px1, px2) if a != b)
                (outdir / f"{name}-rev1.png").write_bytes(png_before)
                (outdir / f"{name}-rev2-{target}.png").write_bytes(png_after)
                entry = {
                    "scenario": name, "plate": plate, "source_rule": src_rule, "target_rule": target,
                    "image": {"width": w1, "height": h1, "pixel_channels": len(px1),
                              "differing_channels": diff, "identical_png": diff == 0,
                              "artifact_before": art_before["id"], "artifact_after": art_after["id"]},
                    "geometry": {k: report[k] for k in (
                        "reliability", "source_contours", "source_vertices", "output_contours",
                        "output_vertices", "operations", "source_signed_fill_area",
                        "candidate_signed_fill_area", "area_absolute_delta", "area_tolerance",
                        "grid_points_checked", "grid_mismatches", "grid_on_edge_skipped",
                        "min_channel_device_units", "coincident_contacts", "candidate_compiles",
                        "approximation_required", "node_budget_blocked")},
                    "warnings": report["warnings"],
                    "source_revision_preserved": True,
                }
                manifest.append(entry)
                print(json.dumps(entry, ensure_ascii=False))
            (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
            # Hard evidence gate: every clean scenario must produce identical PNGs.
            failures = [e["scenario"] for e in manifest if not e["image"]["identical_png"]
                        or e["geometry"]["grid_mismatches"] != 0]
            if failures:
                raise RuntimeError(f"region equivalence failures: {failures}")
            print(json.dumps({"result": "passed", "scenarios": len(manifest), "out": str(outdir)},
                             ensure_ascii=False))
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=45)
            log.close()


if __name__ == "__main__":
    main()
