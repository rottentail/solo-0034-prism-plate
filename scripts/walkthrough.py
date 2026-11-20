#!/usr/bin/env python3
"""Run bounded production HTTP journeys against an isolated ephemeral service."""
import argparse, concurrent.futures, copy, hashlib, json, os, pathlib, signal, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
def cubic_ring(ox,oy,k):
    """One closed contour of k cubic verbs; every vertex stays near (ox, oy)."""
    ops=[{'kind':'M','points':[{'x':ox,'y':oy}]}]
    for i in range(k):
        ops.append({'kind':'C','points':[
            {'x':ox+5,'y':oy+7},{'x':ox+9,'y':oy+3},
            {'x':ox+(i%7),'y':oy+2}]})
    ops.append({'kind':'L','points':[{'x':ox,'y':oy}]})
    ops.append({'kind':'Z'})
    return ops
def hidden_shape(sid,ox,oy,k=100):
    return {'id':sid,'path':cubic_ring(ox,oy,k),'fill_rule':'nonzero',
            'transform':[1,0,0,1,0,0],
            'brush':{'kind':'solid','color':{'r':0.7,'g':0.2,'b':0.2,'a':1}}}
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
            elif w=='visibility':
                W=H=512
                def plate(pid,document):
                    return {'id':pid,'title':'可见性预算'+pid,'document':document}
                def layer(shapes,clip=None,mask=None,lid='sheet'):
                    l={'id':lid,'opacity':1,'blend':'normal','shapes':shapes}
                    if clip is not None:l['clip']=clip
                    if mask is not None:l['mask']=mask
                    return l
                bg={'r':0.04,'g':0.05,'b':0.09,'a':1}
                def document(layers):return {'width':W,'height':H,'background':bg,'layers':layers}
                def draw(pid,fmt='png',status=201):
                    return call('POST',f'/v1/plates/{pid}/render?revision=1&format={fmt}',status=status)
                # 1) Thousands of fully off-canvas edges: rejected under the old
                #    total-segment rule (2402*512*512*4 > 1.6e8), admitted now
                #    because the visible budget is zero.
                hidden=plate('hidden-heavy',document([layer([
                    hidden_shape('far-a',9000,9000,100),hidden_shape('far-b',-9000,-9000,100)])]))
                call('POST','/v1/plates',hidden,status=201)
                t0=time.monotonic();a_hidden=draw('hidden-heavy');dt=time.monotonic()-t0
                assert dt<5,f'invisible geometry must stay bounded, took {dt:.2f}s'
                raw_hidden,_=call('GET','/v1/artifacts/'+a_hidden['id'],raw=True)
                assert struct.unpack('>II',raw_hidden[16:24])==(W,H)
                # 2) The same edge count crossing the whole canvas still fails
                #    the same 160M per-sample budget.
                spanner=plate('spanner',document([layer([hidden_shape('span',1,1,100)])]))
                call('POST','/v1/plates',spanner,status=400)
                call('GET','/v1/plates/spanner',status=404)
                # 3) Invisible contours cannot change a single visible pixel.
                visible={'id':'square','path':[
                    {'kind':'M','points':[{'x':40,'y':40}]},
                    {'kind':'L','points':[{'x':200,'y':40}]},
                    {'kind':'L','points':[{'x':200,'y':200}]},
                    {'kind':'L','points':[{'x':40,'y':200}]},{'kind':'Z'}],
                    'fill_rule':'nonzero','transform':[1,0,0,1,0,0],
                    'brush':{'kind':'solid','color':{'r':0.9,'g':0.7,'b':0.2,'a':1}}}
                plain=plate('plain',document([layer([copy.deepcopy(visible)])]))
                mixed=plate('mixed',document([layer([copy.deepcopy(visible),
                    hidden_shape('far-c',8000,8000,100)])]))
                call('POST','/v1/plates',plain,status=201);call('POST','/v1/plates',mixed,status=201)
                a_plain=draw('plain');a_mixed=draw('mixed')
                raw_plain,_=call('GET','/v1/artifacts/'+a_plain['id'],raw=True)
                raw_mixed,_=call('GET','/v1/artifacts/'+a_mixed['id'],raw=True)
                assert raw_plain==raw_mixed,'off-canvas geometry altered visible pixels'
                # 4) Holes (an off-canvas inner ring) and boundary-grazing
                #    geometry are never rough-clipped: the plates remain
                #    admissible and render identical content. A ring inside a
                #    partially visible path stays fully charged because the fill
                #    rule evaluates every ring per sample; only whole disjoint
                #    paths/shapes are provably invisible, so the hole ring is
                #    kept small rather than hidden behind a budget shortcut.
                holed=copy.deepcopy(visible);holed['id']='holed';holed['fill_rule']='evenodd'
                holed['path']+=cubic_ring(5000,5000,4)
                holed_doc=plate('holed-plate',document([layer([holed])]))
                call('POST','/v1/plates',holed_doc,status=201)
                assert draw('holed-plate')['digest']==a_plain['digest']
                grazing=copy.deepcopy(visible);grazing['id']='grazing'
                grazing['transform']=[1,0,0,1,-39.999999999,0]
                call('POST','/v1/plates',plate('grazing-plate',document([layer([grazing])])),status=201)
                # A clearly separated translation is still admissible but renders
                # only the background, and an off-canvas clip hides its layer.
                far=copy.deepcopy(visible);far['id']='far';far['transform']=[1,0,0,1,-400,0]
                call('POST','/v1/plates',plate('far-plate',document([layer([far])])),status=201)
                a_far=draw('far-plate')
                assert call('GET','/v1/artifacts/'+a_far['id'],raw=True)[0]==raw_hidden
                clipped=plate('clipped',document([layer([copy.deepcopy(visible)],
                    clip=[{'kind':'M','points':[{'x':900,'y':900}]},
                          {'kind':'L','points':[{'x':940,'y':900}]},
                          {'kind':'L','points':[{'x':940,'y':940}]},
                          {'kind':'L','points':[{'x':900,'y':940}]},{'kind':'Z'}])]))
                call('POST','/v1/plates',clipped,status=201)
                assert draw('clipped')['digest']==a_far['digest']
                # 5) SVG export shares the compiled geometry and stays deterministic.
                svg=draw('hidden-heavy',fmt='svg');assert svg['digest']==draw('hidden-heavy',fmt='svg')['digest']
                # 6) Envelope import and restart recovery recompile through the
                #    same admission and reproduce identical output.
                sealed,_=call('GET','/v1/plates/hidden-heavy/bundle',raw=True)
                stop();os.rename(base/'state',base/'original');start()
                call('POST','/v1/bundles',sealed,status=201)
                assert draw('hidden-heavy')['digest']==a_hidden['digest']
                stop();start()
                assert draw('hidden-heavy')['digest']==a_hidden['digest']
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
