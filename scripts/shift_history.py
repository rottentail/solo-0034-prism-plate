#!/usr/bin/env python3
"""Replayable shift-history generator and 256 MiB acceptance verifier for prism-plate.

Subcommands:
  generate  Replay a deterministic shift (create/revise/render/proof) into --home,
            growing the snapshot toward --target-mib with real 512x512 PNG/SVG artifacts.
  verify    Restart the service on the generated snapshot and run the acceptance
            checklist: old proofs downloadable, new orders processed, two-operator
            heavy concurrency queued or explicitly rejected, no phantom artifacts.
  compare   Prove two generated histories (e.g. unlimited vs memory-capped) produced
            byte-identical artifact digests for every accepted order.

The service is deterministic: identical documents yield identical artifact digests,
so `generate` with the same --seed replays the same shift history. Memory pressure is
applied to the server child via --gomemlimit (GOMEMLIMIT, MiB); the kernel-enforced
container limit is applied by the deployment (see docs/deployment-256m.md). Peak RSS
(VmHWM) of the server is recorded per phase as the fit evidence.
"""
import argparse, concurrent.futures, copy, hashlib, json, os, pathlib, random, signal, socket, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST_SCHEMA = "prism-plate-shift-v1"
REPORT_SCHEMA = "prism-plate-shift-verify-v1"
MIB = 1 << 20

# ---------------------------------------------------------------- scene builders
def quad(x0, y0, x1, y1):
    return [{"kind": "M", "points": [{"x": round(x0, 3), "y": round(y0, 3)}]},
            {"kind": "L", "points": [{"x": round(x1, 3), "y": round(y0, 3)}]},
            {"kind": "L", "points": [{"x": round(x1, 3), "y": round(y1, 3)}]},
            {"kind": "L", "points": [{"x": round(x0, 3), "y": round(y1, 3)}]},
            {"kind": "Z"}]

def band_stops(rng, n=16):
    out, base = [], [rng.random() for _ in range(3)]
    for i in range(n):
        f = 0.15 + 0.85 * (i % 2)
        out.append({"at": round(i / (n - 1), 4),
                    "color": {"r": round(min(1, base[0] * f + 0.08 * i / n), 4),
                              "g": round(min(1, base[1] * f + 0.05 * (1 - i / n)), 4),
                              "b": round(min(1, base[2] * (1.1 - f) + 0.1 * i / n), 4),
                              "a": round(0.5 + 0.5 * ((i * 7) % 10) / 10, 4)}})
    return out

def gradient(rng, nstop=16):
    fx, fy = rng.uniform(0, 512), rng.uniform(0, 512)
    tx, ty = fx + rng.uniform(-300, 300), fy + rng.uniform(-300, 300)
    if abs(tx - fx) + abs(ty - fy) < 1:
        tx = fx + 50
    return {"kind": "linear", "from": {"x": round(fx, 2), "y": round(fy, 2)},
            "to": {"x": round(tx, 2), "y": round(ty, 2)},
            "color": {"r": 0, "g": 0, "b": 0, "a": 0}, "stops": band_stops(rng, nstop)}

def mosaic_doc(rng):
    """普通单张校样: 3 layers x 12 gradient quads + clip = 148 segments."""
    layers = []
    for li in range(3):
        shapes = []
        for k in range(12):
            x0, y0 = rng.uniform(-40, 470), rng.uniform(-40, 470)
            shapes.append({"id": f"m{li}-{k}", "fill_rule": "nonzero",
                           "path": quad(x0, y0, x0 + rng.uniform(40, 200), y0 + rng.uniform(40, 200)),
                           "transform": [1, 0, 0, 1, 0, 0], "brush": gradient(rng)})
        layer = {"id": f"mosaic-{li}", "opacity": round(rng.uniform(0.55, 1), 3),
                 "blend": ["normal", "multiply", "screen"][li % 3], "shapes": shapes}
        if li == 0:
            layer["clip"] = quad(6, 6, 506, 506)
        layers.append(layer)
    return {"width": 512, "height": 512,
            "background": {"r": 0.07, "g": 0.08, "b": 0.11, "a": 1}, "layers": layers}

def lattice_doc(rng, n=6):
    """细密镂空稿: gradient underlay (4) + evenodd 6x6 cutout mesh (148) = 152 segments,
    the maximum admissible at 512x512 under the 1.6e8 edge-visit budget."""
    margin, gap = 24.0, 12.0
    ops = quad(margin, margin, 512 - margin, 512 - margin)
    inner = 512 - 2 * margin
    cell = (inner - (n - 1) * gap) / n
    for i in range(n):
        for j in range(n):
            x0, y0 = margin + j * (cell + gap), margin + i * (cell + gap)
            ops += quad(x0, y0, x0 + cell, y0 + cell)
    return {"width": 512, "height": 512,
            "background": {"r": 0.05, "g": 0.06, "b": 0.09, "a": 1},
            "layers": [
                {"id": "under", "opacity": 1, "blend": "normal", "shapes": [
                    {"id": "glow", "fill_rule": "nonzero", "path": quad(0, 0, 512, 512),
                     "transform": [1, 0, 0, 1, 0, 0], "brush": gradient(rng)}]},
                {"id": "cut", "opacity": round(rng.uniform(0.85, 1), 3), "blend": "normal", "shapes": [
                    {"id": "mesh", "fill_rule": "evenodd", "path": ops,
                     "transform": [1, 0, 0, 1, 0, 0],
                     "brush": {"kind": "solid", "color": {"r": 0.06, "g": 0.07, "b": 0.1, "a": 0.97}}}]}]}

