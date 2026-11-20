#!/usr/bin/env python3
"""Run bounded production HTTP journeys against an isolated ephemeral service."""
import argparse, concurrent.futures, copy, hashlib, json, os, pathlib, signal, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request, zlib
ROOT=pathlib.Path(__file__).resolve().parents[1]

def read_gray_png(raw):
    """Decode an 8-bit grayscale, non-interlaced PNG with the stdlib only."""
    assert raw[:8]==b'\x89PNG\r\n\x1a\n'
    pos=8; width=height=0; bitdepth=colortype=interlace=0; idat=b''
    while pos < len(raw):
        (ln,)=struct.unpack('>I',raw[pos:pos+4]); typ=raw[pos+4:pos+8]; body=raw[pos+8:pos+8+ln]
        if typ==b'IHDR':
            width,height,bitdepth,colortype,_,_,interlace=struct.unpack('>IIBBBBB',body)
        elif typ==b'IDAT':
            idat+=body
        pos+=12+ln
    assert bitdepth==8 and colortype==0 and interlace==0
    data=zlib.decompress(idat); stride=width; rows=[]; prev=[0]*stride; i=0
    for _ in range(height):
        f=data[i]; i+=1; line=list(data[i:i+stride]); i+=stride
        for x in range(stride):
            a=line[x]; left=line[x-1] if x else 0; up=prev[x]; ul=prev[x-1] if x else 0
            if f==1: a=(a+left)&255
            elif f==2: a=(a+up)&255
            elif f==3: a=(a+((left+up)//2))&255
            elif f==4:
                p=left+up-ul; pa,pb,pc=abs(p-left),abs(p-up),abs(p-ul)
                pr=left if pa<=pb and pa<=pc else (up if pb<=pc else ul)
                a=(a+pr)&255
            line[x]=a
        rows.append(line); prev=line
    return width,height,rows

def read_rgb_png(raw):
    """Decode an 8-bit RGB(A), non-interlaced PNG; returns (width,height,bpp,rows)."""
    pos=8; width=height=0; bitdepth=colortype=interlace=0; idat=b''
    while pos < len(raw):
        (ln,)=struct.unpack('>I',raw[pos:pos+4]); typ=raw[pos+4:pos+8]; body=raw[pos+8:pos+8+ln]
        if typ==b'IHDR':
            width,height,bitdepth,colortype,_,_,interlace=struct.unpack('>IIBBBBB',body)
        elif typ==b'IDAT':
            idat+=body
        pos+=12+ln
    assert bitdepth==8 and colortype in (2,6) and interlace==0
    bpp=3 if colortype==2 else 4
    data=zlib.decompress(idat); stride=width*bpp; rows=[]; prev=bytearray(stride); i=0
    def paeth(a,b,c):
        p=a+b-c; pa,pb,pc=abs(p-a),abs(p-b),abs(p-c)
        return a if pa<=pb and pa<=pc else (b if pb<=pc else c)
    for _ in range(height):
        f=data[i]; i+=1; line=bytearray(data[i:i+stride]); i+=stride
        for x in range(stride):
            left=line[x-bpp] if x>=bpp else 0; up=prev[x]; ul=prev[x-bpp] if x>=bpp else 0
            if f==1: line[x]=(line[x]+left)&255
            elif f==2: line[x]=(line[x]+up)&255
            elif f==3: line[x]=(line[x]+((left+up)//2))&255
            elif f==4: line[x]=(line[x]+paeth(left,up,ul))&255
        rows.append(line); prev=line
    return width,height,bpp,rows

def srgb_to_linear(v):
    v/=255.0
    return v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4
def linear_to_srgb(v):
    u=v*12.92 if v<=0.0031308 else 1.055*(v**(1/2.4))-0.055
    return round(max(0,min(1,u))*255)
def overprint_color(substrate, ink, alpha):
    """Independent premultiplied linear-light multiply expectation."""
    out=[]
    for ch in range(3):
        d=srgb_to_linear(round(substrate[ch]*255)); s=srgb_to_linear(round(ink[ch]*255))
        out.append(linear_to_srgb((1-alpha)*d + alpha*d*s))
    return out

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
            elif w=='spotsep':
                spot=json.loads((ROOT/'examples/spot-separation.json').read_text())
                call('POST','/v1/plates',spot,status=201)
                # A document without declared spot inks cannot be separated.
                call('POST','/v1/plates/amber-plate/render?revision=1&format=spotsep',status=400)
                # Admission rejects undeclared spot references, non-normal blends and duplicate ids.
                bad=copy.deepcopy(spot);bad['id']='bad-ref';bad['document']['layers'][0]['shapes'][0]['brush']['spot']='ghost'
                call('POST','/v1/plates',bad,status=400)
                bad=copy.deepcopy(spot);bad['id']='bad-blend';bad['document']['layers'][0]['blend']='multiply'
                call('POST','/v1/plates',bad,status=400)
                bad=copy.deepcopy(spot);bad['id']='bad-dup';bad['document']['spots'].append(dict(bad['document']['spots'][0]))
                call('POST','/v1/plates',bad,status=400)
                # Separation publishes the manifest plus every per-ink plate atomically.
                a=call('POST','/v1/plates/spot-registration/render?revision=1&format=spotsep',status=201)
                manifest,_=call('GET','/v1/artifacts/'+a['id'],raw=True)
                m=json.loads(manifest)
                assert m['schema']=='prism-plate-separation-v1'
                assert len(m['inks'])==3 and [i['print_order'] for i in m['inks']]==[0,1,2]
                assert m['semantics']['coverage_basis'].startswith('each plate is geometry-derived')
                plate_refs=[r for r in m['artifacts'] if r['format']=='spotplate']
                assert len(plate_refs)==3
                plates={}
                for ref in plate_refs:
                    raw,hs=call('GET','/v1/artifacts/'+ref['artifact_id'],raw=True)
                    assert hashlib.sha256(raw).hexdigest()==ref['digest']
                    assert hs['content-type']=='image/png' and hs['x-spot-ink']==ref['ink']
                    pw,ph,g=read_gray_png(raw);assert (pw,ph)==(192,144)
                    plates[ref['ink']]=g
                red,blue,gold=plates['inkPMS185'],plates['inkPMS299'],plates['inkPMS123']
                # evenodd cutouts reach the paper; bands keep full coverage.
                assert (red[30][30],red[70][58])==(255,0)
                assert (blue[40][140],blue[68][112])==(255,0)
                # knockout makes the two opaque inks mutually exclusive.
                assert not any(red[y][x]>0 and blue[y][x]>0 for y in range(25,119) for x in range(17,151))
                # overprint keeps underlying coverage while adding its own alpha (0.55 -> 140).
                assert (red[100][50],blue[100][120],gold[100][50],gold[100][120])==(255,255,140,140)
                # overlap bookkeeping names both overprinted pairs in press order.
                pairs=[(o['under_ink'],o['top_ink'],o['overprinted']) for o in m['overlaps']]
                assert ('inkPMS185','inkPMS123',True) in pairs and ('inkPMS299','inkPMS123',True) in pairs
                # every difference vs the reference is inside the overprint region.
                rc=m['recomposite']
                assert rc['different_pixels']==rc['overprint_pixels'] and rc['different_pixels']>0
                assert rc['byte_identical_without_overprint'] is False
                proof=[r for r in m['artifacts'] if r['format']=='spotproof'][0]
                proof_raw,_=call('GET','/v1/artifacts/'+proof['artifact_id'],raw=True)
                assert hashlib.sha256(proof_raw).hexdigest()==rc['recomposited_png_digest']
                # color-level overprint check: multiply math must match an independent computation.
                pw,ph,pbpp,prows=read_rgb_png(proof_raw)
                def proof_px(x,y):
                    off=x*pbpp; return tuple(prows[y][off:off+3])
                blue=(0.0,0.45,0.85); red=(0.87,0.08,0.16); gold=(0.95,0.72,0.18)
                assert proof_px(120,100)==tuple(overprint_color(blue,gold,0.55))
                assert proof_px(50,100)==tuple(overprint_color(red,gold,0.55))
                assert proof_px(90,50)==tuple(round(c*255) for c in blue)
                # opaque, knockout-only variant must recompose byte-identically.
                opaque=copy.deepcopy(spot);opaque['id']='spot-opaque'
                gl=opaque['document']['layers'][2];gl['overprint']=False
                gl['shapes'][0]['brush']['color']['a']=1.0
                call('POST','/v1/plates',opaque,status=201)
                oa=call('POST','/v1/plates/spot-opaque/render?revision=1&format=spotsep',status=201)
                om=json.loads(call('GET','/v1/artifacts/'+oa['id'],raw=True)[0])
                assert om['recomposite']['byte_identical_without_overprint'] is True
                assert om['recomposite']['different_pixels']==0
                # deterministic: a second run returns the same manifest and plate artifacts.
                again=call('POST','/v1/plates/spot-registration/render?revision=1&format=spotsep',status=201)
                assert again['id']==a['id']
                # spot gradient carries only alpha; without overprint it still recomposes identically.
                gradient_doc={
                    'width':64,'height':48,'background':{'r':1,'g':1,'b':1,'a':1},
                    'spots':[{'id':'inkFade','name':'渐变专色','proof_color':{'r':0.2,'g':0.2,'b':0.8,'a':1}}],
                    'layers':[{'id':'fade-layer','opacity':1,'blend':'normal','shapes':[
                        {'id':'fade','fill_rule':'nonzero',
                         'path':[{'kind':'M','points':[{'x':4,'y':4}]},
                                 {'kind':'L','points':[{'x':60,'y':4}]},
                                 {'kind':'L','points':[{'x':60,'y':44}]},
                                 {'kind':'L','points':[{'x':4,'y':44}]},
                                 {'kind':'Z','points':[]}],
                         'transform':[1,0,0,1,0,0],
                         'brush':{'kind':'linear','spot':'inkFade',
                                  'from':{'x':4,'y':0},'to':{'x':60,'y':0},
                                  'color':{'r':0,'g':0,'b':0,'a':0},
                                  'stops':[{'at':0,'color':{'r':0,'g':0,'b':0,'a':0.1}},
                                           {'at':1,'color':{'r':0,'g':0,'b':0,'a':1}}]}}]}]}
                call('POST','/v1/plates',{'id':'spot-fade','title':'渐变专色','document':gradient_doc},status=201)
                ga=call('POST','/v1/plates/spot-fade/render?revision=1&format=spotsep',status=201)
                gm=json.loads(call('GET','/v1/artifacts/'+ga['id'],raw=True)[0])
                gref=[r for r in gm['artifacts'] if r['format']=='spotplate'][0]
                gw,gh,gg=read_gray_png(call('GET','/v1/artifacts/'+gref['artifact_id'],raw=True)[0])
                assert (gw,gh)==(64,48) and gg[24][5] < gg[24][58] and gg[24][5]>0
                assert gm['recomposite']['byte_identical_without_overprint'] is True
                assert gm['recomposite']['different_pixels']==0
                # a separation is an atomic group: an accepted proof on one plate protects every member.
                call('POST','/v1/proofs',{'id':'proof-group','artifact':plate_refs[0]['artifact_id']},status=201)
                call('PUT','/v1/proofs/proof-group',{'state':'accepted','note':'红版通过'},status=200)
                for ref in m['artifacts']+[{'artifact_id':a['id']}]:
                    call('DELETE','/v1/artifacts/'+ref['artifact_id'],status=409)
                # without a proof, a separate plate can be revoked; re-running then refuses
                # to reactivate the revoked content-addressed member, while a new revision works.
                free=copy.deepcopy(spot);free['id']='spot-free';free['title']='无校样分版'
                call('POST','/v1/plates',free,status=201)
                fa=call('POST','/v1/plates/spot-free/render?revision=1&format=spotsep',status=201)
                fm=json.loads(call('GET','/v1/artifacts/'+fa['id'],raw=True)[0])
                revoke_id=[r['artifact_id'] for r in fm['artifacts'] if r['format']=='spotplate'][2]
                call('DELETE','/v1/artifacts/'+revoke_id,status=200)
                call('GET','/v1/artifacts/'+revoke_id,status=409)
                call('POST','/v1/plates/spot-free/render?revision=1&format=spotsep',status=409)
                free['document']['background']['r']=0.8
                call('PUT','/v1/plates/spot-free',{'expected_revision':1,'document':free['document']},status=200)
                na=call('POST','/v1/plates/spot-free/render?revision=2&format=spotsep',status=201)
                nm=json.loads(call('GET','/v1/artifacts/'+na['id'],raw=True)[0])
                for ref in nm['artifacts']:
                    call('GET','/v1/artifacts/'+ref['artifact_id'],raw=True)
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
