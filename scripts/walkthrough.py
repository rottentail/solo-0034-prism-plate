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
            elif w=='edits':
                # Build a plate with two layers and two shapes so a relocation
                # can leave both layers with at least one shape.
                doc=copy.deepcopy(seed['document'])
                s0=copy.deepcopy(doc['layers'][0]['shapes'][0]);s0['id']='amber-twin';s0['transform']=[1,0,0,1,40,12]
                doc['layers'][0]['shapes'].append(s0)
                plate={'id':'edit-plate','title':'节点编辑校样','document':doc}
                call('POST','/v1/plates',plate,status=201)
                newbrush={'kind':'solid','color':{'r':0.2,'g':0.9,'b':0.4,'a':0.9}}
                ops=[
                    {'type':'move','move':{'shape':'amber-contour','delta_x':12.5,'delta_y':-6}},
                    {'type':'replace_brush','brush':{'shape':'amber-twin','brush':newbrush}},
                    {'type':'relocate','relocate':{'shape':'amber-twin','layer':'cyan-glaze'}},
                ]
                # Previews never persist: revision count stays at one.
                pv,ph=call('POST','/v1/plates/edit-plate/edits/preview?revision=1&format=png',{'format':'png','operations':ops},status=200,raw=True)
                assert pv[:8]==b'\x89PNG\r\n\x1a\n'
                preview_digest=hashlib.sha256(pv).hexdigest();preview_scene=ph['x-scene-digest']
                assert ph['etag']=='"'+preview_digest+'"'
                assert len(call('GET','/v1/plates/edit-plate')['revisions'])==1
                # JSON preview exposes local coordinates, layer clip domains and stacking.
                jpv=call('POST','/v1/plates/edit-plate/edits/preview?revision=1&format=json',{'format':'json','operations':ops},status=200)
                assert jpv['scene_digest']==preview_scene
                cd=jpv['document'];assert len(cd['layers'])==2
                assert len(cd['layers'][0]['shapes'])==1 and cd['layers'][0]['shapes'][0]['id']=='amber-contour'
                assert cd['layers'][0]['shapes'][0]['transform'][4:]==[12.5,-6]
                assert len(cd['layers'][1]['shapes'])==2
                twin=[x for x in cd['layers'][1]['shapes'] if x['id']=='amber-twin'][0]
                assert twin['brush']['kind']=='solid' and twin['brush']['color']==newbrush['color']
                # Relocation moves the shape under the destination layer's screen blend, mask and clip.
                assert [s['id'] for s in cd['layers'][1]['shapes']]==['cyan-window','amber-twin']
                assert 'clip' in cd['layers'][0] and 'mask' in cd['layers'][1]
                # Commit the exact same operation set and prove the final PNG is the preview.
                committed=call('POST','/v1/plates/edit-plate/edits',{'expected_revision':1,'operations':ops},status=201)
                assert len(committed['revisions'])==2
                assert committed['revisions'][1]['digest']==preview_scene
                final=call('POST','/v1/plates/edit-plate/render?revision=2&format=png',status=201)
                assert final['scene_digest']==preview_scene and final['digest']==preview_digest
                fr, _=call('GET','/v1/artifacts/'+final['id'],raw=True)
                assert hashlib.sha256(fr).hexdigest()==preview_digest and fr==pv
                # Stale expected_revision and unknown targets conflict/fail without a third revision.
                call('POST','/v1/plates/edit-plate/edits',{'expected_revision':1,'operations':ops},status=409)
                bad=copy.deepcopy(ops)
                bad[2]={'type':'relocate','relocate':{'shape':'amber-twin','layer':'no-such-layer'}}
                call('POST','/v1/plates/edit-plate/edits',{'expected_revision':2,'operations':bad},status=400)
                # An invalid brush is admitted nowhere: the whole batch is rejected atomically.
                badbrush=copy.deepcopy(newbrush);badbrush['kind']='linear'
                bad=[{'type':'replace_brush','brush':{'shape':'amber-contour','brush':badbrush}}]
                call('POST','/v1/plates/edit-plate/edits',{'expected_revision':2,'operations':bad},status=400)
                call('POST','/v1/plates/edit-plate/edits/preview?revision=2&format=png',{'format':'png','operations':bad},status=400)
                # Empty batches and unknown operation types are contract violations.
                call('POST','/v1/plates/edit-plate/edits',{'expected_revision':2,'operations':[]},status=400)
                call('POST','/v1/plates/edit-plate/edits/preview?revision=2&format=png',{'format':'png','operations':[{'type':'warp'}]},status=400)
                assert len(call('GET','/v1/plates/edit-plate')['revisions'])==2
                # Missing plate is a 404; an archived plate previews but cannot commit.
                call('POST','/v1/plates/ghost-plate/edits',{'expected_revision':1,'operations':ops},status=404)
                call('POST','/v1/plates/edit-plate/archive')
                arch, _=call('POST','/v1/plates/edit-plate/edits/preview?revision=2&format=png',{'format':'png','operations':ops[:1]},status=200,raw=True)
                assert arch[:8]==b'\x89PNG\r\n\x1a\n'
                call('POST','/v1/plates/edit-plate/edits',{'expected_revision':2,'operations':ops[:1]},status=409)
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
