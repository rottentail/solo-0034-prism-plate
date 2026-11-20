#!/usr/bin/env python3
"""Run bounded production HTTP journeys against an isolated ephemeral service."""
import argparse, concurrent.futures, copy, hashlib, json, os, pathlib, signal, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]

def run_annotation(call, render, seed, base, start, stop):
    """Rejected proof with region annotations, projection across revisions, handling."""
    # Revision 1: render the final image, open a proof and record regional feedback.
    a1 = render('png', 1)
    call('POST', '/v1/proofs', {'id': 'proof-r1', 'artifact': a1['id']}, status=201)
    n1 = {'id': 'note-cyan-window', 'region': {'x': 56, 'y': 56, 'w': 16, 'h': 14},
          'comment': '青色窗口边缘与镂空对齐有偏差，请收紧仿射偏移'}
    n2 = {'id': 'note-corner', 'region': {'x': 2, 'y': 2, 'w': 12, 'h': 10},
          'comment': '左上角留白希望保留为出血位'}
    n3 = {'id': 'note-amber-body', 'region': {'x': 80, 'y': 40, 'w': 32, 'h': 24},
          'comment': '琥珀主形渐变中段偏暗'}
    for n in (n1, n2, n3):
        got = call('POST', '/v1/proofs/proof-r1/annotations', n, status=201)
        assert got['state'] == 'open' and got['revision'] == 1
    assert call('POST', '/v1/proofs/proof-r1/annotations', n1, status=409)
    # Anchor capture: the region over a painted shape pins to that shape and stores
    # its local coordinates; the corner region has no shape under it.
    stored = call('POST', '/v1/proofs/proof-r1/annotations',
                  {'id': 'note-again', 'region': n1['region'], 'comment': 'x'}, status=201)
    assert stored['anchor'] is not None and stored['anchor']['shape'] == 'cyan-window'
    # Handling requires a later existing revision; revision 1 cannot close its own notes.
    call('PUT', '/v1/annotations/note-again', {'state': 'dismissed', 'handled_by_revision': 1}, status=400)
    call('PUT', '/v1/annotations/missing-note', {'state': 'resolved', 'handled_by_revision': 2}, status=404)
    # Admission failures.
    call('POST', '/v1/proofs/proof-r1/annotations',
         {'id': 'note-bad', 'region': {'x': 0, 'y': 0, 'w': 999, 'h': 9}, 'comment': '越界'}, status=400)
    call('POST', '/v1/proofs/missing/annotations',
         {'id': 'note-x', 'region': {'x': 1, 'y': 1, 'w': 2, 'h': 2}, 'comment': 'x'}, status=404)
    svg = render('svg', 1)
    call('POST', '/v1/proofs', {'id': 'proof-svg', 'artifact': svg['id']}, status=201)
    call('POST', '/v1/proofs/proof-svg/annotations',
         {'id': 'note-svg', 'region': {'x': 1, 'y': 1, 'w': 2, 'h': 2}, 'comment': 'x'}, status=400)
    # Rejection freezes the proof and its annotations; the terminal image is retained.
    rejected = call('PUT', '/v1/proofs/proof-r1',
                    {'state': 'rejected', 'note': '区域批注已记录，按意见修订后重提'}, status=200)
    assert rejected['state'] == 'rejected'
    call('POST', '/v1/proofs/proof-r1/annotations',
         {'id': 'note-late', 'region': {'x': 1, 'y': 1, 'w': 2, 'h': 2}, 'comment': 'x'}, status=409)
    call('DELETE', '/v1/artifacts/' + a1['id'], status=409)
    raw1, _ = call('GET', '/v1/artifacts/' + a1['id'], raw=True)
    assert raw1[:8] == b'\x89PNG\r\n\x1a\n'

    # Revision 2: cyan-window affine changes; its annotation must be recomputed,
    # never silently pasted by shared id.
    doc2 = copy.deepcopy(seed['document'])
    doc2['layers'][1]['shapes'][0]['transform'] = [1, 0.12, -0.08, 1, 24, -10]
    call('PUT', '/v1/plates/amber-plate', {'expected_revision': 1, 'document': doc2}, status=200)
    review2 = call('GET', '/v1/plates/amber-plate/annotations?revision=2')
    assert review2['pending_open_annotations'] == 4, review2
    statuses = {p['annotation']['id']: p for grp in review2['sources'] for p in grp['projections']}
    assert statuses['note-cyan-window']['status'] == 'relocated' and statuses['note-cyan-window']['unverified']
    assert statuses['note-amber-body']['status'] == 'anchored' and not statuses['note-amber-body']['unverified']
    assert statuses['note-corner']['status'] == 'absolute'
    assert statuses['note-cyan-window']['region']['x'] != n1['region']['x']
    sheet2, _ = call('GET', '/v1/plates/amber-plate/review-sheet?revision=2', raw=True)
    assert b'<svg' in sheet2 and b'relocated' in sheet2 and b'note-cyan-window' in sheet2
    (base / 'review-sheet-rev2.svg').write_bytes(sheet2)

    # Revision 3: canvas resized and the cyan layer removed. Orphan and rescale
    # associations must be explicit rather than re-attached by name.
    doc3 = copy.deepcopy(doc2)
    doc3['width'], doc3['height'] = 200, 150
    doc3['layers'] = [doc3['layers'][0]]
    call('PUT', '/v1/plates/amber-plate', {'expected_revision': 2, 'document': doc3}, status=200)
    review3 = call('GET', '/v1/plates/amber-plate/annotations?revision=3')
    statuses = {p['annotation']['id']: p for grp in review3['sources'] for p in grp['projections']}
    assert statuses['note-cyan-window']['status'] == 'orphaned' and statuses['note-cyan-window']['unverified']
    assert statuses['note-cyan-window']['region'] == n1['region'], 'orphan keeps original coordinates'
    assert statuses['note-amber-body']['status'] == 'rescaled' and statuses['note-amber-body']['unverified']
    assert statuses['note-corner']['status'] == 'rescaled'
    call('GET', '/v1/plates/amber-plate/annotations?revision=99', status=404)
    sheet3, _ = call('GET', '/v1/plates/amber-plate/review-sheet?revision=3', raw=True)
    (base / 'review-sheet-rev3.svg').write_bytes(sheet3)
    assert b'rescaled' in sheet3 and b'anchored' in sheet3

    # Handle the open feedback against the later revision; immutable comments stay.
    call('PUT', '/v1/annotations/note-cyan-window',
         {'state': 'dismissed', 'handled_by_revision': 3}, status=200)
    call('PUT', '/v1/annotations/note-again',
         {'state': 'dismissed', 'handled_by_revision': 3}, status=200)
    call('PUT', '/v1/annotations/note-amber-body',
         {'state': 'resolved', 'handled_by_revision': 3}, status=200)
    call('PUT', '/v1/annotations/note-amber-body',
         {'state': 'resolved', 'handled_by_revision': 3}, status=409)
    call('PUT', '/v1/annotations/note-corner',
         {'state': 'resolved', 'handled_by_revision': 99}, status=400)
    call('PUT', '/v1/annotations/note-corner',
         {'state': 'resolved', 'handled_by_revision': 3}, status=200)
    review3b = call('GET', '/v1/plates/amber-plate/annotations?revision=3')
    assert review3b['pending_open_annotations'] == 0 and review3b['sources'] == []
    empty_sheet, _ = call('GET', '/v1/plates/amber-plate/review-sheet?revision=3', raw=True)
    assert empty_sheet[:5] == b'<svg ' and b'<image' in empty_sheet and b'note-' not in empty_sheet
    # Reopen a fresh proof on revision 3 and record one new note for the recovery check.
    a3 = render('png', 3)
    call('POST', '/v1/proofs', {'id': 'proof-r3', 'artifact': a3['id']}, status=201)
    call('POST', '/v1/proofs/proof-r3/annotations',
         {'id': 'note-fresh', 'region': {'x': 10, 'y': 10, 'w': 8, 'h': 8}, 'comment': '新稿意见'}, status=201)
    review3c = call('GET', '/v1/plates/amber-plate/annotations?revision=3')
    assert review3c['pending_open_annotations'] == 1
    assert review3c['sources'][0]['projections'][0]['annotation']['id'] == 'note-fresh'
    # Annotations are part of the durable snapshot and survive process recovery.
    sheet_before_stop, _ = call('GET', '/v1/plates/amber-plate/review-sheet?revision=3', raw=True)
    stop(); start()
    after = call('GET', '/v1/plates/amber-plate/annotations?revision=3')
    assert after['pending_open_annotations'] == 1
    kept = after['sources'][0]['projections'][0]['annotation']
    assert kept['comment'] == '新稿意见' and kept['region'] == {'x': 10, 'y': 10, 'w': 8, 'h': 8}
    sheet_restart, _ = call('GET', '/v1/plates/amber-plate/review-sheet?revision=3', raw=True)
    assert sheet_restart[:5] == b'<svg ' and sheet_restart == sheet_before_stop
    # Original rejected proof image remains bit-identical after recovery.
    assert call('GET', '/v1/artifacts/' + a1['id'], raw=True)[0] == raw1

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workflow',required=True);args=parser.parse_args()
    seed=json.loads((ROOT/'examples/amber-proof.json').read_text())
    with tempfile.TemporaryDirectory(prefix='prism-plate-') as tmp:
        base=pathlib.Path(tmp);binary=base/'prismplate';subprocess.run(['go','build','-o',str(binary),'./cmd/prismplate'],cwd=ROOT,check=True)
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close();url=f'http://127.0.0.1:{port}'
        log=(base/'service.log').open('w');proc=None
        def start():
            nonlocal proc
            proc=subprocess.Popen([str(binary),'-listen',f'127.0.0.1:{port}','-data',str(base/'state')],stdout=log,stderr=log)
            for _ in range(100):
                if proc.poll() is not None:raise RuntimeError('service exited')
                try:
                    urllib.request.urlopen(url+'/healthz',timeout=1).close();return
                except OSError:time.sleep(.05)
            raise RuntimeError('startup timed out')
        def stop():
            if proc and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:proc.wait(timeout=45)
                except subprocess.TimeoutExpired:proc.kill();proc.wait();raise
        def call(method,path,data=None,status=200,raw=False,headers=None):
            body=data if isinstance(data,bytes) else (json.dumps(data).encode() if data is not None else None)
            req=urllib.request.Request(url+path,data=body,method=method,headers=headers or {'Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(req,timeout=35) as r:code=r.status;payload=r.read();hs={k.lower():v for k,v in r.headers.items()}
            except urllib.error.HTTPError as e:code=e.code;payload=e.read();hs={k.lower():v for k,v in e.headers.items()}
            if code!=status:raise RuntimeError(f'{method} {path}: expected {status}, observed {code}: {payload[:200]!r}')
            return (payload,hs) if raw else json.loads(payload) if payload else None
        def render(fmt='png',revision=1):return call('POST',f'/v1/plates/amber-plate/render?revision={revision}&format={fmt}',status=201)
        try:
            start();created=call('POST','/v1/plates',seed,status=201);assert created['revisions'][0]['number']==1
            w=args.workflow
            if w=='create':
                assert call('GET','/v1/plates/amber-plate')['title']==seed['title'];call('POST','/v1/plates',seed,status=409)
                bad=copy.deepcopy(seed);bad['id']='bad-plate';bad['document']['layers'][0]['shapes'][0]['transform']=[0]*6;call('POST','/v1/plates',bad,status=400)
            elif w=='revise':
                q={'expected_revision':1,'document':copy.deepcopy(seed['document'])};q['document']['background']['r']=.2
                with concurrent.futures.ThreadPoolExecutor(2) as pool:
                    def attempt(_):
                        try:return call('PUT','/v1/plates/amber-plate',q)
                        except RuntimeError as e:
                            if 'observed 409' in str(e):return 'conflict'
                            raise
                    results=list(pool.map(attempt,range(2)))
                assert sum(x=='conflict' for x in results)==1;assert len(call('GET','/v1/plates/amber-plate')['revisions'])==2
                assert call('GET','/v1/plates/amber-plate')['revisions'][0]['document']==created['revisions'][0]['document']
            elif w=='branch':
                b=call('POST','/v1/plates/amber-plate/branch',{'revision':1,'id':'amber-fork','title':'琥珀新稿'},status=201);assert b['revisions'][0]['digest']==created['revisions'][0]['digest'];call('POST','/v1/plates/amber-plate/branch',{'revision':99,'id':'missing-fork','title':'无效稿'},status=404)
            elif w=='archive':
                call('POST','/v1/plates/amber-plate/archive');call('PUT','/v1/plates/amber-plate',{'expected_revision':1,'document':seed['document']},status=409);assert render()['revision']==1
            elif w in ('png','svg','artifact'):
                fmt='svg' if w=='svg' else 'png';a=render(fmt);raw,hs=call('GET','/v1/artifacts/'+a['id'],raw=True);assert hashlib.sha256(raw).hexdigest()==a['digest']
                if fmt=='png':assert raw[:8]==b'\x89PNG\r\n\x1a\n';assert struct.unpack('>II',raw[16:24])==(192,144)
                else:assert b'<svg' in raw and b'<linearGradient' in raw and b'clipPath' in raw
                assert render(fmt)['digest']==a['digest'];call('GET','/v1/artifacts/'+a['id'],status=304,raw=True,headers={'If-None-Match':hs['etag']});call('GET','/v1/artifacts/missing',status=404)
                if w=='artifact':call('DELETE','/v1/artifacts/'+a['id']);call('GET','/v1/artifacts/'+a['id'],status=409)
            elif w in ('exposure','cancel'):
                job=call('POST','/v1/plates/amber-plate/exposures?revision=1&format=png',status=202)
                if w=='cancel':call('DELETE','/v1/exposures/'+job['id'],status=202)
                for _ in range(200):
                    job=call('GET','/v1/exposures/'+job['id'])
                    if job['state'] in ('succeeded','cancelled','failed'):break
                    time.sleep(.02)
                assert job['state'] in (('succeeded','cancelled') if w=='cancel' else ('succeeded',))
                if job['state']=='succeeded':assert call('GET','/v1/artifacts/'+job['artifact'],raw=True)[0][:8]==b'\x89PNG\r\n\x1a\n'
                call('DELETE','/v1/exposures/absent',status=404)
            elif w == 'proof':
                a=render();call('POST','/v1/proofs',{'id':'proof-amber','artifact':a['id']},status=201);assert call('PUT','/v1/proofs/proof-amber',{'state':'accepted','note':'叠色与镂空检查通过'})['state']=='accepted';call('DELETE','/v1/artifacts/'+a['id'],status=409);call('PUT','/v1/proofs/proof-amber',{'state':'rejected','note':'重复裁决'},status=409)
            elif w == 'annotation':
                run_annotation(call, render, seed, base, start, stop)
            elif w in ('bundle','restore'):
                a=render();sealed,_=call('GET','/v1/plates/amber-plate/bundle',raw=True);payload=json.loads(sealed);assert payload['plate']['revisions'][0]['digest']==a['scene_digest']
                if w=='bundle':
                    call('POST','/v1/bundles',sealed,status=409);payload['digest']='0'*64;call('POST','/v1/bundles',payload,status=400)
                    stop();os.rename(base/'state',base/'original');start();imported=call('POST','/v1/bundles',sealed,status=201);assert imported['revisions'][0]['digest']==a['scene_digest'];assert render()['digest']==a['digest']
                else:
                    stop();start();assert call('GET','/v1/artifacts/'+a['id'],raw=True)[0][:8]==b'\x89PNG\r\n\x1a\n';assert render()['digest']==a['digest']
            elif w=='isolation':
                lockproc=subprocess.run([str(binary),'-listen','127.0.0.1:0','-data',str(base/'state')],capture_output=True,timeout=3);assert lockproc.returncode!=0;assert b'locked' in lockproc.stderr
                call('POST','/v1/plates',b'{"id":"x"} true',status=400)
            elif w=='rejection':
                bad=copy.deepcopy(seed);bad['id']='oversize';bad['document']['width']=513;call('POST','/v1/plates',bad,status=400);call('GET','/v1/plates/oversize',status=404)
                bad=copy.deepcopy(seed);bad['id']='invalid-brush';bad['document']['layers'][0]['shapes'][0]['brush']['stops'][1]['at']=0;call('POST','/v1/plates',bad,status=400);assert render()['revision']==1
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
