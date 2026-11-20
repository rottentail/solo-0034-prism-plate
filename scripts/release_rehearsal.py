#!/usr/bin/env python3
"""Rehearse docs/release-manual.md against real artifacts from an ephemeral service.

Three field changes that are easy to misjudge at release time:
  case-a  PNG bytes changed while decoded pixels are unchanged (encoding-only drift)
  case-b  fine-edge geometry changed while the 2x2 PNG sampler sees no difference
  case-c  signed artifact stays downloadable while its source fails current admission

The script builds the repo binary, produces a signed "old release" from
examples/amber-proof.json, then checks each case with actual artifact bytes,
decoded full-pixel matrices, scene digests and SVG witnesses. It prints one JSON
evidence report and exits 0 only if every check holds. Nothing outside the
temporary directory is modified; the "next version" used in case-c is built from
a patched copy of the source tree, never from the repository itself.
"""
import copy, hashlib, json, pathlib, shutil, signal, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request, zlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def check(cond, label):
    if not cond:
        raise RuntimeError('CHECK FAILED: ' + label)


# ---- minimal PNG codec (stdlib only): the neutral decoder for pixel comparison ----
def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def png_decode(data):
    """Decode a non-interlaced 8-bit PNG to (width, height, color_type, pixel bytes)."""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    pos, idat, meta = 8, b'', None
    while pos < len(data):
        ln, = struct.unpack('>I', data[pos:pos + 4])
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b'IHDR':
            w, h, depth, ct, comp, filt, inter = struct.unpack('>IIBBBBB', chunk)
            if depth != 8 or comp != 0 or filt != 0 or inter != 0:
                raise ValueError('unsupported PNG encoding')
            meta = (w, h, ct)
        elif typ == b'IDAT':
            idat += chunk
        elif typ == b'IEND':
            break
    if meta is None:
        raise ValueError('missing IHDR')
    w, h, ct = meta
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}[ct]
    raw = zlib.decompress(idat)
    stride = w * bpp
    out, prev, p = bytearray(), bytearray(stride), 0
    for _ in range(h):
        f = raw[p]
        p += 1
        row = bytearray(raw[p:p + stride])
        p += stride
        if f == 1:
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 0xff
        elif f == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 0xff
        elif f == 3:
            for i in range(stride):
                row[i] = (row[i] + (((row[i - bpp] if i >= bpp else 0) + prev[i]) >> 1)) & 0xff
        elif f == 4:
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + paeth(a, prev[i], c)) & 0xff
        elif f != 0:
            raise ValueError('unknown PNG filter %d' % f)
        out += row
        prev = row
    return w, h, ct, bytes(out)


def png_encode(w, h, ct, pixels, level):
    """Re-encode pixels with filter 0 and an explicit zlib level: new bytes, same image."""
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}[ct]
    stride = w * bpp
    raw = b''.join(b'\x00' + pixels[y * stride:(y + 1) * stride] for y in range(h))
    co = zlib.compressobj(level)
    idat = co.compress(raw) + co.flush()

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, ct, 0, 0, 0)) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')


def max_channel_delta(a, b):
    if len(a) != len(b):
        return None
    m = 0
    for x, y in zip(a, b):
        d = abs(x - y)
        if d > m:
            m = d
    return m


class Service:
    def __init__(self, binary, data, logpath, port):
        self.binary, self.data, self.logpath, self.port = binary, data, logpath, port
        self.proc = None
        self.log = None
        self.url = f'http://127.0.0.1:{port}'

    def start(self):
        self.log = open(self.logpath, 'w')
        self.proc = subprocess.Popen([str(self.binary), '-listen', f'127.0.0.1:{self.port}', '-data', str(self.data)], stdout=self.log, stderr=self.log)
        for _ in range(100):
            if self.proc.poll() is not None:
                raise RuntimeError('service exited early: ' + pathlib.Path(self.logpath).read_text()[:400])
            try:
                urllib.request.urlopen(self.url + '/healthz', timeout=1).close()
                return
            except OSError:
                time.sleep(.05)
        raise RuntimeError('startup timed out')

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=45)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
                raise
        if self.log:
            self.log.close()

    def call(self, method, path, data=None, status=200, raw=False):
        body = data if isinstance(data, bytes) else (json.dumps(data).encode() if data is not None else None)
        req = urllib.request.Request(self.url + path, data=body, method=method, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=35) as r:
                code, payload, hs = r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            code, payload, hs = e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}
        if code != status:
            raise RuntimeError(f'{method} {path}: expected {status}, observed {code}: {payload[:200]!r}')
        return (payload, hs) if raw else (json.loads(payload) if payload else None)