def veil_doc(rng):
    """多层透明稿: 16 layers, 38 gradient quads (6 layers x3 + 10 layers x2) = 152 segments."""
    layers = []
    for i in range(16):
        shapes = []
        for k in range(3 if i < 6 else 2):
            x0, y0 = rng.uniform(-30, 420), rng.uniform(-30, 420)
            shapes.append({"id": f"v{i}-{k}", "fill_rule": "nonzero",
                           "path": quad(x0, y0, x0 + rng.uniform(90, 240), y0 + rng.uniform(90, 240)),
                           "transform": [1, 0, 0, 1, 0, 0], "brush": gradient(rng)})
        layers.append({"id": f"veil-{i}", "opacity": round(rng.uniform(0.3, 0.95), 3),
                       "blend": ["normal", "multiply", "screen"][i % 3], "shapes": shapes})
    return {"width": 512, "height": 512,
            "background": {"r": 0.08, "g": 0.07, "b": 0.1, "a": 1}, "layers": layers}

def mutate(doc, rng):
    """Derive the next revision: recolor/reattenuate, keep structure and ids valid."""
    d = copy.deepcopy(doc)
    d["background"]["r"] = round(d["background"]["r"] * 0.95, 4)
    d["background"]["g"] = round(min(1, d["background"]["g"] * 1.05 + 0.01), 4)
    for layer in d["layers"]:
        layer["opacity"] = round(min(1, layer["opacity"] * 0.92 + 0.04), 3)
        for shape in layer["shapes"]:
            brush = shape["brush"]
            if brush["kind"] == "solid":
                c = brush["color"]
                c["r"], c["g"], c["b"] = round(c["r"] * 0.9, 4), round(c["g"] * 0.9, 4), round(c["b"] * 0.9, 4)
            else:
                for stop in brush["stops"]:
                    c = stop["color"]
                    c["r"], c["g"], c["b"] = c["g"], c["b"], c["r"]
    return d

def oversize_lattice_doc():
    """7x7 cutout mesh = 200 segments -> over the 1.6e8 budget, must be rejected 400."""
    return lattice_doc(random.Random(0), n=7)

def oversize_canvas_doc():
    d = mosaic_doc(random.Random(0))
    d["width"] = 513
    return d

ARCHETYPES = {"mosaic": mosaic_doc, "lattice": lattice_doc, "veil": veil_doc}
# Filler rotation weights veil (largest PNG) so the 32 MiB target is reached well
# below the 256-artifact snapshot capacity.
FILLER_ROTATION = ["veil", "mosaic", "veil", "veil"]

# ---------------------------------------------------------------- http client
class Http:
    def __init__(self, base, timeout=40):
        self.base, self.timeout = base, timeout

    def __call__(self, method, path, data=None, raw=False, headers=None):
        body = data if isinstance(data, bytes) else (json.dumps(data).encode() if data is not None else None)
        hs = dict(headers or {})
        if body is not None and not isinstance(data, bytes):
            hs["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=hs)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, (r.read() if raw else json.loads(r.read() or b"null")), {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                parsed = json.loads(payload or b"null")
            except ValueError:
                parsed = payload
            return e.code, (payload if raw else parsed), {k.lower(): v for k, v in e.headers.items()}

# ---------------------------------------------------------------- server control
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port

def vmhwm_kib(pid):
    try:
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmHWM"):
                return int(line.split()[1])
    except OSError:
        pass
    return None

class Server:
    def __init__(self, binary, state_dir, gomemlimit_mib, logpath):
        self.binary, self.state_dir, self.gomemlimit_mib, self.logpath = binary, state_dir, gomemlimit_mib, logpath
        self.proc, self.url, self.peaks = None, None, {}

    def start(self, phase):
        env = dict(os.environ)
        if self.gomemlimit_mib:
            env["GOMEMLIMIT"] = f"{self.gomemlimit_mib}MiB"
        log = open(self.logpath, "ab")
        self.proc = subprocess.Popen([str(self.binary), "-listen", f"127.0.0.1:{self.port}",
                                      "-data", str(self.state_dir)], stdout=log, stderr=log, env=env)
        for _ in range(200):
            if self.proc.poll() is not None:
                raise RuntimeError(f"service exited during start; see {self.logpath}")
            try:
                urllib.request.urlopen(self.url + "/healthz", timeout=1).close()
                self.peaks[phase] = vmhwm_kib(self.proc.pid)
                return
            except OSError:
                time.sleep(.05)
        raise RuntimeError("service startup timed out")

    @property
    def port(self):
        if not hasattr(self, "_port"):
            self._port = free_port()
        return self._port

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def note_peak(self, phase):
        if self.proc:
            self.peaks[phase] = vmhwm_kib(self.proc.pid)

    def stop(self, phase):
        if self.proc and self.proc.poll() is None:
            self.note_peak(phase)
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=45)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
                raise RuntimeError("service ignored SIGTERM")
        self.proc = None

