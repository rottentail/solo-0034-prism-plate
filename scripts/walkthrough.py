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
            elif w=='reframe':
                def near(a,b):return abs(a-b)<1e-6
                # A landscape 192x144 source becomes a 160x160 square. Preview
                # first; nothing is appended until the reviewed digest is given.
                body={'revision':1,'width':160,'height':160,'mode':'fit'}
                fit=call('POST','/v1/plates/amber-plate/reframe',body)
                assert fit['source']=={'width':192,'height':144} and fit['target']=={'width':160,'height':160}
                assert near(fit['placement']['scale'],160/192),fit['placement']
                assert near(fit['margins']['top'],20) and near(fit['margins']['bottom'],20),fit['margins']
                assert fit['document']['width']==160 and fit['document']['height']==160
                assert len(call('GET','/v1/plates/amber-plate')['revisions'])==1,'preview must not append'
                assert {s['shape'] for s in fit['shapes']}=={'amber-contour','cyan-window'}
                assert all(s['effect']=='intact' for s in fit['shapes']),fit['shapes']
                # Clip/mask live in canvas coordinates and must move with it.
                clip=fit['document']['layers'][0]['clip']
                assert near(clip[1]['points'][0]['x'],160-160/192*8) and near(clip[1]['points'][0]['y'],20+160/192*8),clip
                # The shape transform absorbs the canvas affine while the local
                # path stays untouched and the brush stays shape-local.
                transformed=fit['document']['layers'][1]['shapes'][0]
                assert transformed['path']==seed['document']['layers'][1]['shapes'][0]['path']
                assert near(transformed['transform'][0],160/192) and near(transformed['transform'][5],20-160/192*4),transformed['transform']
                assert transformed['brush']['kind']=='solid' and transformed['brush']['color']==seed['document']['layers'][1]['shapes'][0]['brush']['color']
                # A missing or stale digest cannot commit an unreviewed result.
                commit={'revision':1,'expected_revision':1,'width':160,'height':160,'mode':'fit','commit':True}
                call('POST','/v1/plates/amber-plate/reframe',commit,status=409)
                commit['preview_digest']='0'*64
                call('POST','/v1/plates/amber-plate/reframe',commit,status=409)
                commit['preview_digest']=fit['digest']
                p=call('POST','/v1/plates/amber-plate/reframe',commit)
                assert len(p['revisions'])==2
                origin=p['revisions'][1]['origin']
                assert origin['kind']=='reframe' and origin['source_revision']==1 and origin['mode']=='fit',origin
                assert p['revisions'][1]['digest']==fit['digest']
                # The committed revision renders as a square; the landscape
                # source still renders at its original size.
                a=render('png',revision=2);raw,_=call('GET','/v1/artifacts/'+a['id'],raw=True)
                assert struct.unpack('>II',raw[16:24])==(160,160)
                old=render('png',revision=1);raw,_=call('GET','/v1/artifacts/'+old['id'],raw=True)
                assert struct.unpack('>II',raw[16:24])==(192,144)
                assert call('GET','/v1/plates/amber-plate')['revisions'][0]['document']==created['revisions'][0]['document']
                # Crop trims the contour; the preview reports it.
                crop=call('POST','/v1/plates/amber-plate/reframe',{'revision':1,'width':160,'height':160,'mode':'crop'})
                assert near(crop['placement']['scale'],160/144),crop['placement']
                assert crop['detail']['cropped_shapes']==['amber-contour'],crop['detail']
                cc={'revision':1,'expected_revision':2,'width':160,'height':160,'mode':'crop','preview_digest':crop['digest'],'commit':True}
                p=call('POST','/v1/plates/amber-plate/reframe',cc)
                assert p['revisions'][2]['origin']['mode']=='crop'
                # Preserve is 1:1 with margins on a larger canvas, and trims on
                # a smaller one; neither touches the source revision.
                keep=call('POST','/v1/plates/amber-plate/reframe',{'revision':1,'width':200,'height':180,'mode':'preserve'})
                assert keep['placement']['scale']==1 and keep['margins']['left']==4 and keep['margins']['top']==18,keep['margins']
                trim=call('POST','/v1/plates/amber-plate/reframe',{'revision':1,'width':100,'height':100,'mode':'preserve'})
                assert trim['placement']['scale']==1 and trim['placement']['offset_x']==-46 and trim['placement']['offset_y']==-22,trim['placement']
                assert len(call('GET','/v1/plates/amber-plate')['revisions'])==3
                # The original landscape proof is accepted; the signed-off
                # artifact is untouched by every later reframe, and the reframe
                # never shares its identity.
                call('POST','/v1/proofs',{'id':'proof-landscape','artifact':old['id']},status=201)
                accepted=call('PUT','/v1/proofs/proof-landscape',{'state':'accepted','note':'横版已签样'})
                assert accepted['state']=='accepted'
                call('DELETE','/v1/artifacts/'+old['id'],status=409)
                assert call('GET','/v1/artifacts/'+old['id'],status=304,raw=True,headers={'If-None-Match':'"'+old['digest']+'"'})[1].get('etag')=='"'+old['digest']+'"'
                # Restart: digests, origin metadata and signed-off artifact all
                # reproduce from the persisted snapshot alone.
                stop();start()
                after=call('GET','/v1/plates/amber-plate')
                assert after['revisions'][1]['origin']==origin
                assert after['revisions'][2]['digest']==crop['digest']
                again=render('png',revision=2)
                assert again['digest']==a['digest'],'reframed output must be reproducible after restart'
                assert call('GET','/v1/artifacts/'+old['id'],raw=True)[0][:8]==b'\x89PNG\r\n\x1a\n'
                # The sealed bundle carries origin provenance and re-imports
                # into an empty instance with identical digests.
                sealed,_=call('GET','/v1/plates/amber-plate/bundle',raw=True)
                call('POST','/v1/bundles',sealed,status=409)
                payload=json.loads(sealed)
                assert payload['plate']['revisions'][1]['origin']['mode']=='fit'
                stop();os.rename(base/'state',base/'original');start()
                imported=call('POST','/v1/bundles',sealed,status=201)
                assert imported['revisions'][2]['digest']==crop['digest']
                assert imported['revisions'][1]['origin']==origin
                assert render('png',revision=2)['digest']==a['digest'],'bundle import must reproduce the reframed proof'
                # A detail fully outside the target is reported as removed and
                # cannot be committed without acknowledgement.
                edge={'id':'edge-plate','title':'边缘细节','document':{
                    'width':400,'height':144,'background':{'r':0,'g':0,'b':0,'a':1},
                    'layers':[{'id':'edge-sheet','opacity':1,'blend':'normal','shapes':[{
                        'id':'far-strip','fill_rule':'nonzero',
                        'path':[{'kind':'M','points':[{'x':320,'y':10}]},{'kind':'L','points':[{'x':390,'y':10}]},
                                {'kind':'L','points':[{'x':390,'y':60}]},{'kind':'L','points':[{'x':320,'y':60}]},{'kind':'Z'}],
                        'transform':[1,0,0,1,0,0],
                        'brush':{'kind':'solid','color':{'r':1,'g':1,'b':0,'a':1}}}]}]}}
                call('POST','/v1/plates',edge,status=201)
                # 400 -> 120 at 1:1 is offset -140, so 320..390 maps to 180..250.
                gone=call('POST','/v1/plates/edge-plate/reframe',{'revision':1,'width':120,'height':120,'mode':'preserve'})
                assert gone['detail']['removed_shapes']==['far-strip'],gone['detail']
                assert gone['shapes'][0]['visible'] is False
                gc={'revision':1,'expected_revision':1,'width':120,'height':120,'mode':'preserve','preview_digest':gone['digest'],'commit':True}
                call('POST','/v1/plates/edge-plate/reframe',gc,status=409)
                gc['acknowledge_removed']=True
                p=call('POST','/v1/plates/edge-plate/reframe',gc)
                assert p['revisions'][1]['origin']['mode']=='preserve'
                assert len(call('GET','/v1/plates/edge-plate')['revisions'])==2
                # Rejections: unknown mode, oversize canvas, missing revision.
                call('POST','/v1/plates/amber-plate/reframe',{'revision':1,'width':160,'height':160,'mode':'stretch'},status=400)
                call('POST','/v1/plates/amber-plate/reframe',{'revision':1,'width':513,'height':160,'mode':'fit'},status=400)
                call('POST','/v1/plates/amber-plate/reframe',{'revision':9,'width':160,'height':160,'mode':'fit'},status=404)
                # Rejections: unknown mode, oversize canvas, missing revision.
                call('POST','/v1/plates/amber-plate/reframe',{'revision':1,'width':160,'height':160,'mode':'stretch'},status=400)
                call('POST','/v1/plates/amber-plate/reframe',{'revision':1,'width':513,'height':160,'mode':'fit'},status=400)
                call('POST','/v1/plates/amber-plate/reframe',{'revision':9,'width':160,'height':160,'mode':'fit'},status=404)
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
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
