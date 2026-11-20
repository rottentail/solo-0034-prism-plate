#!/usr/bin/env python3
"""Run bounded production HTTP journeys against an isolated ephemeral service."""
import argparse, concurrent.futures, copy, hashlib, json, os, pathlib, signal, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request, zlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
def png_pixels(raw):
    """Decode the service's 8-bit RGB/RGBA non-interlaced PNG into rows of pixels."""
    assert raw[:8]==b'\x89PNG\r\n\x1a\n'
    pos=8;idat=b'';w=h=channels=None
    while pos<len(raw):
        ln=struct.unpack('>I',raw[pos:pos+4])[0];typ=raw[pos+4:pos+8];data=raw[pos+8:pos+8+ln];pos+=12+ln
        if typ==b'IHDR':
            w,h,depth,ctype,_,_,inter=struct.unpack('>IIBBBBB',data)
            assert depth==8 and ctype in (2,6) and inter==0;channels=3 if ctype==2 else 4
        elif typ==b'IDAT':idat+=data
        elif typ==b'IEND':break
    raw=zlib.decompress(idat);stride=w*channels;bpp=channels;rows=[];prev=bytearray(stride);i=0
    for _ in range(h):
        f=raw[i];i+=1;line=bytearray(raw[i:i+stride]);i+=stride
        if f==1:
            for x in range(bpp,stride):line[x]=(line[x]+line[x-bpp])&255
        elif f==2:
            for x in range(stride):line[x]=(line[x]+prev[x])&255
        elif f==3:
            for x in range(stride):
                left=line[x-bpp] if x>=bpp else 0;line[x]=(line[x]+((left+prev[x])>>1))&255
        elif f==4:
            for x in range(stride):
                a=line[x-bpp] if x>=bpp else 0;b=prev[x];c=prev[x-bpp] if x>=bpp else 0
                p=a+b-c;pa=abs(p-a);pb=abs(p-b);pc=abs(p-c)
                pr=a if (pa<=pb and pa<=pc) else (b if pb<=pc else c);line[x]=(line[x]+pr)&255
        elif f!=0:raise AssertionError(f'unknown PNG filter {f}')
        rows.append(bytes(line));prev=line
    return w,h,channels,rows
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
        def png_of(artifact):
            raw,_=call('GET','/v1/artifacts/'+artifact['id'],raw=True)
            assert hashlib.sha256(raw).hexdigest()==artifact['digest']
            return raw,*png_pixels(raw)
        def region_query(x,y,w,h):return f'&x={x}&y={y}&width={w}&height={h}'
        def crop(full,x,y,w,h):
            a=call('POST','/v1/plates/amber-plate/render?revision=1&format=png'+region_query(x,y,w,h),status=201)
            assert a['region']=={'x':x,'y':y,'width':w,'height':h}
            raw,cw,chh,cch,crows=png_of(a);_,fw,fh,fch,frows=full
            assert (cw,chh)==(w,h) and cch==fch
            for yy in range(h):assert crows[yy]==frows[y+yy][x*cch:(x+w)*cch],(x,y,x+w,y+yy)
            return a,raw
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
            elif w=='region':
                whole=render();full=png_of(whole);assert (full[1],full[2])==(192,144)
                for x,y,rw,rh in ((20,20,100,50),(0,0,1,1),(183,130,9,14),(8,8,176,128),(64,64,1,1),(191,143,1,1)):
                    a,raw=crop(full,x,y,rw,rh);assert a['id']!=whole['id'] and a['digest']!=''
                    assert call('GET','/v1/artifacts/'+a['id'],raw=True)[0]==raw
                alias=call('POST','/v1/plates/amber-plate/render?revision=1&format=png'+region_query(0,0,192,144),status=201)
                assert alias['id']==whole['id'] and alias.get('region') is None
                assert render()['id']==whole['id']
                job=call('POST','/v1/plates/amber-plate/exposures?revision=1&format=png'+region_query(20,20,100,50),status=202)
                for _ in range(200):
                    job=call('GET','/v1/exposures/'+job['id'])
                    if job['state'] in ('succeeded','failed','cancelled'):break
                    time.sleep(.02)
                assert job['state']=='succeeded'
                sync,_=crop(full,20,20,100,50);assert job['artifact']==sync['id']
                for q in (region_query(190,0,4,4),region_query(0,0,0,4),region_query(0,0,4,4).replace('&height=4',''),region_query(-1,0,4,4)):
                    call('POST','/v1/plates/amber-plate/render?revision=1&format=png'+q,status=400)
                    call('POST','/v1/plates/amber-plate/exposures?revision=1&format=png'+q,status=400)
                call('POST','/v1/plates/amber-plate/render?revision=1&format=svg'+region_query(0,0,4,4),status=400)
                heavy=copy.deepcopy(seed);heavy['id']='heavy-plate';heavy['title']='重场景'
                shape=heavy['document']['layers'][0]['shapes'][0]
                import math
                def ring(i):return {'x':256+180*math.cos(i*6.2832/100),'y':256+180*math.sin(i*6.2832/100)}
                shape['path']=[{'kind':'M','points':[ring(0)]}]+[{'kind':'L','points':[ring(i)]} for i in range(1,100)]+[{'kind':'Z'}]
                heavy['document']['width']=heavy['document']['height']=512
                call('POST','/v1/plates',heavy,status=201)
                def timed(path):t=time.perf_counter();v=call('POST',path,status=201);return time.perf_counter()-t,v
                whole_time,_=timed('/v1/plates/heavy-plate/render?revision=1&format=png')
                small_time,sample=timed('/v1/plates/heavy-plate/render?revision=1&format=png'+region_query(250,250,4,4))
                assert (sample['region']['width'],sample['region']['height'])==(4,4)
                assert small_time*4<whole_time,(small_time,whole_time)
                a,raw=crop(full,20,20,100,50)
                stop();start()
                assert call('GET','/v1/artifacts/'+a['id'],raw=True)[0]==raw
                restored=call('POST','/v1/plates/amber-plate/render?revision=1&format=png'+region_query(20,20,100,50),status=201)
                assert restored['id']==a['id'] and restored['region']==a['region']
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
