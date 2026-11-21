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
            elif w=='restructure':
                def rect(sid,x0,y0,x1,y1,color):
                    return {'id':sid,'fill_rule':'nonzero','transform':[1,0,0,1,0,0],'brush':{'kind':'solid','color':color},
                            'path':[{'kind':'M','points':[{'x':x0,'y':y0}]},{'kind':'L','points':[{'x':x1,'y':y0}]},{'kind':'L','points':[{'x':x1,'y':y1}]},{'kind':'L','points':[{'x':x0,'y':y1}]},{'kind':'Z'}]}
                def layer(lid,shapes,opacity=1,blend='normal'):return {'id':lid,'opacity':opacity,'blend':blend,'shapes':shapes}
                def plate_doc(layers):return {'width':64,'height':64,'background':{'r':0.08,'g':0.09,'b':0.12,'a':1},'layers':layers}
                def make(pid,title,doc):return call('POST','/v1/plates',{'id':pid,'title':title,'document':doc},status=201)
                def png_digest(pid,rev):return call('POST',f'/v1/plates/{pid}/render?revision={rev}&format=png',status=201)['digest']
                def preview(pid,rev,body):return call('POST',f'/v1/plates/{pid}/restructure?revision={rev}',body)
                def apply(pid,expected,op,tolerance=0,status=200):return call('POST',f'/v1/plates/{pid}/restructure/apply',{'expected_revision':expected,'op':op,'tolerance':tolerance},status=status)
                # discovery on the amber seed: the only adjacent pair is refused and explained, no candidate is invented
                disc=preview('amber-plate',1,{})['discovery']
                assert (disc['candidates'] or [])==[];rejected=disc['rejected'];assert len(rejected)==1 and rejected[0]['class']=='refused' and rejected[0]['reasons']
                bad=apply('amber-plate',1,{'kind':'merge','layers':['amber-sheet','cyan-glaze']},status=400);assert bad['restructure']['class']=='refused' and bad['restructure']['reasons']
                # fully opaque overlapping normal layers merge exactly; the new draft renders the same PNG
                make('merge-lab','拆并合并实验',plate_doc([
                    layer('base',[rect('ground',0,0,64,64,{'r':0.9,'g':0.88,'b':0.84,'a':1})]),
                    layer('low',[rect('red-box',8,8,40,40,{'r':0.85,'g':0.2,'b':0.2,'a':1}),rect('blue-box',24,24,56,56,{'r':0.2,'g':0.4,'b':0.85,'a':1})]),
                    layer('high',[rect('green-box',16,16,48,48,{'r':0.25,'g':0.8,'b':0.35,'a':1})])]))
                d1=png_digest('merge-lab',1)
                disc=preview('merge-lab',1,{})['discovery'];cands=disc['candidates'] or []
                assert len(cands)==2 and all(c['class']=='exact' for c in cands) and all('document' not in c for c in cands)
                plan=preview('merge-lab',1,{'op':{'kind':'merge','layers':['low','high']}})['plan']
                assert plan['class']=='exact' and plan['background_independent'] and plan['measured_max_delta']==0
                assert plan['document']['layers'][1]['id']=='low' and len(plan['document']['layers'][1]['shapes'])==3
                far=preview('merge-lab',1,{'op':{'kind':'merge','layers':['base','high']}})['plan']
                assert far['class']=='refused' and any('adjacent' in r for r in far['reasons'])
                done=apply('merge-lab',1,{'kind':'merge','layers':['low','high']})
                assert done['restructure']['class']=='exact' and len(done['plate']['revisions'])==2
                assert png_digest('merge-lab',2)==d1 and png_digest('merge-lab',1)==d1
                history=call('GET','/v1/plates/merge-lab')['revisions']
                assert history[0]['document']['layers'][1]['id']=='low' and len(history[0]['document']['layers'])==3
                # transparency is not blanket-rejected: disjoint translucent shapes under group opacity merge exactly
                make('alpha-lab','透明不相交合并',plate_doc([
                    layer('left',[rect('ghost-red',4,4,28,28,{'r':0.9,'g':0.3,'b':0.3,'a':0.7})],opacity=0.6),
                    layer('right',[rect('ghost-blue',36,36,60,60,{'r':0.3,'g':0.5,'b':0.9,'a':0.5})],opacity=0.6)]))
                a1=png_digest('alpha-lab',1)
                plan=preview('alpha-lab',1,{'op':{'kind':'merge','layers':['left','right']}})['plan']
                assert plan['class']=='exact' and plan['measured_max_delta']==0
                apply('alpha-lab',1,{'kind':'merge','layers':['left','right']});assert png_digest('alpha-lab',2)==a1
                # differing group opacity folds into brush alpha and stays exact while coverage is disjoint
                make('fold-lab','组透明度折入',plate_doc([
                    layer('solid-low',[rect('warm',4,4,28,28,{'r':0.9,'g':0.6,'b':0.2,'a':0.9})]),
                    layer('veil-high',[rect('cool',36,36,60,60,{'r':0.2,'g':0.6,'b':0.9,'a':0.4})],opacity=0.5)]))
                f1=png_digest('fold-lab',1)
                plan=preview('fold-lab',1,{'op':{'kind':'merge','layers':['solid-low','veil-high']}})['plan']
                assert plan['class']=='exact' and any('fold' in r for r in plan['reasons'])
                done=apply('fold-lab',1,{'kind':'merge','layers':['solid-low','veil-high']})
                assert png_digest('fold-lab',2)==f1
                merged=done['plate']['revisions'][1]['document']['layers'][0]
                assert merged['opacity']==1 and abs(merged['shapes'][1]['brush']['color']['a']-0.2)<1e-9
                # overlapping translucent shapes under group opacity: refused at zero tolerance, bounded within tolerance
                make('overlap-lab','有界近似核对',plate_doc([
                    layer('veil-a',[rect('warm-x',8,8,40,40,{'r':0.85,'g':0.3,'b':0.3,'a':0.8})],opacity=0.5),
                    layer('veil-b',[rect('cool-x',24,24,56,56,{'r':0.3,'g':0.5,'b':0.85,'a':0.8})],opacity=0.5)]))
                o1=png_digest('overlap-lab',1)
                strict=preview('overlap-lab',1,{'op':{'kind':'merge','layers':['veil-a','veil-b']}})['plan']
                assert strict['class']=='refused' and strict['measured_max_delta']>0
                loose=preview('overlap-lab',1,{'op':{'kind':'merge','layers':['veil-a','veil-b']},'tolerance':255})['plan']
                assert loose['class']=='bounded' and not loose['background_independent'] and loose['measured_max_delta']==strict['measured_max_delta']
                bad=apply('overlap-lab',1,{'kind':'merge','layers':['veil-a','veil-b']},status=400);assert bad['restructure']['class']=='refused'
                done=apply('overlap-lab',1,{'kind':'merge','layers':['veil-a','veil-b']},tolerance=255)
                assert done['restructure']['class']=='bounded' and png_digest('overlap-lab',2)!=o1
                # splitting a translucent disjoint group out of an attributed layer is exact
                make('split-lab','形状拆出实验',plate_doc([
                    layer('duo',[rect('low-box',4,4,28,28,{'r':0.9,'g':0.4,'b':0.3,'a':0.6}),rect('top-box',36,36,60,60,{'r':0.3,'g':0.7,'b':0.9,'a':0.9})],opacity=0.8),
                    layer('plain',[rect('p-low',4,32,28,60,{'r':0.7,'g':0.7,'b':0.2,'a':0.5}),rect('p-top',36,4,60,28,{'r':0.2,'g':0.7,'b':0.7,'a':0.5})])]))
                s1=png_digest('split-lab',1)
                plan=preview('split-lab',1,{'op':{'kind':'split','layer':'duo','shapes':['top-box'],'position':'above'}})['plan']
                assert plan['class']=='exact' and plan['measured_max_delta']==0
                plan=preview('split-lab',1,{'op':{'kind':'split','layer':'plain','shapes':['p-low'],'position':'above'}})['plan']
                assert plan['class']=='exact'
                done=apply('split-lab',1,{'kind':'split','layer':'duo','shapes':['top-box'],'position':'above'})
                assert png_digest('split-lab',2)==s1
                newdoc=done['plate']['revisions'][1]['document']
                assert [l['id'] for l in newdoc['layers']]==['duo','duo-x1','plain']
                assert newdoc['layers'][1]['opacity']==0.8 and len(newdoc['layers'][0]['shapes'])==1
                # overlapping translucent shapes under group opacity cannot split exactly at zero tolerance
                make('tangle-lab','重叠透明拆出拒绝',plate_doc([
                    layer('tangle',[rect('one',8,8,40,40,{'r':0.8,'g':0.3,'b':0.3,'a':0.7}),rect('two',24,24,56,56,{'r':0.3,'g':0.4,'b':0.8,'a':0.7})],opacity=0.6)]))
                plan=preview('tangle-lab',1,{'op':{'kind':'split','layer':'tangle','shapes':['one'],'position':'below'}})['plan']
                assert plan['class']=='refused' and plan['measured_max_delta']>0
                # contract rejections: bad tolerance, unknown kind, stale expected revision
                call('POST','/v1/plates/split-lab/restructure?revision=1',{'op':{'kind':'split','layer':'duo','shapes':['top-box']},'tolerance':300},status=400)
                call('POST','/v1/plates/split-lab/restructure?revision=1',{'op':{'kind':'rotate'}},status=400)
                call('POST','/v1/plates/split-lab/restructure/apply',{'expected_revision':9,'op':{'kind':'split','layer':'duo','shapes':['top-box']}},status=409)
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
