#!/usr/bin/env python3
"""Run bounded production HTTP journeys against an isolated ephemeral service."""
import argparse, concurrent.futures, copy, hashlib, json, os, pathlib, signal, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workflow',required=True);args=parser.parse_args()
    seed=json.loads((ROOT/'examples/amber-proof.json').read_text())
    tseed=json.loads((ROOT/'examples/badge-template.json').read_text())
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
            elif w=='template':
                t=call('POST','/v1/templates',tseed,status=201);assert t['revisions'][0]['number']==1
                call('POST','/v1/templates',tseed,status=409)
                bad=copy.deepcopy(tseed);bad['id']='badge-bad-term';bad['definition']['constraints'][0]['terms']={'nope':1};call('POST','/v1/templates',bad,status=400)
                bad=copy.deepcopy(tseed);bad['id']='badge-bad-kind';bad['definition']['params'][1]['kind']='number';call('POST','/v1/templates',bad,status=400)
                bad=copy.deepcopy(tseed);bad['id']='badge-bad-canvas';bad['definition']['width']=513;call('POST','/v1/templates',bad,status=400)
                rev=copy.deepcopy(tseed['definition']);rev['edge']={'r':0.9,'g':0.2,'b':0.2,'a':1}
                call('PUT','/v1/templates/badge-standard',{'expected_revision':2,'definition':rev},status=409)
                t=call('PUT','/v1/templates/badge-standard',{'expected_revision':1,'definition':rev});assert t['revisions'][1]['number']==2
                got=call('GET','/v1/templates/badge-standard');assert got['revisions'][0]['definition']==tseed['definition']
                call('POST','/v1/templates/badge-standard/archive');call('PUT','/v1/templates/badge-standard',{'expected_revision':2,'definition':rev},status=409)
                call('GET','/v1/templates/missing',status=404)
            elif w=='batch':
                t=call('POST','/v1/templates',tseed,status=201)
                body=json.loads((ROOT/'examples/badge-batch.json').read_text())
                call('POST','/v1/templates/badge-standard/batches',body,status=400)
                call('POST','/v1/templates/badge-standard/batches?revision=9',body,status=404)
                call('POST','/v1/templates/missing/batches?revision=1',body,status=404)
                b=call('POST','/v1/templates/badge-standard/batches?revision=1',body,status=201)
                by={i['label']:i for i in b['items']}
                assert by['compact']['state']=='resolved' and by['wide-ring']['state']=='resolved'
                assert by['overflow']['state']=='rejected' and 'canvas-fit' in by['overflow']['reason']
                assert by['hairline']['state']=='rejected' and 'ring-floor' in by['hairline']['reason']
                assert by['domain']['state']=='rejected' and 'outside' in by['domain']['reason']
                assert by['stray']['state']=='rejected' and 'unknown parameter' in by['stray']['reason']
                call('POST','/v1/templates/badge-standard/batches?revision=1',body,status=409)
                plate_id=by['compact']['plate']
                p=call('GET','/v1/plates/'+plate_id)
                assert p['origin']['template']=='badge-standard' and p['origin']['revision']==1 and p['origin']['params']==body['items'][0]['params']
                a=call('POST',f'/v1/plates/{plate_id}/render?revision=1&format=png',status=201)
                raw,_=call('GET','/v1/artifacts/'+a['id'],raw=True);assert raw[:8]==b'\x89PNG\r\n\x1a\n'
                call('POST','/v1/proofs',{'id':'proof-compact','artifact':a['id']},status=201)
                assert call('PUT','/v1/proofs/proof-compact',{'state':'accepted','note':'边距与色阶通过'})['state']=='accepted'
                call('GET','/v1/plates/'+body['id']+'-overflow',status=404)
                got=call('GET','/v1/batches/'+body['id']);assert got['template_digest']==t['revisions'][0]['digest']
                rev=copy.deepcopy(tseed['definition']);rev['fill_to']={'r':0.1,'g':0.9,'b':0.4,'a':1}
                call('PUT','/v1/templates/badge-standard',{'expected_revision':1,'definition':rev})
                body2=copy.deepcopy(body);body2['id']='badge-batch-002';body2['items']=[body['items'][0]]
                b2=call('POST','/v1/templates/badge-standard/batches?revision=1',body2,status=201)
                assert b2['items'][0]['digest']==by['compact']['digest']
                b3=call('POST','/v1/templates/badge-standard/batches?revision=2',{**body2,'id':'badge-batch-003'},status=201)
                assert b3['items'][0]['digest']!=by['compact']['digest']
                dup=copy.deepcopy(body2);dup['id']='badge-batch-004';dup['items']=[body['items'][0],body['items'][0]]
                call('POST','/v1/templates/badge-standard/batches?revision=1',dup,status=400)
                call('POST','/v1/templates/badge-standard/archive')
                b4=call('POST','/v1/templates/badge-standard/batches?revision=1',{**body2,'id':'badge-batch-005'},status=201)
                assert b4['items'][0]['state']=='resolved'
                stop();start()
                again=call('GET','/v1/batches/'+body['id']);assert again['items'][0]['digest']==by['compact']['digest']
                assert call('POST',f'/v1/plates/{plate_id}/render?revision=1&format=png',status=201)['digest']==a['digest']
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
