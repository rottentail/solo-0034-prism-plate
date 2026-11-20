#!/usr/bin/env python3
"""Run bounded production HTTP journeys against an isolated ephemeral service."""
import argparse, concurrent.futures, copy, hashlib, json, math, os, pathlib, re, signal, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request
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
            elif w=='proof':
                a=render();call('POST','/v1/proofs',{'id':'proof-amber','artifact':a['id']},status=201);assert call('PUT','/v1/proofs/proof-amber',{'state':'accepted','note':'叠色与镂空检查通过'})['state']=='accepted';call('DELETE','/v1/artifacts/'+a['id'],status=409);call('PUT','/v1/proofs/proof-amber',{'state':'rejected','note':'重复裁决'},status=409)
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
            elif w == 'timeline':
                motion=json.loads((ROOT/'examples/timed-proof.json').read_text())
                call('POST','/v1/plates',motion,status=201)
                def timed(at,fmt='png'):
                    return call('POST',f"/v1/plates/motion-plate/render?revision=1&format={fmt}&time={at}",status=201)
                def svg_at(at):
                    raw,_=call('GET','/v1/artifacts/'+timed(at,'svg')['id'],raw=True)
                    # Only the shape path carries these attributes; the background
                    # rect has its own earlier fill-opacity.
                    m=re.search(rb'<path[^>]*?transform="matrix\(([^"]*?)\)"',raw)
                    vals=[float(x) for x in m.group(1).split()]
                    op=re.search(rb'<path[^>]*?fill-opacity="([^"]*)"',raw)
                    return vals,float(op.group(1))
                # 1) Domain and format rejection paths.
                call('POST','/v1/plates/motion-plate/render?revision=1&format=png',status=400)
                call('POST','/v1/plates/motion-plate/render?revision=1&format=png&time=-1',status=400)
                call('POST','/v1/plates/motion-plate/render?revision=1&format=png&time=4.01',status=400)
                call('POST','/v1/plates/motion-plate/render?revision=1&format=png&time=soon',status=400)
                call('POST','/v1/plates/amber-plate/render?revision=1&format=png&time=1',status=400)
                bad=copy.deepcopy(motion);bad['id']='bad-motion';bad['document']['timeline']['tracks'][0]['target']='ghost'
                call('POST','/v1/plates',bad,status=400)
                bad=copy.deepcopy(motion);bad['id']='bad-motion2';bad['document']['timeline']['duration']=0
                call('POST','/v1/plates',bad,status=400)
                bad=copy.deepcopy(motion);bad['id']='bad-motion3';bad['document']['timeline']['tracks'][1]['keyframes'][0]['rotation']=1e9
                call('POST','/v1/plates',bad,status=400)
                bad=copy.deepcopy(motion);bad['id']='bad-motion4';bad['document']['timeline']['tracks'][3]['keyframes'][0]['opacity']=2
                call('POST','/v1/plates',bad,status=400)
                # 2) Endpoint keyframes are sampled exactly; intermediate frames linearly.
                a0=timed(0);a4=timed(4);amid=timed(2)
                assert a0['time']==0 and a4['time']==4 and amid['time']==2
                assert a0['timeline_digest']==a4['timeline_digest']==amid['timeline_digest']
                assert timed(0)['digest']==a0['digest']
                raw,hs=call('GET','/v1/artifacts/'+a0['id'],raw=True)
                assert raw[:8]==b'\x89PNG\r\n\x1a\n' and struct.unpack('>II',raw[16:24])==(96,96)
                assert hs['x-sample-time']=='0' and hs['x-timeline-digest']==a0['timeline_digest']
                # SVG matrix() is column-major: (m0,m1,m2,m3,m4,m5).
                vals,op=svg_at(0)
                # %.9g export keeps ~1e-9 relative precision at unit magnitude.
                eps=2e-9
                assert len(vals)==6 and abs(vals[0]-math.cos(math.radians(350)))<eps and abs(vals[4]-10)<eps and op==0
                vals,op=svg_at(4)
                assert abs(vals[0]-math.cos(math.radians(390)))<eps and abs(vals[4]-10)<eps and op==1
                vals,op=svg_at(0.5)
                # t=0.5 on each channel's own keyframes: rotation 355deg,
                # scale_x = 1 + (-1-1)*0.25 = 0.5, position (17.5,15), opacity 0.5.
                assert abs(vals[0]-0.5*math.cos(math.radians(355)))<eps
                assert abs(vals[1]-0.5*math.sin(math.radians(355)))<eps
                assert abs(vals[4]-17.5)<eps and abs(vals[5]-15)<eps and abs(op-0.5)<eps
                # 3) Rotation crosses the 360 boundary with a continuous sweep, never
                # an unexplained backward snap. Every non-collapse sample must match
                # the analytic piecewise-linear TRS expectation within SVG export
                # precision; the only sanctioned discontinuity is the exact collapse
                # instant t=1 (checked below). This is also the continuous-sampling
                # error statement: interpolation is exact, export error stays at the
                # fixed %.9g numeric precision, there is no unmodelled time jump.
                for k in range(41):
                    at=round(2*k/40,6)
                    if at == 1.0:
                        continue
                    rot=350+10*at
                    sx=1-at
                    sy=1.0
                    px=10+15*at if at<=2 else 40-15*(at-2)
                    py=10+10*at if at<=2 else 30-10*(at-2)
                    exp=[sx*math.cos(math.radians(rot)),sx*math.sin(math.radians(rot)),
                         -sy*math.sin(math.radians(rot)),sy*math.cos(math.radians(rot)),px,py]
                    vals,_=svg_at(at)
                    for u,v in zip(exp,vals):
                        assert abs(v-u) <= 1e-8+1e-9*max(1,abs(u)), f'matrix drift at t={at}'
                # On either side of the boundary the rotation keeps growing: at t=0
                # it is 350deg and at t=2 it is 370deg, i.e. a +20deg sweep, not a
                # -340deg rewind through the 360 wrap. At t=2 scale_x is -1 so the
                # first column is the rotated basis mirrored in x.
                v0,_=svg_at(0);v2,_=svg_at(2)
                assert abs(v0[0]-math.cos(math.radians(350)))<eps
                assert abs(v2[1]-(-1)*math.sin(math.radians(370)))<eps                # 4) Mirror sweep through scale_x=0: the collapse instant is the
                # documented zero-alpha fallback, neighbours stay mirrored and visible.
                vals,op=svg_at(1)
                assert vals==[1,0,0,1,0,0] and op==0
                vals,op=svg_at(1.01)
                assert vals[0] < 0 and op==1
                vals,op=svg_at(0.99)
                assert vals[0] > 0 and abs(op-0.99)<eps                # 5) Fade-in is continuous: alpha rises monotonically over dense
                # samples up to the collapse instant, then stays fully present.
                ops=[]
                for k in range(10):
                    _,o=svg_at(round(k/10,6))
                    ops.append(o)
                assert ops[0]==0 and all(b>=a-1e-12 for a,b in zip(ops,ops[1:])) and abs(ops[-1]-0.9)<eps
                _,op_after=svg_at(1.5)
                assert op_after==1
                # The collapse instant itself is forced transparent by definition.
                _,op_collapse=svg_at(1)
                assert op_collapse==0
                # 6) Async exposures lock revision, time and animation source at submit.
                job=call('POST','/v1/plates/motion-plate/exposures?revision=1&format=png&time=2',status=202)
                assert job['time']==2 and job['timeline_digest']==amid['timeline_digest'] and job['scene_digest']
                for _ in range(200):
                    job=call('GET','/v1/exposures/'+job['id'])
                    if job['state'] in ('succeeded','cancelled','failed'):break
                    time.sleep(.02)
                assert job['state']=='succeeded' and call('GET','/v1/artifacts/'+job['artifact'],status=200,raw=True)[0][:8]==b'\x89PNG\r\n\x1a\n'
                done=call('GET','/v1/artifacts/'+job['artifact'],raw=True)[0]
                assert hashlib.sha256(done).hexdigest()==amid['digest']
                revised=copy.deepcopy(motion['document']);revised['background']['r']=.3
                call('PUT','/v1/plates/motion-plate',{'expected_revision':1,'document':revised},status=200)
                # A later revision must not change the already-captured timed artifact.
                assert call('GET','/v1/artifacts/'+amid['id'],raw=True)[0]==done
                # 6b) A plain restart re-runs snapshot lineage (including timed
                # artifact fields) and recompiles the timeline document.
                stop();start()
                restarted=call('GET','/v1/artifacts/'+amid['id'],raw=True)[0]
                assert hashlib.sha256(restarted).hexdigest()==amid['digest']
                assert timed(2)['digest']==amid['digest']
                # 7) The portable time description survives bundle export and import.
                sealed,_=call('GET','/v1/plates/motion-plate/bundle',raw=True)
                payload=json.loads(sealed)
                assert payload['plate']['revisions'][0]['document']['timeline']['duration']==4
                stop();os.rename(base/'state',base/'original');start()
                call('POST','/v1/bundles',sealed,status=201)
                assert timed(2)['digest']==amid['digest']
                # 8) Continuous sampling error: interpolation is exact piecewise linear,
                # so the only raster error is the fixed four-sample pixel grid; dense
                # neighbouring samples never jump between keyframe endpoint values.
                digests=[timed(round(0.2*k,6))['digest'] for k in range(21)]
                assert digests[10]==amid['digest'] and len(set(digests))>1
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
