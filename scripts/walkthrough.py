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
                # Legacy guard: finalization is mandatory, an empty archive body
                # cannot produce a manifest and the plate stays editable.
                call('POST','/v1/plates/amber-plate/archive',{},status=409)
                # No manifest exists before finalization.
                call('GET','/v1/plates/amber-plate/handover',status=404)
                # A plate that does not exist is still a genuine 404.
                call('POST','/v1/plates/no-such-plate/archive',{'revision':1},status=404)
            elif w=='finalize':
                # Build a history where the latest revision is NOT the approved
                # design. Revision 1 gets PNG/SVG and an accepted review; a newer
                # revision 2 exists but never passes review.
                rev1_png=render('png',1);rev1_svg=render('svg',1)
                rev2_doc=copy.deepcopy(seed['document']);rev2_doc['background']['r']=.25
                call('PUT','/v1/plates/amber-plate',{'expected_revision':1,'document':rev2_doc})
                rev2_png=render('png',2)  # latest revision, but no accepted proof
                call('POST','/v1/proofs',{'id':'proof-open','artifact':rev2_png['id']},status=201)
                call('POST','/v1/proofs',{'id':'proof-ok','artifact':rev1_png['id']},status=201)
                call('PUT','/v1/proofs/proof-ok',{'state':'accepted','note':'像素承诺与镂空通过'})
                call('POST','/v1/proofs',{'id':'proof-bad','artifact':rev1_svg['id']},status=201)
                call('PUT','/v1/proofs/proof-bad',{'state':'rejected','note':'矢量插值差异待核'})

                # Rejecting the wrong-revision artifact: the latest revision must
                # not be silently treated as the finalized design.
                bad_pick={'revision':2,'artifacts':[rev1_png['id']],'proofs':['proof-ok']}
                call('POST','/v1/plates/amber-plate/archive',bad_pick,status=409)
                # Unknown and revoked artifacts/proofs are hard errors.
                call('POST','/v1/plates/amber-plate/archive',{'revision':1,'artifacts':['nope']},status=404)
                call('POST','/v1/plates/amber-plate/archive',{'revision':1,'proofs':['proof-bad']},status=409)
                call('POST','/v1/plates/amber-plate/archive',{'revision':1,'proofs':['proof-open']},status=409)
                call('POST','/v1/plates/amber-plate/archive',{'revision':99},status=409)
                call('POST','/v1/plates/amber-plate/archive',{'revision':1,'on_missing':'bogus'},status=400)
                # Malformed selections: repeated id (renders of one revision and
                # format are deterministic and share a stable artifact id).
                call('POST','/v1/plates/amber-plate/archive',{'revision':1,'artifacts':[rev1_png['id'],rev1_png['id']],'on_missing':'exclude'},status=400)
                # A revoked deliverable can never be finalized even under exclude.
                revoke_seed={'id':'revoke-plate','title':'撤销版面','document':copy.deepcopy(seed['document'])}
                call('POST','/v1/plates',revoke_seed,status=201)
                revoked_png=call('POST','/v1/plates/revoke-plate/render?revision=1&format=png',status=201)
                call('DELETE','/v1/artifacts/'+revoked_png['id'])
                call('POST','/v1/plates/revoke-plate/archive',{'revision':1,'artifacts':[revoked_png['id']],'on_missing':'exclude'},status=409)

                # Default reject policy: a gap (no SVG, no accepted proof) refuses.
                call('POST','/v1/plates/amber-plate/archive',{'revision':1,'artifacts':[rev1_png['id']]},status=409)
                # Exclude policy allows archiving with explicit null gaps.
                gap_seed={'id':'gap-plate','title':'缺口版面','document':copy.deepcopy(seed['document'])}
                call('POST','/v1/plates',gap_seed,status=201)
                gap_png=call('POST','/v1/plates/gap-plate/render?revision=1&format=png',status=201)
                gap_h=call('POST','/v1/plates/gap-plate/archive',{'revision':1,'artifacts':[gap_png['id']],'on_missing':'exclude'})
                assert gap_h['complete'] is False and gap_h['svg'] is None and gap_h['png']['id']==gap_png['id'] and gap_h['proofs']==[]
                assert len(gap_h['digest'])==64
                gap_rep=call('GET','/v1/plates/gap-plate/handover/verify')
                assert gap_rep['digest_valid'] and gap_rep['lineage_valid'] and gap_rep['png_verified'] and not gap_rep['svg_verified'] and gap_rep['proofs_verified']==0 and not gap_rep['complete']

                # Happy path: explicit revision 1 selection with PNG, SVG and the
                # accepted proof, even though revision 2 is the latest.
                pick={'revision':1,'artifacts':[rev1_png['id'],rev1_svg['id']],'proofs':['proof-ok']}
                h=call('POST','/v1/plates/amber-plate/archive',pick)
                assert h['revision']==1 and h['complete'] is True and h['on_missing']=='reject'
                assert h['scene_digest']==created['revisions'][0]['digest']
                assert h['png']['id']==rev1_png['id'] and h['png']['digest']==rev1_png['digest'] and h['png']['format']=='png'
                assert h['svg']['id']==rev1_svg['id'] and h['svg']['digest']==rev1_svg['digest'] and h['svg']['format']=='svg'
                assert len(h['proofs'])==1 and h['proofs'][0]['id']=='proof-ok'
                assert h['proofs'][0]['artifact']==rev1_png['id'] and h['proofs'][0]['state']=='accepted'
                # The manifest self-check digest is present and stable.
                assert isinstance(h['digest'],str) and len(h['digest'])==64
                assert call('GET','/v1/plates/amber-plate/handover')['digest']==h['digest']
                # Independent re-check: digest, PNG/SVG bytes and the accepted
                # review all reconcile against the chosen revision 1 source.
                rep=call('GET','/v1/plates/amber-plate/handover/verify')
                assert rep['digest_valid'] and rep['lineage_valid'] and rep['png_verified'] and rep['svg_verified'] and rep['proofs_verified']==1 and rep['complete']

                # After archiving: history stays readable, branching still works,
                # revision is refused, and finalization cannot be repeated.
                call('PUT','/v1/plates/amber-plate',{'expected_revision':2,'document':rev2_doc},status=409)
                call('POST','/v1/plates/amber-plate/archive',pick,status=409)
                got=call('GET','/v1/plates/amber-plate')
                assert got['state']=='archived' and len(got['revisions'])==2
                # Rendering historical revisions still works after finalization.
                assert render('png',1)['digest']==rev1_png['digest']
                assert render('png',2)['id']==rev2_png['id']
                fork=call('POST','/v1/plates/amber-plate/branch',{'revision':1,'id':'amber-after-archive','title':'归档后分支'},status=201)
                assert fork['revisions'][0]['digest']==h['scene_digest']
                # The accepted-proof artifact stays protected from revocation.
                call('DELETE','/v1/artifacts/'+rev1_png['id'],status=409)
                # The listed SVG has no accepted proof, so it can be revoked after
                # finalization; the independent verify must then flag that row
                # instead of continuing to report a clean handover.
                call('DELETE','/v1/artifacts/'+rev1_svg['id'])
                stale=call('GET','/v1/plates/amber-plate/handover/verify')
                assert stale['png_verified'] and not stale['svg_verified'] and not stale['lineage_valid']

                # Restart: the manifest and its full lineage survive recovery and
                # still cross-check against the persisted revision and bytes. The
                # revoked-SVG verdict is persisted too, proving verify reads facts
                # rather than trusting the manifest's self-reported rows.
                stop();start()
                h2=call('GET','/v1/plates/amber-plate/handover')
                assert h2==h
                raw,hs=call('GET','/v1/artifacts/'+h2['png']['id'],raw=True)
                assert hashlib.sha256(raw).hexdigest()==h2['png']['digest']
                assert hs['x-scene-digest']==h2['scene_digest']
                assert call('GET','/v1/plates/gap-plate/handover')['complete'] is False
                rep2=call('GET','/v1/plates/amber-plate/handover/verify')
                assert rep2['digest_valid'] and not rep2['lineage_valid'] and rep2['png_verified'] and not rep2['svg_verified'] and rep2['proofs_verified']==1
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
