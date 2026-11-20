#!/usr/bin/env python3
"""Run bounded production HTTP journeys against an isolated ephemeral service."""
import argparse, concurrent.futures, copy, hashlib, json, os, pathlib, signal, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request, zlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
def png_pixels(raw):
    """Decode an 8-bit RGB/RGBA PNG (the service encoder contract) to (w,h,RGBA pixels)."""
    assert raw[:8]==b'\x89PNG\r\n\x1a\n';pos=8;w=h=None;data=b''
    while pos<len(raw):
        ln=struct.unpack('>I',raw[pos:pos+4])[0];typ=raw[pos+4:pos+8];chunk=raw[pos+8:pos+8+ln];pos+=12+ln
        if typ==b'IHDR':
            w,h,depth,ctype,_,_,inter=struct.unpack('>IIBBBBB',chunk);assert depth==8 and ctype in (2,6) and inter==0
        elif typ==b'IDAT':data+=chunk
        elif typ==b'IEND':break
    bpp=4 if ctype==6 else 3
    scan=zlib.decompress(data);stride=w*bpp;out=bytearray();prev=bytearray(stride);i=0
    for _ in range(h):
        f=scan[i];i+=1;row=bytearray(scan[i:i+stride]);i+=stride
        if f==1:
            for x in range(bpp,stride):row[x]=(row[x]+row[x-bpp])&255
        elif f==2:
            for x in range(stride):row[x]=(row[x]+prev[x])&255
        elif f==3:
            for x in range(stride):row[x]=(row[x]+((row[x-bpp] if x>=bpp else 0)+prev[x])//2)&255
        elif f==4:
            for x in range(stride):
                a=row[x-bpp] if x>=bpp else 0;b=prev[x];c=prev[x-bpp] if x>=bpp else 0
                p=a+b-c;pa,pb,pc=abs(p-a),abs(p-b),abs(p-c)
                row[x]=(row[x]+(a if pa<=pb and pa<=pc else b if pb<=pc else c))&255
        else:assert f==0
        out+=row;prev=row
    assert i==len(scan)
    if bpp==3:
        rgba=bytearray()
        for x in range(w*h):rgba+=out[x*3:x*3+3]+b'\xff'
        out=rgba
    return w,h,bytes(out)
def px(pix,w,x,y):
    i=(y*w+x)*4;return tuple(pix[i:i+4])
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
            elif w=='size':
                orig=render();braw,_=call('GET','/v1/artifacts/'+orig['id'],raw=True);bw,bh,bpix=png_pixels(braw);assert (bw,bh)==(192,144)
                # contain: 4:3 document letterboxed into a 16:9 frame, transparent side bars, content pixel-identical
                c1=call('POST','/v1/plates/amber-plate/render?revision=1&format=png&width=256&height=144&fit=contain',status=201)
                assert c1['target']=={'width':256,'height':144,'fit':'contain'} and c1['id']!=orig['id'] and c1['scene_digest']==orig['scene_digest']
                craw,chs=call('GET','/v1/artifacts/'+c1['id'],raw=True);assert chs['x-render-target']=='256x144:contain'
                cw,ch,cpix=png_pixels(craw);assert (cw,ch)==(256,144)
                assert px(cpix,cw,0,0)[3]==0 and px(cpix,cw,255,143)[3]==0 and px(cpix,cw,31,71)[3]==0 and px(cpix,cw,32,71)[3]==255
                assert all(px(cpix,cw,32+x,y)==px(bpix,bw,x,y) for x in range(192) for y in range(144))
                assert call('POST','/v1/plates/amber-plate/render?revision=1&format=png&width=256&height=144&fit=contain',status=201)['id']==c1['id']
                # cover: 4:3 document cropped into a 1:1 frame, fully covered, centre crop pixel-identical
                c2=call('POST','/v1/plates/amber-plate/render?revision=1&format=png&width=144&height=144&fit=cover',status=201)
                kraw,_=call('GET','/v1/artifacts/'+c2['id'],raw=True);kw,kh,kpix=png_pixels(kraw);assert (kw,kh)==(144,144)
                assert all(px(kpix,kw,x,y)==px(bpix,bw,x+24,y) for x in range(144) for y in range(144))
                assert all(px(kpix,kw,x,y)[3]==255 for x in range(144) for y in range(144))
                # matching aspect at 2x: contain and cover coincide in content but keep distinct provenance
                u1=call('POST','/v1/plates/amber-plate/render?revision=1&format=png&width=384&height=288&fit=contain',status=201)
                u2=call('POST','/v1/plates/amber-plate/render?revision=1&format=png&width=384&height=288&fit=cover',status=201)
                assert u1['digest']==u2['digest'] and u1['id']!=u2['id']
                # sized svg shares the same layout transform
                s1=call('POST','/v1/plates/amber-plate/render?revision=1&format=svg&width=256&height=144&fit=contain',status=201)
                sraw,_=call('GET','/v1/artifacts/'+s1['id'],raw=True)
                assert b'width="256" height="144" viewBox="0 0 256 144"' in sraw and b'matrix(1 0 0 1 32 0)' in sraw
                # async exposure honours the same target and dedupes to the same artifact
                job=call('POST','/v1/plates/amber-plate/exposures?revision=1&format=png&width=384&height=288&fit=contain',status=202)
                for _ in range(200):
                    job=call('GET','/v1/exposures/'+job['id'])
                    if job['state'] in ('succeeded','failed'):break
                    time.sleep(.02)
                assert job['state']=='succeeded' and job['artifact']==u1['id']
                # layout-proof plate: gradient wash, clipped band, evenodd cutout under one shared layout
                def rect(x0,y0,x1,y1):
                    return [{'kind':'M','points':[{'x':x0,'y':y0}]},{'kind':'L','points':[{'x':x1,'y':y0}]},{'kind':'L','points':[{'x':x1,'y':y1}]},{'kind':'L','points':[{'x':x0,'y':y1}]},{'kind':'Z'}]
                ident=[1,0,0,1,0,0]
                doc={'id':'layout-proof','title':'版式适配证明','document':{'width':100,'height':50,'background':{'r':0.02,'g':0.02,'b':0.05,'a':1},'layers':[
                    {'id':'wash','opacity':1,'blend':'normal','shapes':[{'id':'grad','fill_rule':'nonzero','path':rect(0,0,100,50),'transform':ident,'brush':{'kind':'linear','from':{'x':0,'y':0},'to':{'x':100,'y':0},'color':{'r':0,'g':0,'b':0,'a':0},'stops':[{'at':0,'color':{'r':0.1,'g':0.2,'b':0.9,'a':1}},{'at':1,'color':{'r':0.95,'g':0.85,'b':0.2,'a':1}}]}}]},
                    {'id':'marks','opacity':1,'blend':'normal','clip':rect(10,5,90,45),'shapes':[
                        {'id':'band','fill_rule':'nonzero','path':rect(5,10,95,40),'transform':ident,'brush':{'kind':'solid','color':{'r':1,'g':0,'b':0,'a':1}}},
                        {'id':'window','fill_rule':'evenodd','path':rect(30,15,70,35)+rect(45,20,55,30),'transform':ident,'brush':{'kind':'solid','color':{'r':0,'g':0.8,'b':0.87,'a':1}}}]}]}}
                call('POST','/v1/plates',doc,status=201)
                uns=call('POST','/v1/plates/layout-proof/render?revision=1&format=png',status=201)
                same=call('POST','/v1/plates/layout-proof/render?revision=1&format=png&width=100&height=50&fit=contain',status=201)
                assert same['digest']==uns['digest'] and same['id']!=uns['id']
                uw,uh,upix=png_pixels(call('GET','/v1/artifacts/'+uns['id'],raw=True)[0])
                def sized(w,h,fit):
                    a=call('POST',f'/v1/plates/layout-proof/render?revision=1&format=png&width={w}&height={h}&fit={fit}',status=201)
                    return png_pixels(call('GET','/v1/artifacts/'+a['id'],raw=True)[0])
                red=(255,0,0,255);cyan=(0,204,222,255)
                # letterbox 2:1 into 1:1: transparent margins, cutout/clip/brush at doubled coordinates
                cw,ch,cpix=sized(200,200,'contain')
                assert px(cpix,cw,0,0)[3]==0 and px(cpix,cw,199,199)[3]==0 and px(cpix,cw,100,25)[3]==0 and px(cpix,cw,100,175)[3]==0
                assert px(cpix,cw,100,100)==red and px(upix,uw,50,25)==red
                assert px(cpix,cw,88,100)==cyan and px(cpix,cw,112,100)==cyan and px(cpix,cw,120,100)==cyan and px(upix,uw,60,25)==cyan
                assert px(cpix,cw,160,100)==red and px(cpix,cw,179,100)==red
                assert px(cpix,cw,181,100)!=red and px(cpix,cw,181,100)[3]==255
                assert px(cpix,cw,100,54)[0]>100 and px(cpix,cw,100,54)[2]>100
                # crop 2:1 into 1:1: no margins, same document points land at the cropped coordinates
                kw,kh,kpix=sized(200,200,'cover')
                assert all(px(kpix,kw,x,y)[3]==255 for x in range(0,200,7) for y in range(0,200,7))
                assert px(kpix,kw,100,100)==red and px(kpix,kw,70,100)==cyan and px(kpix,kw,130,100)==cyan
                assert px(kpix,kw,140,100)==px(cpix,cw,120,100) and px(kpix,kw,180,100)==red
                assert px(kpix,kw,20,8)[2]>px(kpix,kw,180,8)[2] and px(kpix,kw,20,8)[0]<px(kpix,kw,180,8)[0]
                # admission and budget: output pixels share the same bounded work allowance
                call('POST','/v1/plates/amber-plate/render?revision=1&format=png&width=1024&height=1024&fit=contain',status=400)
                call('POST','/v1/plates/amber-plate/render?revision=1&format=png&width=1025&height=64&fit=contain',status=400)
                call('POST','/v1/plates/amber-plate/render?revision=1&format=png&width=256&fit=contain',status=400)
                call('POST','/v1/plates/amber-plate/render?revision=1&format=png&height=144',status=400)
                call('POST','/v1/plates/amber-plate/render?revision=1&format=png&width=256&height=144&fit=stretch',status=400)
                call('POST','/v1/plates/amber-plate/render?revision=1&format=png&width=0&height=144&fit=contain',status=400)
                big=call('POST','/v1/plates/amber-plate/render?revision=1&format=png&width=1024&height=768&fit=cover',status=201)
                assert png_pixels(call('GET','/v1/artifacts/'+big['id'],raw=True)[0])[:2]==(1024,768)
                # old proofs keep reading the original artifact; sized lineage survives a restart
                call('POST','/v1/proofs',{'id':'proof-base','artifact':orig['id']},status=201)
                assert call('PUT','/v1/proofs/proof-base',{'state':'accepted','note':'原件校样通过'})['state']=='accepted'
                call('DELETE','/v1/artifacts/'+orig['id'],status=409)
                stop();start()
                assert hashlib.sha256(call('GET','/v1/artifacts/'+orig['id'],raw=True)[0]).hexdigest()==orig['digest']
                assert hashlib.sha256(call('GET','/v1/artifacts/'+c1['id'],raw=True)[0]).hexdigest()==c1['digest']
                assert call('POST','/v1/plates/amber-plate/render?revision=1&format=png&width=256&height=144&fit=contain',status=201)['id']==c1['id']
                assert render()['id']==orig['id']
            elif w=='rejection':
                bad=copy.deepcopy(seed);bad['id']='oversize';bad['document']['width']=513;call('POST','/v1/plates',bad,status=400);call('GET','/v1/plates/oversize',status=404)
                bad=copy.deepcopy(seed);bad['id']='invalid-brush';bad['document']['layers'][0]['shapes'][0]['brush']['stops'][1]['at']=0;call('POST','/v1/plates',bad,status=400);assert render()['revision']==1
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
