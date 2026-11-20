#!/usr/bin/env python3
"""Run bounded production HTTP journeys against an isolated ephemeral service."""
import argparse, concurrent.futures, copy, hashlib, json, os, pathlib, signal, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request, zlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
def decode_png(raw):
    assert raw[:8]==b'\x89PNG\r\n\x1a\n'
    pos=8;w=h=None;ctype=None;idat=b''
    while pos<len(raw):
        ln,=struct.unpack('>I',raw[pos:pos+4]);typ=raw[pos+4:pos+8];data=raw[pos+8:pos+8+ln];pos+=12+ln
        if typ==b'IHDR':
            w,h,bd,ctype,_,_,inter=struct.unpack('>IIBBBBB',data);assert bd==8 and inter==0
        elif typ==b'IDAT':idat+=data
        elif typ==b'IEND':break
    ch={0:1,2:3,6:4}[ctype];stride=w*ch;rawimg=zlib.decompress(idat);out=bytearray(h*stride);prev=bytearray(stride);p=0
    for y in range(h):
        f=rawimg[p];p+=1;row=bytearray(rawimg[p:p+stride]);p+=stride
        for i in range(stride):
            a=row[i-ch] if i>=ch else 0;b=prev[i];c=prev[i-ch] if i>=ch else 0
            if f==1:row[i]=(row[i]+a)&255
            elif f==2:row[i]=(row[i]+b)&255
            elif f==3:row[i]=(row[i]+(a+b)//2)&255
            elif f==4:
                q=a+b-c;pa,pb,pc=abs(q-a),abs(q-b),abs(q-c)
                row[i]=(row[i]+(a if pa<=pb and pa<=pc else (b if pb<=pc else c)))&255
        out[y*stride:(y+1)*stride]=row;prev=row
    return w,h,ch,out
def minv(m):
    a,b,c,d,e,f=m;det=a*d-b*c
    return [d/det,-b/det,-c/det,a/det,(c*f-d*e)/det,(b*e-a*f)/det]
def mapy(m,x,y):return (m[0]*x+m[2]*y+m[4],m[1]*x+m[3]*y+m[5])
def lin(v):return v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4
def srgb(v):
    v=min(1,max(0,v))
    return v*12.92 if v<=0.0031308 else 1.055*v**(1/2.4)-0.055
def over_px(dst,src):
    a=src[3]
    return tuple(s+d*(1-a) for s,d in zip(src,dst))[:3]+(a+dst[3]*(1-a),)
def premul(c):return (lin(c['r'])*c['a'],lin(c['g'])*c['a'],lin(c['b'])*c['a'],c['a'])
def rgba8(p):
    a=p[3]
    return tuple(int(srgb(c/a)*255+0.5) for c in p[:3])+(int(min(1,a)*255+0.5),)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workflow',required=True);args=parser.parse_args()
    seed=json.loads((ROOT/'examples/amber-proof.json').read_text())
    if args.workflow=='pattern':seed=json.loads((ROOT/'examples/twill-dots.json').read_text())
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
            elif w=='pattern':
                doc=seed['document'];W,H=doc['width'],doc['height']
                def render_td(fmt,rev=1):return call('POST',f'/v1/plates/twill-dots/render?revision={rev}&format={fmt}',status=201)
                a1=render_td('png');raw=call('GET','/v1/artifacts/'+a1['id'],raw=True)[0]
                pw,ph,ch,img=decode_png(raw);assert (pw,ph)==(W,H)
                def px(x,y):
                    i=(y*pw+x)*ch;v=tuple(img[i:i+ch])
                    return v if ch==4 else v+(255,)
                shapes={s['id']:s for l in doc['layers'] for s in l['shapes']}
                def interior(sid,margin):
                    s=shapes[sid];inv=minv(s['transform'])
                    pts=[p for o in s['path'] for p in o.get('points',[])]
                    xs=[p['x'] for p in pts];ys=[p['y'] for p in pts];out=set()
                    for y in range(H):
                        for x in range(W):
                            lx,ly=mapy(inv,x+0.5,y+0.5)
                            if min(xs)+margin<=lx<=max(xs)-margin and min(ys)+margin<=ly<=max(ys)-margin:out.add((x,y))
                    return out
                dots=interior('dot-panel',2);twill=interior('twill-panel',2)
                assert len(dots)>2000 and len(twill)>2000
                # canvas space: exact 18px periodicity on canvas axes despite the rotated shape
                n=0
                for x,y in dots:
                    if (x+18,y) in dots:assert px(x,y)==px(x+18,y);n+=1
                    if (x,y+18) in dots:assert px(x,y)==px(x,y+18);n+=1
                assert n>4000
                # object space: twill follows the rotated shape and is not canvas-locked
                assert any(px(x,y)!=px(x+16,y) for x,y in twill if (x+16,y) in twill)
                # seam probe: dot centres sit exactly on tile corners (phase 0.5,0.5)
                cands=[(x,y)for x in range(0,W,18)for y in range(0,H,18)if(x,y)in interior('dot-panel',6)]
                assert cands
                probe=next(p for p in cands if(p[0]+9,p[1]+9)in interior('dot-panel',1))
                bg=doc['background'];cell=shapes['dot-panel']['brush']['pattern']['cells'][0]['color']
                single=rgba8(over_px(premul(bg),premul(cell)));double=rgba8(over_px(over_px(premul(bg),premul(cell)),premul(cell)))
                got=px(*probe)
                assert all(abs(g-e)<=1 for g,e in zip(got,single)),('seam colour',got,single)
                assert any(abs(g-e)>1 for g,e in zip(got,double)),('double blend at seam',got,double)
                x0,y0=probe
                assert px(x0-1,y0)==px(x0+1,y0) and px(x0,y0-1)==px(x0,y0+1)
                gap=px(x0+9,y0+9);back=rgba8(premul(bg))
                assert all(abs(g-e)<=1 for g,e in zip(gap,back)),('gap colour',gap,back)
                # SVG expresses the same period and phase without tile expansion
                s1=render_td('svg');svg=call('GET','/v1/artifacts/'+s1['id'],raw=True)[0].decode()
                pats=[seg.split('</pattern>')[0] for seg in svg.split('<pattern')[1:]]
                assert len(pats)==2
                obj=[p for p in pats if 'width="16"' in p];cvs=[p for p in pats if 'width="18"' in p]
                assert len(obj)==1 and len(cvs)==1
                assert 'height="16"' in obj[0] and 'x="0" y="0"' in obj[0] and 'patternTransform' not in obj[0]
                assert 'height="18"' in cvs[0] and 'x="0.5" y="0.5"' in cvs[0] and 'patternTransform="matrix(' in cvs[0]
                assert obj[0].count('<path')==1 and cvs[0].count('<path')==4
                assert 'translate(18.5 18.5)' in cvs[0]
                assert render_td('png')['digest']==a1['digest'] and render_td('svg')['digest']==s1['digest']
                # tiny but legal tiles render without expansion; degenerate ones are refused
                def rect(x0,y0,x1,y1):return [{'kind':'M','points':[{'x':x0,'y':y0}]},{'kind':'L','points':[{'x':x1,'y':y0}]},{'kind':'L','points':[{'x':x1,'y':y1}]},{'kind':'L','points':[{'x':x0,'y':y1}]},{'kind':'Z'}]
                tinycell={'fill_rule':'nonzero','color':{'r':1,'g':1,'b':1,'a':0.4},'path':rect(0,0,0.25,0.5)}
                tinybrush={'kind':'pattern','pattern':{'space':'canvas','period':{'x':0.5,'y':0.5},'phase':{'x':0,'y':0},'cells':[tinycell]}}
                tinyshape={'id':'panel','fill_rule':'nonzero','path':rect(4,4,60,60),'transform':[1,0,0,1,0,0],'brush':tinybrush}
                tiny={'id':'tiny-tile','title':'微小单元','document':{'width':64,'height':64,'background':bg,'layers':[{'id':'sheet','opacity':1,'blend':'normal','shapes':[tinyshape]}]}}
                call('POST','/v1/plates',tiny,status=201)
                t=call('POST','/v1/plates/tiny-tile/render?revision=1&format=png',status=201)
                assert call('GET','/v1/artifacts/'+t['id'],raw=True)[0][:8]==b'\x89PNG\r\n\x1a\n'
                def refused(mutate):
                    q=copy.deepcopy(tiny);q['id']='tiny-reject';mutate(q);call('POST','/v1/plates',q,status=400)
                def pat(q):return q['document']['layers'][0]['shapes'][0]['brush']['pattern']
                refused(lambda q:pat(q).update(period={'x':0,'y':0.5}))
                refused(lambda q:pat(q).update(period={'x':0.49,'y':0.49}))
                refused(lambda q:pat(q)['cells'][0].update(path=rect(0,0,0.75,0.5)))
                refused(lambda q:pat(q)['cells'][0].update(path=rect(-0.6,0,-0.2,0.5)))
                refused(lambda q:pat(q).update(space='viewport'))
                refused(lambda q:pat(q).update(cells=[]))
                refused(lambda q:q['document']['layers'][0]['shapes'][0]['brush'].update(kind='solid',color={'r':1,'g':1,'b':1,'a':1}))
                # revisions keep history immutable; bundles carry patterns offline
                doc2=copy.deepcopy(doc);doc2['layers'][1]['shapes'][0]['brush']['pattern']['phase']={'x':2.5,'y':0.5}
                call('PUT','/v1/plates/twill-dots',{'expected_revision':1,'document':doc2})
                a2=render_td('png',2);assert a2['digest']!=a1['digest']
                assert render_td('png',1)['digest']==a1['digest']
                sealed=call('GET','/v1/plates/twill-dots/bundle',raw=True)[0]
                stop();os.rename(base/'state',base/'original');start()
                call('POST','/v1/bundles',sealed,status=201)
                assert render_td('png',1)['digest']==a1['digest'] and render_td('png',2)['digest']==a2['digest']
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
