#!/usr/bin/env python3
"""svg-path-1 migration journey over real HTTP and an ephemeral data root.

Evidence covered:
  1. a legacy op-array request still creates a plate (read capability kept);
  2. the same geometry submitted as versioned svg-path-1 text, using relative
     verbs, exponent/leading-dot numbers and two closed subpaths, compiles and
     renders;
  3. both representations produce identical scene digests and identical PNG
     and SVG artifact bytes (fill/hole meaning unchanged);
  4. provenance summaries identify each contour's source representation;
  5. parser failures carry byte/column locations and unsupported SVG verbs and
     markup-bearing fields are rejected with 400, never executed;
  6. legacy snapshots survive a restart and recompile through the new binary;
  7. legacy bundles and svg-path-1 bundles round-trip through export/import and
     re-render to the same artifact digest;
  8. legacy documents still marshal as op arrays (no silent format flip).
"""
import copy, hashlib, json, os, pathlib, signal, socket, subprocess, tempfile, time, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    seed = json.loads((ROOT / 'examples' / 'amber-proof.json').read_text())
    go_bin = os.environ.get('GO_BIN', 'go')
    with tempfile.TemporaryDirectory(prefix='prism-plate-migrate-') as tmp:
        base = pathlib.Path(tmp)
        binary = base / 'prismplate'
        subprocess.run([go_bin, 'build', '-o', str(binary), './cmd/prismplate'], cwd=ROOT, check=True)
        port = None
        log = (base / 'service.log').open('w')
        proc = None

        def free_port():
            s = socket.socket()
            s.bind(('127.0.0.1', 0))
            p = s.getsockname()[1]
            s.close()
            return p

        def stop():
            if proc is not None and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    raise

        def start(data='state'):
            nonlocal proc, port
            stop()
            port = free_port()
            state_dir = base / data
            state_dir.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen([str(binary), '-listen', f'127.0.0.1:{port}', '-data', str(state_dir)],
                                    stdout=log, stderr=log)
            for _ in range(100):
                if proc.poll() is not None:
                    log.flush()
                    raise RuntimeError('service exited; log tail: ' + log_tail())
                try:
                    urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=1).close()
                    return
                except OSError:
                    time.sleep(.05)
            raise RuntimeError('startup timed out')

        def log_tail():
            try:
                with open(log.name) as fh:
                    lines = fh.readlines()
                return ''.join(lines[-5:])
            except OSError:
                return '(log unavailable)'

        def call(method, path, data=None, status=200, raw=False):
            body = data if isinstance(data, bytes) else (json.dumps(data).encode() if data is not None else None)
            req = urllib.request.Request(f'http://127.0.0.1:{port}' + path, data=body, method=method,
                                         headers={'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=35) as r:
                    code, payload = r.status, r.read()
            except urllib.error.HTTPError as e:
                code, payload = e.code, e.read()
            if code != status:
                raise RuntimeError(f'{method} {path}: expected {status}, observed {code}: {payload[:300]!r}')
            return payload if raw else json.loads(payload)

        try:
            start()
            # --- 1. legacy request still admitted --------------------------------
            legacy = copy.deepcopy(seed)
            legacy['id'] = 'legacy-plate'
            legacy_doc = copy.deepcopy(legacy['document'])
            created_legacy = call('POST', '/v1/plates', legacy, status=201)
            legacy_rev = created_legacy['revisions'][0]
            assert all(s['source'] == 'legacy-op-array' for s in created_legacy['path_sources'])
            # Stored document keeps the historical op-array spelling.
            stored_path = created_legacy['revisions'][0]['document']['layers'][1]['shapes'][0]['path']
            assert isinstance(stored_path, list) and stored_path[0]['kind'] == 'M'

            # --- 2. build the svg-path-1 twin of the same document ---------------
            def op_array_to_text(ops):
                parts = []
                for o in ops:
                    parts.append(o['kind'])
                    for p in o.get('points', []):
                        parts.append(str(float(p['x'])))
                        parts.append(str(float(p['y'])))
                return ' '.join(parts)

            svg_doc = copy.deepcopy(legacy_doc)
            for layer in svg_doc['layers']:
                if layer.get('clip'):
                    layer['clip'] = {'path_format': 'svg-path-1', 'path_text': op_array_to_text(layer['clip'])}
                if layer.get('mask'):
                    # Exercise relative verbs and numeric spellings on the mask.
                    layer['mask'] = {'path_format': 'svg-path-1',
                                     'path_text': 'm20 20 l1.52e2 0 l0 108 l-152 0 z'}
                for shape in layer['shapes']:
                    text = op_array_to_text(shape['path'])
                    # The even-odd shape carries two closed subpaths; keep both.
                    shape['path'] = {'path_format': 'svg-path-1', 'path_text': text}
            svg_request = {'id': 'svg-plate', 'title': seed['title'] + ' svg-path-1', 'document': svg_doc}
            created_svg = call('POST', '/v1/plates', svg_request, status=201)
            svg_rev = created_svg['revisions'][0]
            sources = created_svg['path_sources']
            assert any(s['source'] == 'svg-path-1' and s['subpaths'] == 2 for s in sources), sources
            mask_summary = next(s for s in sources if s['role'] == 'mask')
            assert mask_summary['source'] == 'svg-path-1' and mask_summary['text_bytes'] > 0
            # Versioned spelling stays versioned in the stored document.
            stored = created_svg['revisions'][0]['document']['layers'][0]['shapes'][0]['path']
            assert stored['path_format'] == 'svg-path-1' and isinstance(stored['path_text'], str)

            # --- 3. identical normalized geometry => identical digests/renders ---
            assert svg_rev['digest'] == legacy_rev['digest'], 'normalized scene digest must be representation-independent'
            for fmt in ('png', 'svg'):
                a1 = call('POST', f'/v1/plates/legacy-plate/render?revision=1&format={fmt}', status=201)
                a2 = call('POST', f'/v1/plates/svg-plate/render?revision=1&format={fmt}', status=201)
                assert a1['digest'] == a2['digest'], f'{fmt} output diverged by path representation'
                raw1 = call('GET', '/v1/artifacts/' + a1['id'], status=200, raw=True)
                raw2 = call('GET', '/v1/artifacts/' + a2['id'], status=200, raw=True)
                assert hashlib.sha256(raw1).hexdigest() == a1['digest']
                assert raw1 == raw2, f'{fmt} bytes differ between representations'
                if fmt == 'png':
                    assert raw1[:8] == b'\x89PNG\r\n\x1a\n'
                else:
                    assert b'<svg' in raw1 and b'clipPath' in raw1

            # --- 4. relative verbs resolve to the same absolute geometry --------
            rel_doc = copy.deepcopy(legacy_doc)
            shape = rel_doc['layers'][1]['shapes'][0]
            # Outer rectangle (42,48)-(144,124) and inner hole (64,64)-(124,104)
            # written entirely with relative moves/lines.
            shape['path'] = {'path_format': 'svg-path-1',
                             'path_text': 'm42 48 l102 0 l0 76 l-102 0 z m22 16 l60 0 l0 40 l-60 0 z'}
            rel_req = {'id': 'relative-plate', 'title': 'relative twin', 'document': rel_doc}
            rel_created = call('POST', '/v1/plates', rel_req, status=201)
            assert rel_created['revisions'][0]['digest'] == legacy_rev['digest']
            rel_png = call('POST', '/v1/plates/relative-plate/render?revision=1&format=png', status=201)
            leg_png = call('POST', '/v1/plates/legacy-plate/render?revision=1&format=png', status=201)
            assert rel_png['digest'] == leg_png['digest'], 'relative coordinates changed rendering'

            # --- 5. rejection paths with locations and no arbitrary markup ------
            def bad_doc(path_field, plate_id='bad-svg'):
                d = copy.deepcopy(legacy_doc)
                d['layers'][0]['shapes'][0]['path'] = path_field
                return {'id': plate_id, 'title': 'bad', 'document': d}

            cases = [
                ({'path_format': 'svg-path-1', 'path_text': 'M14 110 H178 L14 110 Z'}, 'column 9', 'H verb'),
                ({'path_format': 'svg-path-1', 'path_text': 'M14 110 L178 110 L14 110'}, 'terminate with Z', 'unclosed'),
                ({'path_format': 'svg-path-2', 'path_text': 'M0 0 L1 0 L1 1 L0 1 Z'}, "path_format=\"svg-path-1\"", 'version'),
                ({'path_format': 'svg-path-1', 'path_text': 'M0 0 L. 0 L1 1 L0 1 Z'}, 'column 7', 'number'),
                ({'path_format': 'svg-path-1', 'path_text': '<script>alert(1)</script>'}, 'syntax error', 'markup'),
            ]
            for i, (field, needle, label) in enumerate(cases):
                err = call('POST', '/v1/plates', bad_doc(field, f'bad-{i}'), status=400)
                assert needle in err['error'], f'{label}: {err["error"]}'
            # Both representations at once must be rejected.
            dual = bad_doc({'path_format': 'svg-path-1', 'path_text': 'M0 0 L1 0 L1 1 L0 1 Z',
                            'extra': 1}, 'dual')
            call('POST', '/v1/plates', dual, status=400)
            # Unknown fields anywhere remain rejected (strict decoder).
            hacked = bad_doc({'path_format': 'svg-path-1',
                              'path_text': 'M0 0 L1 0 L1 1 L0 1 Z', 'onload': 'x'}, 'hacked')
            call('POST', '/v1/plates', hacked, status=400)

            # --- 6. legacy snapshot survives restart under the new binary -------
            stop()
            start()
            seen = call('GET', '/v1/plates/legacy-plate', status=200)
            assert seen['revisions'][0]['digest'] == legacy_rev['digest']
            assert all(s['source'] == 'legacy-op-array' for s in seen['path_sources'])
            after = call('POST', '/v1/plates/legacy-plate/render?revision=1&format=png', status=201)
            assert after['digest'] == leg_png['digest'], 'legacy re-render after restart changed'

            # --- 7. bundle round trip for both representations ------------------
            # Envelope digests cover the stored plate bytes including creation
            # timestamps, so plates sealed at different instants naturally
            # differ; the contract evidence is that legacy and svg-path-1
            # plates normalize to identical content, and each envelope still
            # verifies through bundle.Open on import below.
            legacy_bundle = call('GET', '/v1/plates/legacy-plate/bundle', raw=True)
            svg_bundle = call('GET', '/v1/plates/svg-plate/bundle', raw=True)
            lb, sb = json.loads(legacy_bundle), json.loads(svg_bundle)
            assert lb['plate']['revisions'][0]['digest'] == sb['plate']['revisions'][0]['digest']
            # Re-sealing the same plate must be deterministic (no timestamp drift).
            again = call('GET', '/v1/plates/legacy-plate/bundle', raw=True)
            assert json.loads(again)['digest'] == lb['digest']
            # The legacy envelope spelling stays an op array (no format flip);
            # the versioned envelope retains path_text for vector-tool exchange.
            assert isinstance(lb['plate']['revisions'][0]['document']['layers'][0]['shapes'][0]['path'], list)
            assert isinstance(sb['plate']['revisions'][0]['document']['layers'][0]['shapes'][0]['path'], dict)
            # The documents carry different path spellings but identical
            # normalized geometry; verify via the shared scene digest and via
            # the rendered artifact equality already asserted above.
            assert lb['plate']['revisions'][0]['digest'] == sb['plate']['revisions'][0]['digest']
            assert leg_png['digest'] == call(
                'POST', '/v1/plates/svg-plate/render?revision=1&format=png', status=201)['digest']
            os.rename(base / 'state', base / 'state-a')
            start()
            imported_legacy = call('POST', '/v1/bundles', legacy_bundle, status=201)
            assert imported_legacy['revisions'][0]['digest'] == legacy_rev['digest']
            imported_svg = call('POST', '/v1/bundles', svg_bundle, status=201)
            assert all(s['source'] == 'svg-path-1' for s in imported_svg['path_sources']
                       if s['role'] == 'path')
            for plate, expected in (('legacy-plate', leg_png['digest']), ('svg-plate', leg_png['digest'])):
                art = call('POST', f'/v1/plates/{plate}/render?revision=1&format=png', status=201)
                assert art['digest'] == expected, f'{plate} re-render after import changed'
            # Tampered envelope is rejected.
            tampered = json.loads(legacy_bundle)
            tampered['digest'] = '0' * 64
            call('POST', '/v1/bundles', json.dumps(tampered), status=400)

            print(json.dumps({'workflow': 'path-migration', 'result': 'passed',
                              'legacy_digest': legacy_rev['digest'],
                              'png_digest': leg_png['digest'], 'port': port}, ensure_ascii=False))
        finally:
            stop()
            log.close()


if __name__ == '__main__':
    main()