def build_binary(workdir):
    binary = pathlib.Path(workdir) / "prismplate"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/prismplate"], cwd=ROOT, check=True)
    return binary

# ---------------------------------------------------------------- order helpers
def expect(status, want, what):
    if status != want:
        raise RuntimeError(f"{what}: expected HTTP {want}, got {status}")

def place_order(http, plate_id, title, doc1, doc2, proof_state, proof_note, async_formats=()):
    """One normal order: create (rev1), revise (rev2), render PNG+SVG of both
    revisions, then open and decide one proof. Returns the manifest order entry."""
    order = {"plate": plate_id, "revisions": [], "artifacts": [], "proofs": []}
    st, created, _ = http("POST", "/v1/plates", {"id": plate_id, "title": title, "document": doc1})
    expect(st, 201, f"create {plate_id}")
    order["revisions"].append({"number": 1, "digest": created["revisions"][0]["digest"]})
    st, revised, _ = http("PUT", f"/v1/plates/{plate_id}", {"expected_revision": 1, "document": doc2})
    expect(st, 200, f"revise {plate_id}")
    order["revisions"].append({"number": 2, "digest": revised["revisions"][1]["digest"]})

    def render(revision, fmt):
        via = "exposure" if (revision, fmt) in async_formats else "sync"
        if via == "exposure":
            st, job, _ = http("POST", f"/v1/plates/{plate_id}/exposures?revision={revision}&format={fmt}")
            expect(st, 202, f"exposure {plate_id} r{revision} {fmt}")
            for _ in range(400):
                st, job, _ = http("GET", "/v1/exposures/" + job["id"])
                if job["state"] in ("succeeded", "failed", "cancelled"):
                    break
                time.sleep(.05)
            if job["state"] != "succeeded":
                raise RuntimeError(f"exposure {job['id']} ended {job['state']}: {job.get('error')}")
            # fetch metadata through a fresh sync render of the same key (idempotent)
            st, meta, _ = http("POST", f"/v1/plates/{plate_id}/render?revision={revision}&format={fmt}")
            expect(st, 201, f"render {plate_id} r{revision} {fmt}")
            if meta["id"] != job["artifact"]:
                raise RuntimeError("exposure artifact id mismatch with sync render")
        else:
            st, meta, _ = http("POST", f"/v1/plates/{plate_id}/render?revision={revision}&format={fmt}")
            expect(st, 201, f"render {plate_id} r{revision} {fmt}")
        st, raw, hs = http("GET", "/v1/artifacts/" + meta["id"], raw=True)
        expect(st, 200, f"download {meta['id']}")
        if hashlib.sha256(raw).hexdigest() != meta["digest"]:
            raise RuntimeError(f"artifact {meta['id']} digest mismatch at download")
        return {"id": meta["id"], "plate": plate_id, "revision": revision, "format": fmt,
                "digest": meta["digest"], "bytes": len(raw), "scene_digest": meta["scene_digest"], "via": via}

    for revision, fmt in [(1, "png"), (1, "svg"), (2, "png"), (2, "svg")]:
        order["artifacts"].append(render(revision, fmt))
    proof_art = next(a for a in order["artifacts"] if a["revision"] == 2 and a["format"] == "png")
    proof_id = f"proof-{plate_id}"
    st, proof, _ = http("POST", "/v1/proofs", {"id": proof_id, "artifact": proof_art["id"]})
    expect(st, 201, f"open proof {proof_id}")
    st, proof, _ = http("PUT", f"/v1/proofs/{proof_id}", {"state": proof_state, "note": proof_note})
    expect(st, 200, f"decide proof {proof_id}")
    order["proofs"].append({"id": proof_id, "artifact": proof_art["id"], "state": proof_state, "note": proof_note})
    return order

