#!/usr/bin/env python3
"""Generate the regional-annotation review example against a live local service.

The script builds its own binary in a temporary data root, drives the rejected-proof
journey over real HTTP, and writes reviewable artifacts into examples/review:

  proof-revision1.png      immutable final image of the rejected proof
  draft-revision2.png      new draft after the affine change
  draft-revision3.png      later draft after canvas resize and layer removal
  review-sheet-revision2.svg  original proof vs draft 2 with projected regions
  review-sheet-revision3.svg  original proof vs draft 3 with orphan/rescale states
  review-notes.json        the projection ledger with explicit association status

Run: python3 scripts/annotation_demo.py
"""
import copy, json, pathlib, shutil, signal, socket, subprocess, tempfile, time, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / 'examples' / 'review'


def main():
    seed = json.loads((ROOT / 'examples/amber-proof.json').read_text())
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='prism-annotation-demo-'))
    try:
        binary = tmp / 'prismplate'
        subprocess.run(['go', 'build', '-o', str(binary), './cmd/prismplate'], cwd=ROOT, check=True)
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
        url = f'http://127.0.0.1:{port}'
        log = (tmp / 'service.log').open('w')
        proc = subprocess.Popen([str(binary), '-listen', f'127.0.0.1:{port}', '-data', str(tmp / 'state')],
                                stdout=log, stderr=log)

        def wait_ready():
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError('service exited: ' + log.read_text())
                try:
                    urllib.request.urlopen(url + '/healthz', timeout=1).close(); return
                except OSError:
                    time.sleep(.05)
            raise RuntimeError('startup timed out')

        def call(method, path, data=None, status=200, raw=False):
            body = json.dumps(data).encode() if data is not None else None
            req = urllib.request.Request(url + path, data=body, method=method,
                                         headers={'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=35) as r:
                    code, payload = r.status, r.read()
            except urllib.error.HTTPError as e:
                code, payload = e.code, e.read()
            if code != status:
                raise RuntimeError(f'{method} {path}: expected {status}, got {code}: {payload[:200]!r}')
            return payload if raw else json.loads(payload)

        wait_ready()
        call('POST', '/v1/plates', seed, 201)

        def render(revision):
            return call('POST', f'/v1/plates/amber-plate/render?revision={revision}&format=png', status=201)

        def artifact_png(meta):
            return call('GET', '/v1/artifacts/' + meta['id'], raw=True)

        # Revision 1: final image, proof, three regional annotations, rejection.
        art1 = render(1)
        call('POST', '/v1/proofs', {'id': 'proof-r1', 'artifact': art1['id']}, 201)
        annotations = [
            {'id': 'note-cyan-window', 'region': {'x': 56, 'y': 56, 'w': 16, 'h': 14},
             'comment': '青色窗口边缘与镂空对齐有偏差，请收紧仿射偏移'},
            {'id': 'note-amber-body', 'region': {'x': 80, 'y': 40, 'w': 32, 'h': 24},
             'comment': '琥珀主形渐变中段偏暗，提亮 8% 左右'},
            {'id': 'note-corner', 'region': {'x': 2, 'y': 2, 'w': 12, 'h': 10},
             'comment': '左上角留白保留为出血位，不要放任何元素'},
        ]
        for n in annotations:
            call('POST', '/v1/proofs/proof-r1/annotations', n, 201)
        call('PUT', '/v1/proofs/proof-r1', {'state': 'rejected', 'note': '按区域批注修订后重提'}, 200)
        png1 = artifact_png(art1)

        # Revision 2: cyan-window affine changes -> relocated; others anchored/absolute.
        doc2 = copy.deepcopy(seed['document'])
        doc2['layers'][1]['shapes'][0]['transform'] = [1, 0.12, -0.08, 1, 24, -10]
        call('PUT', '/v1/plates/amber-plate', {'expected_revision': 1, 'document': doc2}, 200)
        art2 = render(2)
        png2 = artifact_png(art2)
        sheet2 = call('GET', '/v1/plates/amber-plate/review-sheet?revision=2', raw=True)
        ledger2 = call('GET', '/v1/plates/amber-plate/annotations?revision=2')

        # Revision 3: canvas resized and cyan layer removed -> orphaned and rescaled.
        doc3 = copy.deepcopy(doc2)
        doc3['width'], doc3['height'] = 200, 150
        doc3['layers'] = [doc3['layers'][0]]
        call('PUT', '/v1/plates/amber-plate', {'expected_revision': 2, 'document': doc3}, 200)
        art3 = render(3)
        png3 = artifact_png(art3)
        sheet3 = call('GET', '/v1/plates/amber-plate/review-sheet?revision=3', raw=True)
        ledger3 = call('GET', '/v1/plates/amber-plate/annotations?revision=3')

        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'proof-revision1.png').write_bytes(png1)
        (OUT / 'draft-revision2.png').write_bytes(png2)
        (OUT / 'draft-revision3.png').write_bytes(png3)
        (OUT / 'review-sheet-revision2.svg').write_bytes(sheet2)
        (OUT / 'review-sheet-revision3.svg').write_bytes(sheet3)
        (OUT / 'review-notes.json').write_text(json.dumps(
            {'revision2_projection': ledger2, 'revision3_projection': ledger3},
            ensure_ascii=False, indent=2), encoding='utf-8')

        proc.send_signal(signal.SIGTERM); proc.wait(timeout=45)
        log.close()
        print(json.dumps({'written': sorted(p.name for p in OUT.iterdir())}, ensure_ascii=False))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
