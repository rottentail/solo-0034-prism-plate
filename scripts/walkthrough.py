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
            elif w=='register':
                def apply(m,p):return(m[0]*p[0]+m[2]*p[1]+m[4],m[1]*p[0]+m[3]*p[1]+m[5])
                def mul(a,b):return[a[0]*b[0]+a[2]*b[1],a[1]*b[0]+a[3]*b[1],a[0]*b[2]+a[2]*b[3],a[1]*b[2]+a[3]*b[3],a[0]*b[4]+a[2]*b[5]+a[4],a[1]*b[4]+a[3]*b[5]+a[5]]
                def ops(verts):return[{'kind':'M','points':[{'x':verts[0][0],'y':verts[0][1]}]}]+[{'kind':'L','points':[{'x':x,'y':y}]} for x,y in verts[1:]]+[{'kind':'Z'}]
                def verts(o):return[(op['points'][0]['x'],op['points'][0]['y']) for op in o if op['kind']!='Z']
                foreign={'id':'handoff-plate','title':'外来移交稿','document':{'width':120,'height':90,'background':{'r':.95,'g':.9,'b':.8,'a':1},'layers':[{'id':'ink','opacity':.9,'blend':'multiply','clip':ops([(2,2),(118,2),(118,88),(2,88)]),'mask':ops([(4,4),(116,4),(116,86),(4,86)]),'shapes':[{'id':'glyph','fill_rule':'nonzero','path':ops([(10,10),(50,14),(44,60),(12,52)]),'transform':[.5,0,0,.5,3,-2],'brush':{'kind':'linear','from':{'x':0,'y':0},'to':{'x':40,'y':30},'color':{'r':0,'g':0,'b':0,'a':0},'stops':[{'at':0,'color':{'r':.2,'g':.3,'b':.9,'a':.9}},{'at':1,'color':{'r':.8,'g':.2,'b':.5,'a':.6}}]}}]}]}}
                call('POST','/v1/plates',foreign,status=201)
                sealed,_=call('GET','/v1/plates/handoff-plate/bundle',raw=True);pack=json.loads(sealed)
                mtrue=[0,1,-1,0,150,10];src=[(0,0),(120,0),(120,90),(0,90),(60,45)]
                noise=[(.2,-.2),(-.3,.4),(.3,-.4),(-.2,.2),(.1,-.1)]
                pairs=[{'source':{'x':p[0],'y':p[1]},'target':{'x':apply(mtrue,p)[0]+d[0],'y':apply(mtrue,p)[1]+d[1]}} for p,d in zip(src,noise)]
                prev=call('POST','/v1/plates/amber-plate/registration/preview',{'bundle':pack,'pairs':pairs})
                fit=prev['fit']
                assert prev['base_revision']==1 and 'committed' not in prev and not fit['reflected']
                assert abs(fit['scale']-1)<.01 and abs(fit['rotation_degrees']-90)<.5 and fit['rms_error']<1
                assert fit['confidence']=='high' and fit['degrees_of_freedom']==6 and len(fit['residuals'])==5
                doc=prev['document']
                assert doc['width']==192 and doc['height']==144 and doc['background']==seed['document']['background']
                assert [l['id'] for l in doc['layers']]==['amber-sheet','cyan-glaze','handoff-plate-ink']
                fl=doc['layers'][2];fL=foreign['document']['layers'][0]
                assert fl['opacity']==.9 and fl['blend']=='multiply'
                for key in ('clip','mask'):
                    for p,q in zip(verts(fL[key]),verts(fl[key])):
                        e=apply(fit['transform'],p);assert abs(e[0]-q[0])<1e-6 and abs(e[1]-q[1])<1e-6
                fsh,gsh=fL['shapes'][0],fl['shapes'][0]
                assert gsh['id']=='handoff-plate-glyph' and gsh['path']==fsh['path'] and gsh['brush']==fsh['brush']
                for a,b in zip(mul(fit['transform'],fsh['transform']),gsh['transform']):assert abs(a-b)<1e-9
                assert prev['foreign']['background']['disposition'].startswith('discarded') and prev['foreign']['background']['r']==.95
                assert prev['foreign']['layers_placed']==[{'source':'ink','placed':'handoff-plate-ink'}]
                again=call('POST','/v1/plates/amber-plate/registration/preview',{'bundle':pack,'pairs':pairs})
                assert again['document_digest']==prev['document_digest']
                call('POST','/v1/plates/amber-plate/registration/commit',{'expected_revision':7,'bundle':pack,'pairs':pairs},status=409)
                done=call('POST','/v1/plates/amber-plate/registration/commit',{'expected_revision':1,'bundle':pack,'pairs':pairs},status=201)
                assert done['committed']==2 and done['document_digest']==prev['document_digest'] and done['document']==doc
                plate=call('GET','/v1/plates/amber-plate')
                assert len(plate['revisions'])==2 and plate['revisions'][0]['document']==created['revisions'][0]['document'] and plate['revisions'][1]['digest']==prev['document_digest']
                a=render('png',2);raw,_=call('GET','/v1/artifacts/'+a['id'],raw=True);assert raw[:8]==b'\x89PNG\r\n\x1a\n'
                assert render('png',2)['digest']==a['digest'] and render('svg',2)['revision']==2
                assert len(call('GET','/v1/plates/handoff-plate')['revisions'])==1
                call('POST','/v1/plates/amber-plate/registration/preview',{'bundle':pack,'pairs':pairs[:2]},status=400)
                tampered=dict(pack);tampered['digest']='0'*64
                call('POST','/v1/plates/amber-plate/registration/preview',{'bundle':tampered,'pairs':pairs},status=400)
                call('POST','/v1/plates/amber-plate/registration/preview',{'bundle':pack,'pairs':pairs,'source_revision':99},status=400)
                call('POST','/v1/plates/missing-plate/registration/preview',{'bundle':pack,'pairs':pairs},status=404)
                collinear=[{'source':{'x':x,'y':10},'target':{'x':150-x,'y':10}} for x in (10,20,30)]
                col=call('POST','/v1/plates/amber-plate/registration/preview',{'bundle':pack,'pairs':collinear})
                assert col['fit']['confidence'] in ('medium','low') and 'collinear' in json.dumps(col['fit']['diagnostics'])
                mirror={'id':'cyan','title':'镜像稿','document':{'width':120,'height':90,'background':{'r':1,'g':1,'b':1,'a':1},'layers':[{'id':'glaze','opacity':1,'blend':'normal','shapes':[{'id':'mark','fill_rule':'nonzero','path':ops([(5,5),(60,8),(30,70)]),'transform':[1,0,0,1,0,0],'brush':{'kind':'solid','color':{'r':.4,'g':.2,'b':.6,'a':.8}}}]}]}}
                call('POST','/v1/plates',mirror,status=201)
                msealed,_=call('GET','/v1/plates/cyan/bundle',raw=True);mpack=json.loads(msealed)
                mm=[-1,0,0,1,150,10]
                mpairs=[{'source':{'x':p[0],'y':p[1]},'target':{'x':apply(mm,p)[0],'y':apply(mm,p)[1]}} for p in src]
                no=call('POST','/v1/plates/amber-plate/registration/preview',{'bundle':mpack,'pairs':mpairs})
                assert no['fit']['confidence']=='low' and 'reflection' in json.dumps(no['fit']['diagnostics'])
                yes=call('POST','/v1/plates/amber-plate/registration/preview',{'bundle':mpack,'pairs':mpairs,'allow_reflection':True})
                assert yes['fit']['reflected'] and yes['fit']['rms_error']<.5 and yes['document']['layers'][3]['id']=='cyan-glaze-2'
                call('POST','/v1/plates/amber-plate/archive')
                call('POST','/v1/plates/amber-plate/registration/commit',{'expected_revision':2,'bundle':pack,'pairs':pairs},status=409)
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