def build_variant(src_dst, out_path):
    """Build the simulated next release: tightened admission + grandfathered startup.

    The repository tree is copied first; patches apply only to the copy.
    """
    for name in ('go.mod', 'cmd', 'internal'):
        s = ROOT / name
        d = src_dst / name
        if s.is_dir():
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)

    def patch(rel, old, new):
        p = src_dst / rel
        t = p.read_text()
        if old not in t:
            raise RuntimeError(f'patch anchor missing in {rel}')
        p.write_text(t.replace(old, new, 1))

    # New admission rule: canvas limited to 128. The amber drafts are 192x144,
    # so every persisted revision fails re-admission under the next release.
    patch('internal/validate/validate.go', 'd.Width > 512 || d.Height > 512', 'd.Width > 128 || d.Height > 128')
    patch('internal/validate/validate.go', 'maximum 512 square', 'maximum 128 square')
    # A real tightening release must grandfather persisted scenes at startup,
    # otherwise the service cannot boot on an existing data root at all.
    patch('cmd/prismplate/main.go',
          '\t\t\tif _, err = validate.Compile(r.Document); err != nil {\n\t\t\t\treturn err\n\t\t\t}',
          '\t\t\tif _, err = validate.Compile(r.Document); err != nil {\n\t\t\t\tlog.Printf("grandfathered persisted revision fails current admission: %v", err)\n\t\t\t}')
    subprocess.run(['go', 'build', '-o', str(out_path), './cmd/prismplate'], cwd=src_dst, check=True)


