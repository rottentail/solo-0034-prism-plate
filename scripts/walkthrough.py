#!/usr/bin/env python3
"""Run bounded production HTTP journeys against an isolated ephemeral service."""
import argparse, concurrent.futures, copy, hashlib, json, os, pathlib, signal, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
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
        def render(fmt='png',revision=1,plate='amber-plate'):return call('POST',f'/v1/plates/{plate}/render?revision={revision}&format={fmt}',status=201)
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
            elif w=='proof':
                a=render();call('POST','/v1/proofs',{'id':'proof-amber','artifact':a['id']},status=201);assert call('PUT','/v1/proofs/proof-amber',{'state':'accepted','note':'叠色与镂空检查通过'})['state']=='accepted';call('DELETE','/v1/artifacts/'+a['id'],status=409);call('PUT','/v1/proofs/proof-amber',{'state':'rejected','note':'重复裁决'},status=409)
            elif w in ('bundle','restore'):
                a=render();sealed,_=call('GET','/v1/plates/amber-plate/bundle',raw=True);payload=json.loads(sealed);assert payload['plate']['revisions'][0]['digest']==a['scene_digest']
                if w=='bundle':
                    call('POST','/v1/bundles',sealed,status=409);payload['digest']='0'*64;call('POST','/v1/bundles',payload,status=400)
                    stop();os.rename(base/'state',base/'original');start();imported=call('POST','/v1/bundles',sealed,status=201);assert imported['revisions'][0]['digest']==a['scene_digest'];assert render()['digest']==a['digest']
                else:
                    stop();start();assert call('GET','/v1/artifacts/'+a['id'],raw=True)[0][:8]==b'\x89PNG\r\n\x1a\n';assert render()['digest']==a['digest']
            elif w=='anchor':
                anchor_seed=json.loads((ROOT/'examples/anchor-proof.json').read_text())
                call('POST','/v1/plates',anchor_seed,status=201)
                def anchor_call(rev,layer,shape,target,status=201):
                    return call('POST','/v1/plates/anchor-plate/anchor',{'expected_revision':rev,'layer_id':layer,'shape_id':shape,'anchor':target},status=status)
                def brush(rev,shape_id):
                    plate=call('GET','/v1/plates/anchor-plate')
                    doc=plate['revisions'][rev-1]['document']
                    return next(sh['brush'] for l in doc['layers'] for sh in l['shapes'] if sh['id']==shape_id)
                def digest(rev,fmt):
                    return render(fmt,rev,'anchor-plate')['digest']
                # Rev 1: object-anchored baseline. Conversion creates normal revisions.
                png1=digest(1,'png');svg1=digest(1,'svg')
                r2=anchor_call(1,'anchor-sheet','uniform-blade','canvas')
                assert r2['report']['exact_field_preserved'] and r2['report']['naive_endpoint_move_exact']
                assert r2['report']['from_anchor']=='object' and r2['report']['to_anchor']=='canvas'
                assert brush(2,'uniform-blade')['anchor']=='canvas'
                # Appearance at the conversion instant is identical in PNG pixels.
                assert digest(2,'png')==png1
                # SVG keeps the same flattened geometry and gains an inverse gradientTransform.
                svg2=call('GET','/v1/artifacts/'+render('svg',2,'anchor-plate')['id'],raw=True)[0]
                assert b'gradientTransform="matrix(' in svg2 and b'userSpaceOnUse' in svg2
                assert digest(2,'svg')!=svg1
                # Round trip canvas -> object restores the original axis numerically.
                r3=anchor_call(2,'anchor-sheet','uniform-blade','object')
                b0,b3=brush(1,'uniform-blade'),brush(3,'uniform-blade')
                for key in ('from','to'):
                    for axis in ('x','y'):
                        assert abs(b0[key][axis]-b3[key][axis])<1e-9
                assert digest(3,'png')==png1
                # Non-uniform scale: exact field preserved, naive endpoint move would drift.
                r4=anchor_call(3,'anchor-sheet','anisotropic-blade','canvas')
                rep=r4['report']
                assert rep['exact_field_preserved'] and not rep['naive_endpoint_move_exact']
                assert rep['naive_max_t_error']>0.01 and rep['naive_axis_drift_degrees']>1
                assert rep['conformal_ratio']>1+1e-9
                assert digest(4,'png')==png1
                # Pure mirror: direct endpoint move is already exact; reflected flag set.
                r5=anchor_call(4,'anchor-sheet','mirror-blade','canvas')
                rep=r5['report']
                assert rep['transform_reflected'] and rep['naive_endpoint_move_exact']
                assert rep['naive_max_t_error']<=1e-9
                assert digest(5,'png')==png1
                # Reject paths: same anchor, unknown target, missing node, bad expected, solid brush.
                anchor_call(5,'anchor-sheet','mirror-blade','canvas',status=400)
                anchor_call(5,'anchor-sheet','mirror-blade','diagonal',status=400)
                anchor_call(5,'anchor-sheet','no-such-shape','canvas',status=400)
                anchor_call(99,'anchor-sheet','mirror-blade','object',status=404)
                bad=copy.deepcopy(anchor_seed);bad['id']='solid-anchor'
                bad['document']['layers'][0]['shapes']=[{
                    'id':'solid-one','fill_rule':'nonzero',
                    'path':anchor_seed['document']['layers'][0]['shapes'][0]['path'],
                    'transform':[1,0,0,1,0,0],
                    'brush':{'kind':'solid','color':{'r':1,'g':0,'b':0,'a':1}}}]
                call('POST','/v1/plates',bad,status=201)
                call('POST','/v1/plates/solid-anchor/anchor',{'expected_revision':1,'layer_id':'anchor-sheet','shape_id':'solid-one','anchor':'canvas'},status=400)
                # Old documents without an anchor field keep object semantics and render unchanged.
                legacy=copy.deepcopy(seed);legacy['id']='legacy-plate'
                call('POST','/v1/plates',legacy,status=201)
                legacy_png=render('png',1,'legacy-plate')['digest']
                assert 'anchor' not in json.dumps(call('GET','/v1/plates/legacy-plate')['revisions'][0]['document'])
                assert legacy_png==render('png',1,'legacy-plate')['digest']
                # The historical revision remains renderable with its original digest.
                assert digest(1,'png')==png1 and digest(1,'svg')==svg1
                # Anchor documents survive restart: snapshot reload and recompile, same bytes.
                rev5_png=digest(5,'png')
                stop();start()
                assert digest(5,'png')==rev5_png
                sealed=call('GET','/v1/plates/anchor-plate/bundle',raw=True)[0]
                assert b'"anchor":"canvas"' in sealed
                # Envelope import re-admits anchored revisions through the same Compile gate.
                stop();os.rename(base/'state',base/'original');start()
                imported=call('POST','/v1/bundles',sealed,status=201)
                assert len(imported['revisions'])==5
                assert digest(5,'png')==rev5_png
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
