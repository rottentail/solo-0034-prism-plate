#!/usr/bin/env python3
"""Run bounded production HTTP journeys against an isolated ephemeral service."""
import argparse, concurrent.futures, copy, hashlib, json, os, pathlib, signal, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request, zlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
def decode_png(raw):
    assert raw[:8]==b'\x89PNG\r\n\x1a\n'
    pos,w,h,idat=8,None,None,b''
    while pos<len(raw):
        ln,=struct.unpack('>I',raw[pos:pos+4]);typ,data=raw[pos+4:pos+8],raw[pos+8:pos+8+ln]
        if typ==b'IHDR':
            w,h,depth,ctype,_,_,inter=struct.unpack('>IIBBBBB',data);assert depth==8 and ctype==6 and inter==0
        elif typ==b'IDAT':idat+=data
        elif typ==b'IEND':break
        pos+=12+ln
    buf=zlib.decompress(idat);stride=w*4;out=bytearray(h*stride);prev=bytearray(stride);p=0
    for y in range(h):
        f=buf[p];p+=1;line=bytearray(buf[p:p+stride]);p+=stride
        if f==1:
            for i in range(4,stride):line[i]=(line[i]+line[i-4])&255
        elif f==2:
            for i in range(stride):line[i]=(line[i]+prev[i])&255
        elif f==3:
            for i in range(stride):line[i]=(line[i]+(((line[i-4] if i>=4 else 0)+prev[i])>>1))&255
        elif f==4:
            for i in range(stride):
                a=line[i-4] if i>=4 else 0;b=prev[i];c=prev[i-4] if i>=4 else 0;pp=a+b-c;pa,pb,pc=abs(pp-a),abs(pp-b),abs(pp-c)
                line[i]=(line[i]+(a if pa<=pb and pa<=pc else (b if pb<=pc else c)))&255
        else:assert f==0
        out[y*stride:(y+1)*stride]=line;prev=line
    return w,h,bytes(out)
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
            elif w=='mesh':
                mseed=json.loads((ROOT/'examples/prism-mesh.json').read_text());call('POST','/v1/plates',mseed,status=201)
                def mrender(fmt='png'):return call('POST',f'/v1/plates/prism-mesh/render?revision=1&format={fmt}',status=201)
                a=mrender();assert mrender()['digest']==a['digest']
                raw,_=call('GET','/v1/artifacts/'+a['id'],raw=True);assert hashlib.sha256(raw).hexdigest()==a['digest']
                w0,h0,px=decode_png(raw);assert (w0,h0)==(160,120)
                def at(x,y):
                    o=(y*w0+x)*4;return tuple(px[o:o+4])
                assert at(111,83)[3]==0 and at(40,114)[3]==0 and at(2,2)[3]==0
                assert 100<=at(79,67)[3]<=160
                r,g,b,_=at(15,17);assert r>=240 and g<=140 and b<=120
                r,g,b,_=at(142,109);assert b>=200 and r<=130
                assert at(139,14)[3]>=250
                jump,minalpha=0,255
                for y in range(16,105):
                    for x in range(14,139):
                        if 94<=x<=130 and 66<=y<=101:continue
                        av=at(x,y)[3];minalpha=min(minalpha,av)
                        if x+1<=138 and not(94<=x+1<=130 and 66<=y<=101):jump=max(jump,abs(av-at(x+1,y)[3]))
                        if y+1<=104 and not(94<=x<=130 and 66<=y+1<=101):jump=max(jump,abs(av-at(x,y+1)[3]))
                assert minalpha>=100 and jump<=40,(minalpha,jump)
                s=mrender('svg');assert mrender('svg')['digest']==s['digest']
                sraw,_=call('GET','/v1/artifacts/'+s['id'],raw=True)
                assert b'<image' in sraw and b'data:image/png;base64,' in sraw and b'clip-rule="evenodd"' in sraw and b'<desc>' in sraw
                def bad(fn):
                    p=copy.deepcopy(mseed);p['id']='mesh-bad';fn(p['document']['layers'][0]['shapes'][0]['brush']);call('POST','/v1/plates',p,status=400)
                bad(lambda b:b['mesh'].__setitem__('triangles',b['mesh']['triangles']+[{'a':0,'b':1,'c':4}]))
                bad(lambda b:b['mesh']['triangles'][0].__setitem__('c',9))
                bad(lambda b:b['mesh']['vertices'][0].__setitem__('x',2e6))
                bad(lambda b:b['mesh']['vertices'][0]['color'].__setitem__('a',2))
                bad(lambda b:b.pop('mesh'))
                bad(lambda b:b['mesh'].__setitem__('vertices',b['mesh']['vertices'][:2]))
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