def place_order_parallel(http, plate_id, title, doc1, doc2, proof_state, proof_note, workers):
    """Filler order: same HTTP workflow, renders fanned out over a small pool."""
    order = {"plate": plate_id, "revisions": [], "artifacts": [], "proofs": []}
    st, created, _ = http("POST", "/v1/plates", {"id": plate_id, "title": title, "document": doc1})
    expect(st, 201, f"create {plate_id}")
    order["revisions"].append({"number": 1, "digest": created["revisions"][0]["digest"]})
    st, revised, _ = http("PUT", f"/v1/plates/{plate_id}", {"expected_revision": 1, "document": doc2})
    expect(st, 200, f"revise {plate_id}")
    order["revisions"].append({"number": 2, "digest": revised["revisions"][1]["digest"]})

    def render(revision, fmt):
        st, meta, _ = http("POST", f"/v1/plates/{plate_id}/render?revision={revision}&format={fmt}")
        if st != 201:
            return {"error_status": st, "revision": revision, "format": fmt}
        st, raw, _ = http("GET", "/v1/artifacts/" + meta["id"], raw=True)
        expect(st, 200, "download")
        return {"id": meta["id"], "plate": plate_id, "revision": revision, "format": fmt,
                "digest": meta["digest"], "bytes": len(raw), "scene_digest": meta["scene_digest"], "via": "sync"}

    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(lambda rf: render(*rf), [(1, "png"), (1, "svg"), (2, "png"), (2, "svg")]))
    for r in results:
        if "error_status" in r:
            return order, r  # capacity stop signal
        order["artifacts"].append(r)
    proof_art = next(a for a in order["artifacts"] if a["revision"] == 2 and a["format"] == "png")
    proof_id = f"proof-{plate_id}"
    st, _, _ = http("POST", "/v1/proofs", {"id": proof_id, "artifact": proof_art["id"]})
    expect(st, 201, f"open proof {proof_id}")
    st, _, _ = http("PUT", f"/v1/proofs/{proof_id}", {"state": proof_state, "note": proof_note})
    expect(st, 200, f"decide proof {proof_id}")
    order["proofs"].append({"id": proof_id, "artifact": proof_art["id"], "state": proof_state, "note": proof_note})
    return order, None

