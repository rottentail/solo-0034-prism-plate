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
            elif w=='corridor':
                def rect(x0,y0,x1,y1):
                    return [{'kind':'M','points':[{'x':x0,'y':y0}]},{'kind':'L','points':[{'x':x1,'y':y0}]},
                            {'kind':'L','points':[{'x':x1,'y':y1}]},{'kind':'L','points':[{'x':x0,'y':y1}]},{'kind':'Z'}]
                solid={'kind':'solid','color':{'r':.1,'g':.1,'b':.1,'a':1}}
                gapdoc={'width':100,'height':100,'background':{'r':1,'g':1,'b':1,'a':1},'layers':[
                    {'id':'sheet','opacity':1,'blend':'normal','shapes':[
                        {'id':'left','path':rect(10,10,40,90),'fill_rule':'nonzero','brush':solid,'transform':[1,0,0,1,0,0]},
                        {'id':'right','path':rect(60,10,90,90),'fill_rule':'nonzero','brush':solid,'transform':[1,0,0,1,0,0]}]}]}
                call('POST','/v1/plates',{'id':'gap-plate','title':'通道保护板','document':gapdoc},status=201)
                chan={'id':'gap','min_width':10,'centerline':[{'x':50,'y':15},{'x':50,'y':85}]}
                small=[{'shape_id':'left','from':{'x':40,'y':10},'to':{'x':44,'y':10}},
                       {'shape_id':'left','from':{'x':40,'y':90},'to':{'x':44,'y':90}}]
                prop=call('POST','/v1/plates/gap-plate/corridor-proposals',{'revision':1,'moves':small,'channels':[chan]},status=201)
                assert prop['feasible'] is True and prop['boundary_alpha']==1 and len(prop['candidates'])==5
                base=prop['baseline'];assert base['passes'] and abs(base['channels'][0]['min_clearance']-10)<1e-9
                last=prop['candidates'][-1];assert last['alpha']==1 and last['report']['passes']
                assert abs(last['node_deviations'][0]['ratio']-1)<1e-9
                png,hs=call('GET',f"/v1/corridor-proposals/{prop['id']}/previews/4?format=png",raw=True)
                assert png[:8]==b'\x89PNG\r\n\x1a\n' and hashlib.sha256(png).hexdigest()==last['preview_png']['digest']
                svg,_=call('GET',f"/v1/corridor-proposals/{prop['id']}/previews/4?format=svg",raw=True);assert b'<svg' in svg
                assert call('GET','/v1/corridor-proposals/'+prop['id'])['id']==prop['id']
                accepted=call('POST',f"/v1/corridor-proposals/{prop['id']}/accept",{'candidate':4},status=201)
                assert len(accepted['revisions'])==2 and accepted['revisions'][1]['digest']==last['scene_digest']
                committed=call('POST','/v1/plates/gap-plate/render?revision=2&format=png',status=201)
                assert committed['digest']==last['preview_png']['digest']
                # Full drag intent closes the gap: feasible must be false, history unchanged.
                closing=[{'shape_id':'left','from':{'x':40,'y':10},'to':{'x':70,'y':10}},
                         {'shape_id':'left','from':{'x':40,'y':90},'to':{'x':70,'y':90}}]
                blocked=call('POST','/v1/plates/gap-plate/corridor-proposals',{'revision':1,'moves':closing,'channels':[chan]},status=201)
                assert blocked['feasible'] is False and blocked['limit'] is not None and blocked['limit']['passes'] is False
                assert blocked['boundary_alpha']<1 and all(c['report']['passes'] for c in blocked['candidates'])
                limit_ch=blocked['limit']['channels'][0];assert not limit_ch['passes'] and limit_ch['narrowest']['wall'].startswith('layer')
                assert len(call('GET','/v1/plates/gap-plate')['revisions'])==2
                # A boundary candidate is still committable; committed render matches its preview.
                edge=blocked['candidates'][-1];acc2=call('POST',f"/v1/corridor-proposals/{blocked['id']}/accept",{'candidate':4},status=201)
                assert acc2['revisions'][-1]['digest']==edge['scene_digest']
                assert call('POST','/v1/plates/gap-plate/render?revision=3&format=png',status=201)['digest']==edge['preview_png']['digest']
                # Original already violates an over-wide marker: 400, no proposal stored.
                wide=dict(chan,min_width=40)
                call('POST','/v1/plates/gap-plate/corridor-proposals',{'revision':1,'moves':small,'channels':[wide]},status=400)
                # Stale expected revision after a normal revise: acceptance is 409.
                stale=call('POST','/v1/plates/gap-plate/corridor-proposals',{'revision':1,'moves':small,'channels':[chan]},status=201)
                call('PUT','/v1/plates/gap-plate',{'expected_revision':3,'document':gapdoc},status=200)
                call('POST',f"/v1/corridor-proposals/{stale['id']}/accept",{'candidate':0},status=409)
                call('GET','/v1/corridor-proposals/cp-missing',status=404)
                call('POST','/v1/corridor-proposals/cp-missing/accept',{'candidate':0},status=404)
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