def main():
    seed = json.loads((ROOT / 'examples/amber-proof.json').read_text())
    report = {'manual': 'docs/release-manual.md', 'old_release': {}, 'cases': {}}
    with tempfile.TemporaryDirectory(prefix='prism-release-') as tmp:
        base = pathlib.Path(tmp)
        bin_v1 = base / 'prismplate-v1'
        subprocess.run(['go', 'build', '-o', str(bin_v1), './cmd/prismplate'], cwd=ROOT, check=True)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        data = base / 'state'
        svc = Service(bin_v1, data, base / 'v1.log', port)
        svc.start()
        try:
            # ---- phase 0: produce the old signed release ----
            created = svc.call('POST', '/v1/plates', seed, status=201)
            scene1 = created['revisions'][0]['digest']
            png1 = svc.call('POST', '/v1/plates/amber-plate/render?revision=1&format=png', status=201)
            svg1 = svc.call('POST', '/v1/plates/amber-plate/render?revision=1&format=svg', status=201)
            png1_bytes, png1_h = svc.call('GET', '/v1/artifacts/' + png1['id'], raw=True)
            svg1_bytes, _ = svc.call('GET', '/v1/artifacts/' + svg1['id'], raw=True)
            check(sha(png1_bytes) == png1['digest'] == png1_h['etag'].strip('"'), 'signed PNG digest matches bytes and ETag')
            check(png1_h['x-scene-digest'] == scene1 == png1['scene_digest'], 'scene digest consistent across plate, render and artifact header')
            again = svc.call('POST', '/v1/plates/amber-plate/render?revision=1&format=png', status=201)
            check(again['id'] == png1['id'], 'same source + same renderer reproduces the signed artifact id')
            svc.call('POST', '/v1/proofs', {'id': 'proof-release-1', 'artifact': png1['id']}, status=201)
            svc.call('PUT', '/v1/proofs/proof-release-1', {'state': 'accepted', 'note': '旧发布验样通过'})
            svc.call('DELETE', '/v1/artifacts/' + png1['id'], status=409)
            report['old_release'] = {'png_artifact': png1['id'], 'png_digest': png1['digest'],
                                     'svg_artifact': svg1['id'], 'svg_digest': svg1['digest'],
                                     'scene_digest': scene1, 'proof': 'accepted', 'revoke_blocked': True}

            # ---- case-a: PNG bytes changed, decoded pixels unchanged ----
            w, h, ct, pix1 = png_decode(png1_bytes)
            alt = png_encode(w, h, ct, pix1, 9)
            check(alt != png1_bytes and sha(alt) != png1['digest'], 'case-a re-encoded bytes and digest must differ')
            w2, h2, ct2, pix2 = png_decode(alt)
            check((w2, h2, ct2) == (w, h, ct), 'case-a PNG header geometry unchanged')
            delta_a = max_channel_delta(pix1, pix2)
            check(delta_a == 0, 'case-a decoded pixel matrices identical')
            report['cases']['case-a'] = {'signed_digest': png1['digest'], 'candidate_digest': sha(alt),
                                         'bytes_differ': True, 'canvas': [w, h], 'color_type': ct,
                                         'pixel_compare': 'full-matrix', 'pixel_max_channel_delta': delta_a,
                                         'verdict': 'keep-signed-reference',
                                         'if_new_bytes_required': 'rerender-only',
                                         'receiver_notification': 'not-required'}

            # ---- case-b: fine-edge geometry changed, PNG sampler blind ----
            doc2 = copy.deepcopy(seed['document'])
            doc2['layers'][1]['shapes'].append({
                'id': 'hairline-inlay', 'fill_rule': 'nonzero',
                'path': [{'kind': 'M', 'points': [{'x': 100.375, 'y': 40}]},
                         {'kind': 'L', 'points': [{'x': 100.625, 'y': 40}]},
                         {'kind': 'L', 'points': [{'x': 100.625, 'y': 120}]},
                         {'kind': 'L', 'points': [{'x': 100.375, 'y': 120}]},
                         {'kind': 'Z'}],
                'transform': [1, 0, 0, 1, 0, 0],
                'brush': {'kind': 'solid', 'color': {'r': 1, 'g': 1, 'b': 1, 'a': 0.9}}})
            rev2 = svc.call('PUT', '/v1/plates/amber-plate', {'expected_revision': 1, 'document': doc2})
            scene2 = rev2['revisions'][1]['digest']
            check(scene2 != scene1, 'case-b scene digest must change with the source')
            png2 = svc.call('POST', '/v1/plates/amber-plate/render?revision=2&format=png', status=201)
            svg2 = svc.call('POST', '/v1/plates/amber-plate/render?revision=2&format=svg', status=201)
            png2_bytes, _ = svc.call('GET', '/v1/artifacts/' + png2['id'], raw=True)
            svg2_bytes, _ = svc.call('GET', '/v1/artifacts/' + svg2['id'], raw=True)
            check(png2['digest'] == png1['digest'] and png2_bytes == png1_bytes, 'case-b PNG bytes identical: sampler is blind to the hairline')
            check(png2['id'] != png1['id'], 'case-b artifact id tracks lineage even when bytes match')
            check(svg2['digest'] != svg1['digest'] and svg2_bytes != svg1_bytes and b'100.375' in svg2_bytes, 'case-b SVG witnesses the geometry change')
            delta_b = max_channel_delta(png_decode(png1_bytes)[3], png_decode(png2_bytes)[3])
            check(delta_b == 0, 'case-b decoded pixels identical')
            report['cases']['case-b'] = {'scene_digest_changed': True, 'png_bytes_identical': True,
                                         'png_artifact_ids_differ': True, 'svg_digest_changed': True,
                                         'pixel_max_channel_delta': delta_b,
                                         'verdict': 'notify-receiver-reproof',
                                         'receiver_notification': 'required'}
        finally:
            svc.stop()

        # ---- case-c: signed artifact downloadable, source fails new admission ----
        variant_src = base / 'variant-src'
        variant_src.mkdir()
        bin_v2 = variant_src.parent / 'prismplate-v2'
        build_variant(variant_src, bin_v2)
        svc2 = Service(bin_v2, data, base / 'v2.log', port)
        svc2.start()
        try:
            got_bytes, got_h = svc2.call('GET', '/v1/artifacts/' + png1['id'], raw=True)
            check(got_bytes == png1_bytes and got_h['etag'].strip('"') == png1['digest'], 'case-c signed artifact still downloadable, bytes intact')
            err1 = svc2.call('POST', '/v1/plates/amber-plate/render?revision=1&format=png', status=400)
            err2 = svc2.call('POST', '/v1/plates/amber-plate/render?revision=2&format=png', status=400)
            svc2.call('DELETE', '/v1/artifacts/' + png1['id'], status=409)
            hist = svc2.call('GET', '/v1/plates/amber-plate')
            check(len(hist['revisions']) == 2, 'case-c history still readable under new admission')
            check('grandfathered' in (base / 'v2.log').read_text(), 'case-c variant logs grandfathered revisions at startup')
            # Recovery path: a compliant new source renders fine and becomes the re-proof candidate.
            doc3 = copy.deepcopy(doc2)
            doc3['width'] = 128
            doc3['height'] = 96
            rev3 = svc2.call('PUT', '/v1/plates/amber-plate', {'expected_revision': 2, 'document': doc3})
            png3 = svc2.call('POST', '/v1/plates/amber-plate/render?revision=3&format=png', status=201)
            check(rev3['revisions'][2]['digest'] == png3['scene_digest'], 'case-c recovery revision renders under new admission')
            report['cases']['case-c'] = {'old_artifact_downloadable': True, 'bytes_match_signed': True,
                                         'rerender_revision_1_status': 400, 'rerender_revision_1_error': err1['error'],
                                         'rerender_revision_2_status': 400, 'rerender_revision_2_error': err2['error'],
                                         'revoke_still_blocked': True, 'grandfathered_startup_logged': True,
                                         'recovery_revision': 3, 'recovery_artifact': png3['id'],
                                         'verdict': 'keep-signed-reference-final+mark-non-reproducible',
                                         'new_output_requires': 'notify-receiver-reproof'}
        finally:
            svc2.stop()
    report['result'] = 'passed'
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