# ---------------------------------------------------------------- generate
def cmd_generate(args):
    home = pathlib.Path(args.home)
    state_dir = home / "state"
    if state_dir.exists() and any(state_dir.iterdir()):
        if not args.force:
            raise SystemExit(f"{state_dir} is not empty; pass --force to rebuild the shift history")
        import shutil
        shutil.rmtree(state_dir)
    home.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    target = int(args.target_mib * MIB)
    with tempfile.TemporaryDirectory(prefix="prism-plate-build-") as tmp:
        binary = build_binary(tmp)
        server = Server(binary, state_dir, args.gomemlimit, home / "service.log")
        server.url = server.base
        manifest = {"schema": MANIFEST_SCHEMA, "seed": args.seed,
                    "config": {"target_mib": args.target_mib, "gomemlimit_mib": args.gomemlimit,
                               "render_parallel": args.render_parallel},
                    "orders": [], "admission_rejections": [], "phases": []}
        t0 = time.time()
        server.start("startup")
        http = Http(server.base)
        try:
            # --- canonical acceptance orders: one per archetype, both revisions, PNG+SVG
            canonical = [("acc-poster", "验收-普通单张", "mosaic", "accepted", "普通单张签样通过", ()),
                         ("acc-lattice", "验收-细密镂空", "lattice", "accepted", "镂空签样通过", ((2, "png"),)),
                         ("acc-veil", "验收-多层透明", "veil", "accepted", "多层透明签样通过", ((1, "png"),))]
            for plate_id, title, kind, pstate, pnote, async_fmts in canonical:
                doc1 = ARCHETYPES[kind](rng)
                doc2 = mutate(doc1, rng)
                order = place_order(http, plate_id, title, doc1, doc2, pstate, pnote, async_fmts)
                order["kind"] = kind
                manifest["orders"].append(order)
            # one rejected proof shows the full sign-off lifecycle in the history
            lattice = next(o for o in manifest["orders"] if o["plate"] == "acc-lattice")
            svg_art = next(a for a in lattice["artifacts"] if a["revision"] == 2 and a["format"] == "svg")
            st, _, _ = http("POST", "/v1/proofs", {"id": "proof-acc-lattice-svg", "artifact": svg_art["id"]})
            expect(st, 201, "open rejected-proof")
            st, _, _ = http("PUT", "/v1/proofs/proof-acc-lattice-svg",
                            {"state": "rejected", "note": "矢量稿渐变过渡与栅格不一致,退回制稿"})
            expect(st, 200, "decide rejected-proof")
            lattice["proofs"].append({"id": "proof-acc-lattice-svg", "artifact": svg_art["id"],
                                      "state": "rejected", "note": "矢量稿渐变过渡与栅格不一致,退回制稿"})
            # --- admission rejections: over-budget scenes leave no history
            for probe_id, doc, why in [("reject-lattice-7x7", oversize_lattice_doc(), "scene exceeds bounded rendering work"),
                                       ("reject-canvas-513", oversize_canvas_doc(), "invalid canvas")]:
                st, body, _ = http("POST", "/v1/plates", {"id": probe_id, "title": "超预算稿", "document": doc})
                expect(st, 400, f"admission rejection {probe_id}")
                st, _, _ = http("GET", f"/v1/plates/{probe_id}")
                expect(st, 404, f"rejected plate {probe_id} must not exist")
                manifest["admission_rejections"].append({"plate": probe_id, "status": 400,
                                                         "error": body.get("error", ""), "reason": why})
            server.note_peak("canonical")
            manifest["phases"].append({"name": "canonical", "seconds": round(time.time() - t0, 2),
                                       "vmhwm_kib": server.peaks.get("canonical")})
            # --- filler orders until the snapshot approaches the target size
            t_fill = time.time()
            round_no, stop_reason = 0, "target-size-reached"
            while True:
                round_no += 1
                kind = FILLER_ROTATION[(round_no - 1) % len(FILLER_ROTATION)]
                plate_id = f"order-r{round_no:03d}"
                doc1 = ARCHETYPES[kind](rng)
                doc2 = mutate(doc1, rng)
                proof_state = "accepted" if round_no % 2 == 1 else "rejected"
                proof_note = "班次抽检通过" if proof_state == "accepted" else "色差超差,退回"
                order, stop = place_order_parallel(http, plate_id, f"班次工单-{round_no:03d}", doc1, doc2,
                                                   proof_state, proof_note, args.render_parallel)
                order["kind"] = kind
                manifest["orders"].append(order)
                if stop is not None:
                    stop_reason = f"capacity-stop HTTP {stop['error_status']} at round {round_no}"
                    break
                size = (state_dir / "state.json").stat().st_size
                artifacts = sum(len(o["artifacts"]) for o in manifest["orders"])
                if size >= target:
                    break
                if artifacts >= 240 or len(manifest["orders"]) >= 120:
                    stop_reason = f"snapshot-object-cap-guard artifacts={artifacts}"
                    break
            server.note_peak("filler")
            manifest["phases"].append({"name": "filler", "seconds": round(time.time() - t_fill, 2),
                                       "vmhwm_kib": server.peaks.get("filler"), "rounds": round_no,
                                       "stop_reason": stop_reason})
            # --- snapshot summary over the on-disk acceptance material
            raw = (state_dir / "state.json").read_bytes()
            snap = json.loads(raw)
            manifest["snapshot"] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                                    "plates": len(snap["plates"]),
                                    "revisions": sum(len(p["revisions"]) for p in snap["plates"].values()),
                                    "artifacts": len(snap["artifacts"]),
                                    "proofs": len(snap["proofs"]),
                                    "proofs_accepted": sum(1 for p in snap["proofs"].values() if p["state"] == "accepted")}
            server.stop("pre-shutdown")
            manifest["phases"].append({"name": "shutdown", "vmhwm_kib": server.peaks.get("pre-shutdown")})
        finally:
            if server.proc:
                server.stop("final")
    manifest["seconds"] = round(time.time() - t0, 2)
    manifest["server"] = {"gomemlimit_mib": args.gomemlimit,
                          "vmhwm_kib": max((v for v in server.peaks.values() if v), default=None),
                          "peaks": server.peaks}
    (home / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(json.dumps({"generate": "passed", "home": str(home), "snapshot": manifest["snapshot"],
                      "vmhwm_kib": manifest["server"]["vmhwm_kib"], "seconds": manifest["seconds"]},
                     ensure_ascii=False))

# ---------------------------------------------------------------- verify
class Checks:
    def __init__(self):
        self.items = []

    def run(self, name, fn):
        try:
            detail = fn()
            self.items.append({"name": name, "ok": True, "detail": detail})
            print(f"  ok   {name}: {detail}", flush=True)
        except Exception as e:
            self.items.append({"name": name, "ok": False, "detail": str(e)})
            print(f"  FAIL {name}: {e}", flush=True)

    @property
    def ok(self):
        return all(i["ok"] for i in self.items)

def cmd_verify(args):
    home = pathlib.Path(args.home)
    state_dir = home / "state"
    manifest = json.loads((home / "manifest.json").read_text())
    checks = Checks()
    peaks = {}
    t0 = time.time()
    state_file = state_dir / "state.json"

    def check_snapshot_untouched():
        raw = state_file.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != manifest["snapshot"]["sha256"]:
            raise RuntimeError(f"snapshot changed since generation: {digest[:12]} != {manifest['snapshot']['sha256'][:12]}")
        return f"sha256 {digest[:16]}… == manifest, {len(raw)} bytes"
    checks.run("snapshot-untouched", check_snapshot_untouched)

    with tempfile.TemporaryDirectory(prefix="prism-plate-build-") as tmp:
        binary = build_binary(tmp)
        server = Server(binary, state_dir, args.gomemlimit, home / "service-verify.log")
        server.url = server.base
        server.start("restart")
        peaks.update(server.peaks)
        http = Http(server.base)
        try:
            def restart_ready():
                st, body, _ = http("GET", "/healthz")
                expect(st, 200, "healthz")
                rev = sum(len(o["revisions"]) for o in manifest["orders"])
                return f"ready after restart on {manifest['snapshot']['bytes']} byte snapshot, {rev} revisions recompiled"
            checks.run("restart-ready", restart_ready)

            def old_proofs_downloadable():
                accepted = [(p, o) for o in manifest["orders"] for p in o["proofs"] if p["state"] == "accepted"]
                for proof, order in accepted:
                    art = next(a for a in order["artifacts"] if a["id"] == proof["artifact"])
                    st, raw, hs = http("GET", "/v1/artifacts/" + art["id"], raw=True)
                    expect(st, 200, f"old proof {proof['id']}")
                    if hashlib.sha256(raw).hexdigest() != art["digest"]:
                        raise RuntimeError(f"old proof {proof['id']} digest mismatch after restart")
                    if hs.get("etag") != f'"{art["digest"]}"':
                        raise RuntimeError(f"old proof {proof['id']} ETag mismatch")
                    st, _, _ = http("GET", "/v1/artifacts/" + art["id"], raw=True,
                                    headers={"If-None-Match": f'"{art["digest"]}"'})
                    expect(st, 304, f"old proof {proof['id']} cache revalidation")
                server.note_peak("old-proofs")
                peaks["old-proofs"] = server.peaks["old-proofs"]
                return f"{len(accepted)} accepted proofs downloaded, sha256+ETag+304 all match"
            checks.run("old-proofs-downloadable", old_proofs_downloadable)

            def new_order_flow():
                rng = random.Random(manifest["seed"] + 1)
                doc1 = mosaic_doc(rng)
                doc2 = mutate(doc1, rng)
                order = place_order(http, "post-restart-order", "交接班后首单", doc1, doc2,
                                    "accepted", "交接班后首张普通单张校样通过")
                # continuous normal single proofs: three consecutive renders, stable digests
                for i in range(3):
                    st, meta, _ = http("POST", "/v1/plates/post-restart-order/render?revision=2&format=png")
                    expect(st, 201, f"consecutive render {i}")
                    if meta["digest"] != order["artifacts"][2]["digest"]:
                        raise RuntimeError("re-render digest drifted")
                server.note_peak("new-order")
                peaks["new-order"] = server.peaks["new-order"]
                new_order_flow.order = order
                return "create→revise→PNG+SVG→proof→accept→download after restart; 3 consecutive renders identical"
            checks.run("new-order-flow", new_order_flow)

            def deterministic_replay():
                # sample: every canonical artifact plus every 8th filler order
                n = 0
                for idx, order in enumerate(manifest["orders"]):
                    if not order["plate"].startswith("acc-") and idx % 8 != 0:
                        continue
                    for art in order["artifacts"]:
                        st, meta, _ = http("POST", f"/v1/plates/{order['plate']}/render"
                                                   f"?revision={art['revision']}&format={art['format']}")
                        expect(st, 201, "replay render")
                        if meta["id"] != art["id"] or meta["digest"] != art["digest"]:
                            raise RuntimeError(f"replay mismatch on {order['plate']} r{art['revision']} {art['format']}")
                        n += 1
                server.note_peak("replay")
                peaks["replay"] = server.peaks["replay"]
                return f"{n} persisted artifacts re-rendered with identical id+digest"
            checks.run("deterministic-replay", deterministic_replay)

            def two_operator_storm():
                lattice = next(o for o in manifest["orders"] if o["plate"] == "acc-lattice")
                veil = next(o for o in manifest["orders"] if o["plate"] == "acc-veil")
                want = {(a["plate"], a["revision"], a["format"]): a
                        for a in lattice["artifacts"] + veil["artifacts"]}
                barrier = threading.Barrier(3)
                outcomes, lock = [], threading.Lock()

                def operator(jobs):
                    barrier.wait()
                    for kind, plate, revision, fmt in jobs:
                        if kind == "sync":
                            st, meta, _ = http("POST", f"/v1/plates/{plate}/render?revision={revision}&format={fmt}")
                            with lock:
                                outcomes.append({"via": "sync", "status": st, "plate": plate,
                                                 "revision": revision, "format": fmt,
                                                 "meta": meta if st == 201 else None})
                        else:
                            st, job, _ = http("POST", f"/v1/plates/{plate}/exposures?revision={revision}&format={fmt}")
                            with lock:
                                outcomes.append({"via": "exposure", "status": st, "plate": plate,
                                                 "revision": revision, "format": fmt,
                                                 "job": job if st == 202 else None})

                jobs_a = ([("sync", "acc-lattice", 2, "png"), ("sync", "acc-lattice", 2, "svg")] +
                          [("exposure", "acc-lattice", 1, "png")] * 6)
                jobs_b = ([("sync", "acc-veil", 2, "png"), ("sync", "acc-veil", 2, "svg")] +
                          [("exposure", "acc-veil", 1, "png")] * 6)
                threads = [threading.Thread(target=operator, args=(jobs_a,)),
                           threading.Thread(target=operator, args=(jobs_b,))]
                for t in threads:
                    t.start()
                barrier.wait()
                for t in threads:
                    t.join()
                # resolve queued exposures to terminal state
                terminal = []
                for o in outcomes:
                    if o["via"] == "exposure" and o["status"] == 202:
                        job = o["job"]
                        for _ in range(600):
                            st, cur, _ = http("GET", "/v1/exposures/" + job["id"])
                            if cur["state"] in ("succeeded", "failed", "cancelled"):
                                terminal.append((o, cur))
                                break
                            time.sleep(.1)
                        else:
                            raise RuntimeError(f"exposure {job['id']} never reached terminal state")
                server.note_peak("storm")
                peaks["storm"] = server.peaks["storm"]
                sync_ok = [o for o in outcomes if o["via"] == "sync" and o["status"] == 201]
                sync_busy = [o for o in outcomes if o["via"] == "sync" and o["status"] == 429]
                sync_bad = [o for o in outcomes if o["via"] == "sync" and o["status"] not in (201, 429)]
                for o in sync_ok:
                    key = (o["plate"], o["revision"], o["format"])
                    if o["meta"]["digest"] != want[key]["digest"]:
                        raise RuntimeError("storm render digest mismatch")
                if sync_bad:
                    raise RuntimeError(f"sync renders failed unexpectedly during storm: {sync_bad}")
                accepted = [o for o in outcomes if o["via"] == "exposure" and o["status"] == 202]
                rejected = [o for o in outcomes if o["via"] == "exposure" and o["status"] == 429]
                other = [o for o in outcomes if o["via"] == "exposure" and o["status"] not in (202, 429)]
                if other:
                    raise RuntimeError(f"unexpected exposure statuses: {[o['status'] for o in other]}")
                succeeded, capacity_failed = [], []
                for o, cur in terminal:
                    if cur["state"] == "succeeded":
                        # terminal artifact must be the deterministic artifact for that scene
                        key = (o["plate"], o["revision"], o["format"])
                        if cur["artifact"] != want[key]["id"]:
                            raise RuntimeError(f"exposure artifact {cur['artifact'][:12]} != expected {want[key]['id'][:12]}")
                        succeeded.append(cur)
                    elif cur["state"] == "failed" and cur.get("error") == "exposure capacity reached":
                        capacity_failed.append(cur)  # explicit capacity rejection at execution
                    else:
                        raise RuntimeError(f"queued exposure ended {cur['state']}: {cur.get('error')}")
                if not succeeded and not sync_ok:
                    raise RuntimeError("no storm work succeeded; queueing not demonstrated")
                if not (rejected or capacity_failed or sync_busy):
                    raise RuntimeError("burst over queue 8 + workers 2 + 2 slots produced no explicit rejection")
                return (f"operators A(镂空)+B(透明): sync renders {len(sync_ok)}x201 (manifest digests) + "
                        f"{len(sync_busy)}x429; 12 exposures -> {len(succeeded)} succeeded, "
                        f"{len(rejected)} rejected 429 at submit, "
                        f"{len(capacity_failed)} failed 'exposure capacity reached' at execution; no 5xx, no phantoms")
            checks.run("two-operator-storm", two_operator_storm)

            def compute_slot_overflow():
                barrier = threading.Barrier(5)
                results, slot_lock = [], threading.Lock()

                def hit():
                    barrier.wait()
                    st, meta, _ = http("POST", "/v1/plates/acc-veil/render?revision=1&format=png")
                    with slot_lock:
                        results.append((st, meta))

                threads = [threading.Thread(target=hit) for _ in range(4)]
                for t in threads:
                    t.start()
                barrier.wait()
                for t in threads:
                    t.join()
                ok = [m for s, m in results if s == 201]
                busy = [s for s, _ in results if s == 429]
                other = [s for s, _ in results if s not in (201, 429)]
                server.note_peak("overflow")
                peaks["overflow"] = server.peaks["overflow"]
                if other or not ok or not busy:
                    raise RuntimeError(f"expected mix of 201/429 over 2 compute slots, got {[s for s, _ in results]}")
                digests = {m["digest"] for m in ok}
                if len(digests) != 1:
                    raise RuntimeError("concurrent renders of one scene produced divergent digests")
                return (f"4 concurrent sync renders over 2 slots: {len(ok)}x201 identical digest, {len(busy)}x429, "
                        f"no 5xx (boundary probe: peak {peaks['overflow']} KiB, outside the 2-caller envelope)")
            checks.run("compute-slot-overflow", compute_slot_overflow)

            def no_phantom_artifacts():
                snap = json.loads(state_file.read_bytes())
                expected = {a["id"] for o in manifest["orders"] for a in o["artifacts"]}
                new_order = getattr(new_order_flow, "order", None)
                if new_order:
                    expected |= {a["id"] for a in new_order["artifacts"]}
                got = set(snap["artifacts"].keys())
                if got != expected:
                    raise RuntimeError(f"artifact inventory drift: +{sorted(got - expected)[:3]} -{sorted(expected - got)[:3]}")
                for aid, art in snap["artifacts"].items():
                    if art["state"] != "available":
                        raise RuntimeError(f"artifact {aid[:12]} in unexpected state {art['state']}")
                proofs = {p["id"]: p for o in manifest["orders"] for p in o["proofs"]}
                for pid, p in proofs.items():
                    cur = snap["proofs"].get(pid)
                    if not cur or cur["state"] != p["state"] or cur["artifact"] != p["artifact"]:
                        raise RuntimeError(f"proof {pid} changed across restart/storm")
                return f"{len(got)} artifacts == manifest+new-order exactly, all available; {len(proofs)} proofs intact"
            checks.run("no-phantom-artifacts", no_phantom_artifacts)

            def proofs_survive_overflow():
                accepted = [(p, o) for o in manifest["orders"] for p in o["proofs"] if p["state"] == "accepted"][:3]
                for proof, order in accepted:
                    art = next(a for a in order["artifacts"] if a["id"] == proof["artifact"])
                    st, raw, _ = http("GET", "/v1/artifacts/" + art["id"], raw=True)
                    expect(st, 200, f"proof {proof['id']} after overflow")
                    if hashlib.sha256(raw).hexdigest() != art["digest"]:
                        raise RuntimeError(f"proof {proof['id']} digest mismatch after overflow")
                return f"{len(accepted)} old accepted proofs still downloadable with matching digests after 429 storm"
            checks.run("proofs-survive-overflow", proofs_survive_overflow)
        finally:
            server.stop("pre-shutdown")
            peaks["pre-shutdown"] = server.peaks.get("pre-shutdown")
    peaks = {k: v for k, v in peaks.items() if v}
    # The 4-caller overflow probe is a boundary measurement outside the supported
    # envelope (<=2 concurrent API callers at a ~32 MiB snapshot); it is reported
    # but not gated. All supported-envelope phases must stay under 256 MiB.
    envelope = {k: v for k, v in peaks.items() if k not in ("overflow", "pre-shutdown")}
    peak = max(envelope.values()) if envelope else None

    def memory_envelope():
        if peak is None:
            raise RuntimeError("VmHWM unavailable on this platform")
        if peak >= 256 * 1024:
            raise RuntimeError(f"envelope peak RSS {peak} KiB exceeds 256 MiB")
        detail = (f"envelope peak RSS {peak} KiB ({peak / 1024:.0f} MiB) across "
                  f"{','.join(envelope)}; margin {(256 * 1024 - peak) / 1024:.0f} MiB")
        if "overflow" in peaks:
            detail += f"; 4-caller boundary probe peaked {peaks['overflow']} KiB (not gated)"
        return detail
    checks.run("memory-envelope-256MiB", memory_envelope)

    report = {"schema": REPORT_SCHEMA, "home": str(home), "ok": checks.ok,
              "gomemlimit_mib": args.gomemlimit, "checks": checks.items,
              "vmhwm_kib": peaks, "envelope_peak_vmhwm_kib": peak,
              "overflow_probe_vmhwm_kib": peaks.get("overflow"),
              "seconds": round(time.time() - t0, 2)}
    (home / "verify-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({"verify": "passed" if checks.ok else "FAILED", "home": str(home),
                      "checks": f"{sum(1 for i in checks.items if i['ok'])}/{len(checks.items)}",
                      "envelope_peak_vmhwm_kib": peak,
                      "overflow_probe_vmhwm_kib": peaks.get("overflow"),
                      "seconds": report["seconds"]}, ensure_ascii=False))
    if not checks.ok:
        sys.exit(1)

