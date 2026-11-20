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
            elif w=='selective-restore':
                # Revision 1 is the old draft; revision 2 drops cyan-window and the
                # cyan group's mask/opacity/blend, changes the background, and leaves
                # a new overlapping shape plus a redrawn amber contour. A selective
                # restore must bring back only the chosen geometry and say what the
                # destination group no longer keeps.
                old=copy.deepcopy(seed['document'])
                q={'expected_revision':1,'document':copy.deepcopy(old)}
                q['document']['background']={'r':0.2,'g':0.2,'b':0.2,'a':1}
                q['document']['layers'][0]['clip']=[]
                amber=copy.deepcopy(old['layers'][0]['shapes'][0]);amber['id']='amber-overlap';amber['path']=[{'kind':'M','points':[{'x':20,'y':20}]},{'kind':'L','points':[{'x':80,'y':20}]},{'kind':'L','points':[{'x':80,'y':60}]},{'kind':'L','points':[{'x':20,'y':60}]},{'kind':'Z'}]
                q['document']['layers'][0]['shapes']=[amber]
                q['document']['layers'][1]['opacity']=0.25;q['document']['layers'][1]['blend']='normal';q['document']['layers'][1]['mask']=[]
                overlap=copy.deepcopy(old['layers'][1]['shapes'][0]);overlap['id']='cyan-overlap';overlap['path']=[{'kind':'M','points':[{'x':90,'y':70}]},{'kind':'L','points':[{'x':170,'y':70}]},{'kind':'L','points':[{'x':170,'y':120}]},{'kind':'L','points':[{'x':90,'y':120}]},{'kind':'Z'}]
                q['document']['layers'][1]['shapes']=[overlap]
                current=call('PUT','/v1/plates/amber-plate',q);assert len(current['revisions'])==2

                # Destination context: cyan-window lands in the current cyan group.
                req={'base_revision':2,'source_revision':1,'context':'destination','items':[{'shape':'cyan-window','layer':'cyan-glaze'}]}
                call('POST','/v1/plates/amber-plate/restore/preview',{'base_revision':1,'source_revision':1,'context':'destination','items':[{'shape':'cyan-window','layer':'cyan-glaze'}]},status=409)
                plan=call('POST','/v1/plates/amber-plate/restore/preview',req)
                assert plan['result_digest'] and plan['plan_digest'] and plan['items'][0]['action']=='appended'
                assert plan['items'][0]['geometry']['contours']==2 and plan['items'][0]['geometry']['hole_capable'] is True
                assert plan['items'][0]['geometry']['winding']=='consistent'
                assert any('mask' in s and 'destination' in s for s in plan['cannot_preserve']),plan['cannot_preserve']
                assert any('opacity' in s for s in plan['cannot_preserve'])
                assert any('background' in s for s in plan['cannot_preserve'])
                assert any(s.startswith('restored shape "cyan-window" has bounds overlapping existing shape "cyan-overlap"') for s in plan['warnings']),plan['warnings']
                assert any('nested inside another' in s and 'hole' in s for s in plan['warnings']),plan['warnings']
                assert any('hole interiors' in s for s in plan['cannot_preserve']),plan['cannot_preserve']
                assert plan['scope']['restored_shapes']==1 and plan['scope']['untouched_shapes']==2
                call('POST','/v1/plates/amber-plate/restore',{'expected_revision':2,'plan_digest':'0'*64,'request':req},status=409)
                call('POST','/v1/plates/amber-plate/restore',{'expected_revision':1,'plan_digest':plan['plan_digest'],'request':req},status=409)
                assert len(call('GET','/v1/plates/amber-plate')['revisions'])==2
                committed=call('POST','/v1/plates/amber-plate/restore',{'expected_revision':2,'plan_digest':plan['plan_digest'],'request':req},status=201)
                rev=committed['plate']['revisions'][2]
                assert rev['digest']==plan['result_digest'] and rev['restored']['source_revision']==1
                assert rev['restored']['source_digest']==plan['source_digest'] and rev['restored']['mode']=='destination'
                assert rev['restored']['shapes']==[{'source':'cyan-window','restored':'cyan-window','action':'appended'}]
                doc=rev['document'];assert len(doc['layers'])==2 and doc['background']=={'r':0.2,'g':0.2,'b':0.2,'a':1}
                assert doc['layers'][1]['opacity']==0.25 and doc['layers'][1].get('mask',[])==[]
                assert [s['id'] for s in doc['layers'][1]['shapes']]==['cyan-overlap','cyan-window']
                assert doc['layers'][1]['shapes'][1]['path']==old['layers'][1]['shapes'][0]['path']
                assert doc['layers'][1]['shapes'][1]['transform']==old['layers'][1]['shapes'][0]['transform']
                a=render(revision=3);assert a['scene_digest']==rev['digest']
                png=call('GET','/v1/artifacts/'+a['id'],raw=True)[0];assert png[:8]==b'\x89PNG\r\n\x1a\n'
                # Replacing an existing id keeps its current group context; the plan still
                # reports the group attributes that differ from the source revision.
                repl=call('POST','/v1/plates/amber-plate/restore/preview',{'base_revision':3,'source_revision':1,'context':'destination','items':[{'shape':'cyan-window','layer':'cyan-glaze'}]})
                assert repl['items'][0]['action']=='replaced'
                notes=repl['items'][0]['not_preserved'];assert len(notes)==3,notes
                assert any('opacity' in n and '0.72' in n and '0.25' in n for n in notes)
                assert any('blend' in n and 'screen' in n and 'normal' in n for n in notes)
                assert any('mask' in n for n in notes)

                # Source-layer context: recreate amber-sheet with its clip, but at the
                # top of the stack; the document background still belongs to the draft.
                req2={'base_revision':3,'source_revision':1,'context':'source','items':[{'shape':'amber-contour'}]}
                plan2=call('POST','/v1/plates/amber-plate/restore/preview',req2)
                assert plan2['scope']['layers_added']==['amber-sheet-restored']
                assert plan2['items'][0]['adopted']==['layer.opacity','layer.blend','layer.clip']
                assert any('stack' in s for s in plan2['cannot_preserve']),plan2['cannot_preserve']
                assert any('background' in s for s in plan2['cannot_preserve'])
                assert plan2['scope']['untouched_shapes']==3
                committed2=call('POST','/v1/plates/amber-plate/restore',{'expected_revision':3,'plan_digest':plan2['plan_digest'],'request':req2},status=201)
                assert committed2['plate']['revisions'][3]['restored']['shapes']==[{'source':'amber-contour','restored':'amber-contour','action':'created-layer'}]
                d2=committed2['plate']['revisions'][3]['document']
                assert d2['layers'][-1]['id']=='amber-sheet-restored' and d2['layers'][-1]['clip']==[{'kind':o['kind'],**({'points':o['points']} if 'points' in o else {})} for o in old['layers'][0]['clip']]
                assert d2['layers'][-1]['opacity']==1 and d2['layers'][-1]['blend']=='normal'
                assert d2['background']=={'r':0.2,'g':0.2,'b':0.2,'a':1}
                call('POST','/v1/plates/amber-plate/restore',{'expected_revision':4,'plan_digest':plan2['plan_digest'],'request':{'base_revision':4,'source_revision':9,'context':'source','items':[{'shape':'cyan-window'}]}},status=404)
                call('POST','/v1/plates/amber-plate/restore/preview',{'base_revision':4,'source_revision':1,'context':'destination','items':[{'shape':'absent','layer':'cyan-glaze'}]},status=400)
                stop();start();again=call('GET','/v1/plates/amber-plate');assert again['revisions'][3]['digest']==committed2['plate']['revisions'][3]['digest']
                assert render(revision=4)['scene_digest']==again['revisions'][3]['digest']
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
