"""Icon atelier: build 16x16 prism-plate scenes, render them through the real
HTTP service, and derive readability measurements.

The atelier never reimplements the renderer. Every image it grades is produced by
the service binary built from this repository, fetched from
GET /v1/artifacts/{id}, so the measurements describe what the product actually
publishes.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import pngio  # noqa: E402

# ---------------------------------------------------------------------------
# scene helpers


def solid(r, g, b, a=1.0):
    return {"kind": "solid", "color": {"r": r, "g": g, "b": b, "a": a}}


def poly(*points):
    ops = [{"kind": "M", "points": [{"x": points[0][0], "y": points[0][1]}]}]
    for x, y in points[1:]:
        ops.append({"kind": "L", "points": [{"x": x, "y": y}]})
    ops.append({"kind": "Z"})
    return ops


def rect(x0, y0, x1, y1):
    return poly((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def ring(x0, y0, x1, y1, t):
    """Outer rectangle minus inner rectangle as one evenodd path."""
    cx0, cy0, cx1, cy1 = x0 + t, y0 + t, x1 - t, y1 - t
    return (
        poly((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        + poly((cx0, cy0), (cx1, cy0), (cx1, cy1), (cx0, cy1))
    )


def shape(sid, path, fill, fill_rule="nonzero", transform=None):
    return {
        "id": sid,
        "path": path,
        "fill_rule": fill_rule,
        "brush": fill,
        "transform": transform or [1, 0, 0, 1, 0, 0],
    }


def layer(lid, shapes, opacity=1.0, blend="normal"):
    return {"id": lid, "opacity": opacity, "blend": blend, "shapes": shapes}


def document(background, *layers, width=16, height=16):
    return {
        "width": width,
        "height": height,
        "background": {"r": background[0], "g": background[1], "b": background[2], "a": background[3] if len(background) > 3 else 1.0},
        "layers": list(layers),
    }


# ---------------------------------------------------------------------------
# service harness (ephemeral port, temporary data root, real HTTP)

class Service:
    def __init__(self, binary: pathlib.Path | None = None, data: pathlib.Path | None = None):
        self.tmp = None
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(prefix="prism-icon-")
        base = pathlib.Path(self.tmp.name)
        if binary is None:
            binary = base / "prismplate"
            subprocess.run(
                ["go", "build", "-trimpath", "-o", str(binary), "./cmd/prismplate"],
                cwd=ROOT, check=True, capture_output=True,
            )
        self.binary = binary
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.url = f"http://127.0.0.1:{self.port}"
        self.log = (base / "service.log").open("w")
        self.proc = subprocess.Popen(
            [str(binary), "-listen", f"127.0.0.1:{self.port}", "-data", str(base / "state")],
            stdout=self.log, stderr=self.log,
        )
        for _ in range(200):
            if self.proc.poll() is not None:
                raise RuntimeError("service exited during startup")
            try:
                urllib.request.urlopen(self.url + "/healthz", timeout=1).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("service startup timed out")

    def call(self, method, path, payload=None, status=200, raw=False):
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.url + path, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                code, data, headers = r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            code, data, headers = e.code, e.read(), dict(e.headers)
        if code != status:
            raise RuntimeError(f"{method} {path}: expected {status} got {code}: {data[:300]!r}")
        if raw:
            return data, headers
        return json.loads(data) if data else None

    def create(self, plate_id, doc, title="icon proof"):
        return self.call("POST", "/v1/plates", {"id": plate_id, "title": title, "document": doc}, status=201)

    def render(self, plate_id, revision=1, fmt="png"):
        meta = self.call("POST", f"/v1/plates/{plate_id}/render?revision={revision}&format={fmt}", status=201)
        data, headers = self.call("GET", "/v1/artifacts/" + meta["id"], raw=True)
        return meta, data, headers

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=45)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.log.close()
        self.tmp.cleanup()


# ---------------------------------------------------------------------------
# image analysis

def alpha_stats(w, h, rgba):
    """Alpha histogram plus the set of fully opaque and fully clear pixels."""
    opaque = clear = partial = 0
    for i in range(w * h):
        a = rgba[i * 4 + 3]
        if a == 255:
            opaque += 1
        elif a == 0:
            clear += 1
        else:
            partial += 1
    return {"opaque": opaque, "clear": clear, "partial": partial, "total": w * h}


def composite_over(w, h, rgba, bg):
    """Source-over an RGBA image onto an opaque sRGB background triple."""
    out = bytearray(w * h * 3)
    for i in range(w * h):
        r, g, b, a = rgba[i * 4:i * 4 + 4]
        af = a / 255.0
        out[i * 3] = int(round(r * af + bg[0] * (1 - af)))
        out[i * 3 + 1] = int(round(g * af + bg[1] * (1 - af)))
        out[i * 3 + 2] = int(round(b * af + bg[2] * (1 - af)))
    return bytes(out)


def luma(r, g, b):
    # Rec. 709 linearisation-free luma, the same integer form a grayscale
    # readout applies to sRGB code values.
    return (2126 * r + 7152 * g + 722 * b) / 10000.0


def luminance_linear(r, g, b):
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_ratio(r1, g1, b1, r2, g2, b2):
    l1, l2 = luminance_linear(r1, g1, b1), luminance_linear(r2, g2, b2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def grayscale(w, h, rgb):
    out = bytearray(w * h * 3)
    for i in range(w * h):
        y = int(round(luma(rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2])))
        out[i * 3:i * 3 + 3] = bytes((y, y, y))
    return bytes(out)


def mark_pixels(w, h, rgba, threshold=8):
    """Pixels visibly present against the plate's own background.

    A pixel counts as a mark when its composited colour differs from the
    background composite by more than `threshold` code values on any channel.
    This is the alpha-free definition a list row actually perceives.
    """
    bg = composite_over(w, h, bytes(w * h * 4), (0, 0, 0))
    return bg


def gradient_field(w, h, rgb):
    """Per-pixel Sobel magnitude on grayscale, as a coarse 'stroke survives' probe."""
    gray = [luma(rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2]) for i in range(w * h)]
    mag = [0.0] * (w * h)
    for y in range(h):
        for x in range(w):
            def g(dx, dy):
                xx, yy = x + dx, y + dy
                if 0 <= xx < w and 0 <= yy < h:
                    return gray[yy * w + xx]
                return gray[y * w + x]
            gx = (g(1, -1) + 2 * g(1, 0) + g(1, 1)) - (g(-1, -1) + 2 * g(-1, 0) + g(-1, 1))
            gy = (g(-1, 1) + 2 * g(0, 1) + g(1, 1)) - (g(-1, -1) + 2 * g(0, -1) + g(1, -1))
            mag[y * w + x] = (gx * gx + gy * gy) ** 0.5
    return mag


def summarize_image(w, h, rgba, backgrounds):
    """Grade one rendered icon against every background it must survive."""
    result = {"alpha": alpha_stats(w, h, rgba), "backgrounds": {}}
    base = composite_over(w, h, rgba, (0, 0, 0))
    for name, bg in backgrounds.items():
        comp = composite_over(w, h, rgba, bg)
        gray = grayscale(w, h, comp)
        mag = gradient_field(w, h, comp)
        # distinct code values present in the mark-bearing area (alpha > 0)
        vals = set()
        for i in range(w * h):
            if rgba[i * 4 + 3] > 0:
                vals.add((comp[i * 3] >> 3, comp[i * 3 + 1] >> 3, comp[i * 3 + 2] >> 3))
        result["backgrounds"][name] = {
            "bg_rgb": list(bg),
            "distinct_5bit_colours": len(vals),
            "max_gradient": round(max(mag), 2),
            "mean_gradient": round(sum(mag) / len(mag), 3),
        }
    return result
