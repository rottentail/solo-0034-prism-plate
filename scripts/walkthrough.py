#!/usr/bin/env python3
"""Run bounded production HTTP journeys against an isolated ephemeral service."""
import argparse, concurrent.futures, copy, hashlib, json, os, pathlib, signal, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
# Digests produced by the pre-versioning writer for examples/amber-proof.json.
# They pin legacy rendering so an upgrade cannot silently change old校样 bytes.
LEGACY_PNG='a970eaf1c892942dd1fcdd54e1cf9d1fdcd0f48caaa174785cbf5ed38d24db28'
LEGACY_SVG='9ea81c668790077a782a09ab9fcd13ccd6d02e5a4507468abaa089b8a723af53'

def migration_document(background):
    """Scene with a transparent endpoint carrying hidden RGB, a near-transparent
    but nonzero stop, and a normal stop: the cases where straight sRGB and
    premultiplied linear values differ or where information can be lost."""
    return {
        'width':32,'height':24,'background':background,
        'layers':[{'id':'wash','opacity':0.8,'blend':'normal','shapes':[
            {'id':'band','fill_rule':'nonzero','transform':[1,0.1,-0.05,1,2,1],
             'path':[{'kind':'M','points':[{'x':1,'y':1}]},{'kind':'L','points':[{'x':30,'y':1}]},
                     {'kind':'L','points':[{'x':30,'y':22}]},{'kind':'L','points':[{'x':1,'y':22}]},{'kind':'Z'}],
             'brush':{'kind':'linear','from':{'x':0,'y':0},'to':{'x':32,'y':24},
                      'color':{'r':0,'g':0,'b':0,'a':0},'stops':[
                          {'at':0,'color':{'r':1,'g':0,'b':0.25,'a':0}},
                          {'at':0.5,'color':{'r':0.2,'g':0.9,'b':1,'a':0.0001}},
                          {'at':1,'color':{'r':0.05,'g':0.1,'b':0.8,'a':0.6}}]}},
            {'id':'flat','fill_rule':'evenodd','transform':[1,0,0,1,0,0],
             'path':[{'kind':'M','points':[{'x':4,'y':4}]},{'kind':'L','points':[{'x':12,'y':4}]},
                     {'kind':'L','points':[{'x':12,'y':12}]},{'kind':'L','points':[{'x':4,'y':12}]},{'kind':'Z'}],
             'brush':{'kind':'solid','color':{'r':0.7,'g':0.2,'b':0.1,'a':0.35}}}]}]}

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
        def render_png(plate,revision=1):return call('POST',f'/v1/plates/{plate}/render?revision={revision}&format=png',status=201)['digest']
        def render_svg(plate,revision=1):return call('POST',f'/v1/plates/{plate}/render?revision={revision}&format=svg',status=201)
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
            elif w=='migration':
                # Sign off the old校样 first, then upgrade the same plate and edit
                # the gradient in the new model; both revisions travel together.
                legacy_png=render();assert legacy_png['digest']==LEGACY_PNG
                raw,_=call('GET','/v1/artifacts/'+legacy_png['id'],raw=True);assert hashlib.sha256(raw).hexdigest()==legacy_png['digest']
                legacy_svg=render('svg');assert legacy_svg['digest']==LEGACY_SVG
                call('POST','/v1/proofs',{'id':'legacy-proof','artifact':legacy_png['id']},status=201)
                assert call('PUT','/v1/proofs/legacy-proof',{'state':'accepted','note':'旧校样签下'})['state']=='accepted'
                up=call('POST','/v1/plates/amber-plate/upgrade',{'expected_revision':1});assert up['changed'] and len(up['plate']['revisions'])==2
                v2=up['plate']['revisions'][1];assert v2['document']['color_model']=={'version':2,'space':'linear-srgb','alpha':'premultiplied'}
                # The old revision's bytes, digest and artifact identity are untouched.
                assert up['plate']['revisions'][0]==created['revisions'][0]
                assert call('POST','/v1/plates/amber-plate/render?revision=1&format=png',status=201)['digest']==LEGACY_PNG
                assert call('POST','/v1/plates/amber-plate/render?revision=1&format=svg',status=201)['digest']==LEGACY_SVG
                # Conversion is render-equivalent for every color with alpha>0.
                assert call('POST','/v1/plates/amber-plate/render?revision=2&format=png',status=201)['digest']==LEGACY_PNG
                new_svg=call('POST','/v1/plates/amber-plate/render?revision=2&format=svg',status=201)
                svg_raw,_=call('GET','/v1/artifacts/'+new_svg['id'],raw=True);assert b'color-model:prism-plate-v2-linear-premultiplied' in svg_raw
                # Upgrading again is a no-op; editing back to an unversioned document is refused.
                again=call('POST','/v1/plates/amber-plate/upgrade',{'expected_revision':2});assert not again['changed'] and len(again['plate']['revisions'])==2
                call('PUT','/v1/plates/amber-plate',{'expected_revision':2,'document':seed['document']},status=409)
                # Edit the gradient in the new model: components are linear-premultiplied.
                edited=copy.deepcopy(v2['document']);edited['layers'][0]['shapes'][0]['brush']['stops'][0]['color']={'r':0.4,'g':0.2,'b':0.05,'a':0.8}
                call('PUT','/v1/plates/amber-plate',{'expected_revision':2,'document':edited})
                # Unrepresentable or unknown color models are rejected without touching history.
                bad=copy.deepcopy(edited);bad['background']={'r':0.6,'g':0.1,'b':0.1,'a':0.2};call('PUT','/v1/plates/amber-plate',{'expected_revision':3,'document':bad},status=400)
                bad=copy.deepcopy(edited);bad['color_model']={'version':3,'space':'linear-srgb','alpha':'premultiplied'};call('PUT','/v1/plates/amber-plate',{'expected_revision':3,'document':bad},status=400)
                assert len(call('GET','/v1/plates/amber-plate')['revisions'])==3
                # The accepted legacy proof still protects its artifact after upgrade.
                call('DELETE','/v1/artifacts/'+legacy_png['id'],status=409)
                sealed,_=call('GET','/v1/plates/amber-plate/bundle',raw=True);assert json.loads(sealed)['schema']=='prism-plate-bundle-v2'
                # Restart: the snapshot declares v2 so a legacy writer refuses it, and all history still verifies.
                stop();start()
                assert json.loads((base/'state/state.json').read_text())['schema']=='prism-plate-v2'
                assert call('GET','/v1/plates/amber-plate')['revisions'][0]['digest']==created['revisions'][0]['digest']
                assert call('POST','/v1/plates/amber-plate/render?revision=1&format=png',status=201)['digest']==LEGACY_PNG
                assert call('GET','/v1/artifacts/'+legacy_png['id'],raw=True)[0][:8]==b'\x89PNG\r\n\x1a\n'
                # v2 bundles import into an empty instance; the same bytes are refused where the id is taken.
                call('POST','/v1/bundles',sealed,status=409)
                stop();os.rename(base/'state',base/'old');start()
                imported=call('POST','/v1/bundles',sealed,status=201);assert imported['revisions'][1]['document']['color_model']['version']==2
                assert call('POST','/v1/plates/amber-plate/render?revision=1&format=png',status=201)['digest']==LEGACY_PNG
                assert call('POST','/v1/plates/amber-plate/render?revision=2&format=png',status=201)['digest']==LEGACY_PNG
                # A v1 envelope cannot carry v2 revisions; schema and content must agree.
                forged=json.loads(sealed);forged['schema']='prism-plate-bundle-v1';call('POST','/v1/bundles',json.dumps(forged).encode(),status=400)
                # Downgrade path: branch the legacy revision. The branch is a new legacy
                # plate and its bundle is v1 again, readable by the old writer.
                br=call('POST','/v1/plates/amber-plate/branch',{'revision':1,'id':'amber-legacy-fork','title':'旧稿分支'},status=201)
                assert 'color_model' not in br['revisions'][0]['document']
                b0,_=call('GET','/v1/plates/amber-legacy-fork/bundle',raw=True);assert json.loads(b0)['schema']=='prism-plate-bundle-v1'
                assert call('POST','/v1/plates/amber-legacy-fork/render?revision=1&format=png',status=201)['digest']==LEGACY_PNG
                # Archived plates cannot be upgraded; use a branch instead.
                call('POST','/v1/plates/amber-legacy-fork/archive');call('POST','/v1/plates/amber-legacy-fork/upgrade',{'expected_revision':1},status=409)
            elif w=='color-model':
                # Hidden RGB at a fully transparent endpoint: multiplying by alpha
                # makes both variants render identically, and upgrading makes that
                # lost distinction explicit instead of preserving it.
                doc_a={'id':'cm-a','title':'透明端点A','document':migration_document({'r':0.1,'g':0.1,'b':0.1,'a':1})}
                doc_b=copy.deepcopy(doc_a);doc_b['id']='cm-b';doc_b['document']['layers'][0]['shapes'][0]['brush']['stops'][0]['color']['r']=0.9
                call('POST','/v1/plates',doc_a,status=201);call('POST','/v1/plates',doc_b,status=201)
                pa=render_png('cm-a');pb=render_png('cm-b');assert pa==pb, 'hidden RGB must not affect legacy pixels'
                ua=call('POST','/v1/plates/cm-a/upgrade',{'expected_revision':1});ub=call('POST','/v1/plates/cm-b/upgrade',{'expected_revision':1})
                ea,eb=ua['plate']['revisions'][1]['document'],ub['plate']['revisions'][1]['document']
                assert ea==eb, 'upgrade normalizes the unrecoverable hidden RGB'
                stop0=ea['layers'][0]['shapes'][0]['brush']['stops']
                assert stop0[0]['color']=={'r':0,'g':0,'b':0,'a':0}
                # The near-transparent but nonzero stop survives as a nonzero premultiplied value.
                assert 0<stop0[1]['color']['a']<0.001 and stop0[1]['color']['r']>0
                # Round trip: the upgraded plate renders the same PNG as its legacy source.
                assert render_png('cm-a',2)==render_png('cm-a',1)
                assert render_png('cm-a',2)==pa
                # Mixed background: the same scene on a transparent background keeps
                # premultiplied light (no color fringing from de-premultiplication).
                doc_t=copy.deepcopy(doc_a);doc_t['id']='cm-t';doc_t['document']['background']={'r':0,'g':0,'b':0,'a':0}
                call('POST','/v1/plates',doc_t,status=201)
                base_t=render_png('cm-t');call('POST','/v1/plates/cm-t/upgrade',{'expected_revision':1});assert render_png('cm-t',2)==base_t
                # SVG: legacy output is v1 markup; upgraded output is marked and
                # carries the same straight sRGB stops for alpha>0.
                svg1,_=call('GET','/v1/artifacts/'+render_svg('cm-a',1)['id'],raw=True)
                svg2,_=call('GET','/v1/artifacts/'+render_svg('cm-a',2)['id'],raw=True)
                assert b'color-model:' not in svg1 and b'color-model:prism-plate-v2-linear-premultiplied' in svg2
                # Legacy SVG keeps the authored hidden RGB; the upgraded SVG has no
                # color to recover at alpha 0, so it emits a zero stop instead.
                assert b'stop-color="rgb(100%,0%,25%)" stop-opacity="0"' in svg1
                assert b'stop-color="rgb(0%,0%,0%)" stop-opacity="0"' in svg2
                # A new draft may be created directly in the versioned model: reuse
                # the upgraded document as the first revision of a new plate.
                direct=copy.deepcopy(call('GET','/v1/plates/cm-t')['revisions'][1]['document'])
                assert call('POST','/v1/plates',{'id':'cm-direct','title':'新建v2','document':direct},status=201)['revisions'][0]['document']['color_model']['version']==2
                assert render_png('cm-direct')==render_png('cm-t',2)
                # A v2 document must not smuggle an alpha-zero color with RGB.
                bad=copy.deepcopy(ea);bad['layers'][0]['shapes'][1]['brush']['color']={'r':0.2,'g':0,'b':0,'a':0}
                call('PUT','/v1/plates/cm-a',{'expected_revision':2,'document':bad},status=400)
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