# ---------------------------------------------------------------- compare
def cmd_compare(args):
    def load(home):
        m = json.loads((pathlib.Path(home) / "manifest.json").read_text())
        arts = {(a["plate"], a["revision"], a["format"]): a["digest"] for o in m["orders"] for a in o["artifacts"]}
        proofs = {p["id"]: (p["artifact"], p["state"]) for o in m["orders"] for p in o["proofs"]}
        return m, arts, proofs
    ma, aa, pa = load(args.a)
    mb, ab, pb = load(args.b)
    problems = []
    if set(aa) != set(ab):
        problems.append(f"artifact key sets differ: only-a={sorted(set(aa) - set(ab))[:3]} only-b={sorted(set(ab) - set(aa))[:3]}")
    for key in sorted(set(aa) & set(ab)):
        if aa[key] != ab[key]:
            problems.append(f"digest differs at {key}: {aa[key][:12]} != {ab[key][:12]}")
    if pa != pb:
        problems.append("proof maps differ")
    if problems:
        print(json.dumps({"compare": "DIFFERENT", "problems": problems[:10]}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps({"compare": "identical", "artifacts": len(aa), "proofs": len(pa),
                      "a": str(args.a), "b": str(args.b),
                      "a_gomemlimit": ma["config"]["gomemlimit_mib"],
                      "b_gomemlimit": mb["config"]["gomemlimit_mib"]}, ensure_ascii=False))

# ---------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    gen = sub.add_parser("generate", help="replay a deterministic shift history into --home")
    gen.add_argument("--home", required=True)
    gen.add_argument("--seed", type=int, default=20261120)
    gen.add_argument("--target-mib", type=float, default=31.0)
    gen.add_argument("--gomemlimit", type=int, default=0, help="MiB, 0 = unlimited")
    gen.add_argument("--render-parallel", type=int, default=2)
    gen.add_argument("--force", action="store_true")
    gen.set_defaults(fn=cmd_generate)
    ver = sub.add_parser("verify", help="restart on --home and run the acceptance checklist")
    ver.add_argument("--home", required=True)
    ver.add_argument("--gomemlimit", type=int, default=0, help="MiB, 0 = unlimited")
    ver.set_defaults(fn=cmd_verify)
    cmp_ = sub.add_parser("compare", help="prove two histories produced identical digests")
    cmp_.add_argument("--a", required=True)
    cmp_.add_argument("--b", required=True)
    cmp_.set_defaults(fn=cmd_compare)
    args = parser.parse_args()
    args.fn(args)

if __name__ == "__main__":
    main()
