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
                r=call('POST','/v1/plates/amber-plate/branch',{'revision':1,'id':'amber-fork','title':'琥珀新稿'},status=201)
                b=r['plate'];assert b['revisions'][0]['digest']==created['revisions'][0]['digest']
                assert b['provenance']['plate_id']=='amber-plate' and b['provenance']['placement']=='original'
                assert r['preview']['document']==b['revisions'][0]['document']
                call('POST','/v1/plates/amber-plate/branch',{'revision':99,'id':'missing-fork','title':'无效稿'},status=404)
            elif w=='extract':
                # Read-only preview first: nothing is created and the source stays frozen.
                plan={'revision':1,'placement':'original','selection':[{'layer':'cyan-glaze','shape':'cyan-window'}],
                      'canvas':{'width':160,'height':120,'background':{'r':1,'g':1,'b':1,'a':1}}}
                pv=call('POST','/v1/plates/amber-plate/branch/preview',plan,status=200)
                assert pv['document']['width']==160 and pv['document']['height']==120
                assert [l['id'] for l in pv['document']['layers']]==['cyan-glaze']
                assert [s['id'] for s in pv['document']['layers'][0]['shapes']]==['cyan-window']
                assert pv['provenance']['scene_digest']==created['revisions'][0]['digest']
                assert pv['provenance']['selection']==plan['selection'] and pv.get('transform') is None
                codes={n['code'] for n in pv['notices']}
                assert {'partial-layer','blend-context','opacity-context','background-changed','canvas-crop'} <= codes
                assert call('GET','/v1/plates/cyan-extract',status=404)['error']
                # Commit the same plan; the actual new plate matches the preview exactly.
                committed=call('POST','/v1/plates/amber-plate/branch',dict(plan,id='cyan-extract',title='青色单形抽出'),status=201)
                plate=committed['plate'];assert plate['revisions'][0]['document']==pv['document']
                pp={k:v for k,v in plate['provenance'].items() if k!='created'}
                vp={k:v for k,v in pv['provenance'].items() if k!='created'}
                assert pp==vp and plate['provenance']['created']
                assert committed['preview']['notices']==pv['notices']
                # Real PNG of the extracted plate: signature, size and stable digest.
                a=call('POST','/v1/plates/cyan-extract/render?revision=1&format=png',status=201)
                raw,hs=call('GET','/v1/artifacts/'+a['id'],raw=True)
                assert raw[:8]==b'\x89PNG\r\n\x1a\n' and struct.unpack('>II',raw[16:24])==(160,120)
                assert hashlib.sha256(raw).hexdigest()==a['digest']
                assert call('POST','/v1/plates/cyan-extract/render?revision=1&format=png',status=201)['digest']==a['digest']
                # Fit placement scales and centers the selection onto the new canvas.
                fit_plan={'revision':1,'placement':'fit','selection':[{'layer':'amber-sheet'}],'canvas':{'width':96,'height':96}}
                fit=call('POST','/v1/plates/amber-plate/branch/preview',fit_plan,status=200)
                assert fit['document']['width']==fit['document']['height']==96
                assert fit['transform'] is not None and fit['transform'][0]==fit['transform'][3]
                assert any(n['code']=='fit-scaled' for n in fit['notices'])
                clip=fit['document']['layers'][0]['clip']
                s_,tx,ty=fit['transform'][0],fit['transform'][4],fit['transform'][5]
                assert clip[0]['points'][0]=={'x':s_*8+tx,'y':s_*8+ty}
                call('POST','/v1/plates/amber-plate/branch',dict(fit_plan,id='amber-fit',title='琥珀贴合版'),status=201)
                fit_png=call('POST','/v1/plates/amber-fit/render?revision=1&format=png',status=201)
                fit_raw,_=call('GET','/v1/artifacts/'+fit_png['id'],raw=True)
                assert fit_raw[:8]==b'\x89PNG\r\n\x1a\n' and struct.unpack('>II',fit_raw[16:24])==(96,96)
                # Whole-layer plus one shape from the same layer is contradictory.
                call('POST','/v1/plates/amber-plate/branch/preview',
                     {'revision':1,'selection':[{'layer':'amber-sheet'},{'layer':'amber-sheet','shape':'amber-contour'}]},status=400)
                call('POST','/v1/plates/amber-plate/branch/preview',{'revision':1,'selection':[{'layer':'ghost'}]},status=400)
                call('POST','/v1/plates/amber-plate/branch/preview',{'revision':1,'placement':'snap'},status=400)
                call('POST','/v1/plates/amber-plate/branch',{'revision':1,'id':'cyan-extract','title':'重复目标'},status=409)
                # Original plate and its history are untouched and still render identically.
                src_now=call('GET','/v1/plates/amber-plate')
                assert src_now==created and len(src_now['revisions'])==1
                src_png=call('POST','/v1/plates/amber-plate/render?revision=1&format=png',status=201)
                src_raw,_=call('GET','/v1/artifacts/'+src_png['id'],raw=True)
                assert struct.unpack('>II',src_raw[16:24])==(192,144)
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
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
