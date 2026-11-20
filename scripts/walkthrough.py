#!/usr/bin/env python3
"""Run bounded production HTTP journeys against an isolated ephemeral service."""
import argparse, concurrent.futures, copy, hashlib, json, os, pathlib, signal, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]

# --- Sample-target gradient correction support (workflow: correction) --------
def _srgb_to_lin(v):
    return v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4
def _lin_to_srgb(v):
    v=min(1.0,max(0.0,v))
    return v*12.92 if v<=0.0031308 else 1.055*v**(1/2.4)-0.055
def _rect(x0,y0,x1,y1):
    return [{'kind':'M','points':[{'x':x0,'y':y0}]},
            {'kind':'L','points':[{'x':x1,'y':y0}]},
            {'kind':'L','points':[{'x':x1,'y':y1}]},
            {'kind':'L','points':[{'x':x0,'y':y1}]},{'kind':'Z'}]
def _stop(at,r,g,b,a=1.0):
    return {'at':at,'color':{'r':r,'g':g,'b':b,'a':a}}
def _linear(stops,frm=(10,10),to=(118,10)):
    return {'kind':'linear','from':{'x':frm[0],'y':frm[1]},'to':{'x':to[0],'y':to[1]},
            'color':{'r':0,'g':0,'b':0,'a':0},'stops':stops}
def _premul(col):
    a=col['a']
    return tuple(_srgb_to_lin(col[k])*a for k in ('r','g','b'))+(a,)
def _over_normal(dst,src):
    sr,sg,sb,sa=src;dr,dg,db,da=dst
    return (sr+dr*(1-sa),sg+dg*(1-sa),sb+db*(1-sa),sa+da*(1-sa))
def _over_blend(dst,src,mode):
    sr,sg,sb,sa=src;dr,dg,db,da=dst
    if mode=='normal' or sa==0 or da==0:return _over_normal(dst,src)
    def ch(d,s):
        a,b=d/da,s/sa
        v=a*b if mode=='multiply' else a+b-a*b
        return (1-sa)*d+(1-da)*s+sa*da*v
    return (ch(dr,sr),ch(dg,sg),ch(db,sb),sa+da*(1-sa))
def _in_rect(p,b):
    x,y=p;return b[0]<=x<=b[2] and b[1]<=y<=b[3]
def _bounds(path,start=0):
    return (path[start]['points'][0]['x'],path[start]['points'][0]['y'],
            path[start+2]['points'][0]['x'],path[start+2]['points'][0]['y'])
def _sample(doc,x,y,bar_stops):
    """One subsample through the layer stack in premultiplied linear light."""
    dst=_premul(doc['background'])
    for layer in doc['layers']:
        if layer.get('mask') and not _in_rect((x,y),_bounds(layer['mask'])):continue
        src=(0.0,0.0,0.0,0.0)
        for shape in layer['shapes']:
            if not _in_rect((x,y),_bounds(shape['path'])):continue
            if shape['fill_rule']=='evenodd' and _in_rect((x,y),_bounds(shape['path'],5)):continue
            if shape['brush']['kind']=='solid':
                px=_premul(shape['brush']['color'])
            else:
                b=shape['brush'];fx,fy=b['from']['x'],b['from']['y'];tx,ty=b['to']['x'],b['to']['y']
                t=min(1.0,max(0.0,((x-fx)*(tx-fx)+(y-fy)*(ty-fy))/((tx-fx)**2+(ty-fy)**2)))
                stops=bar_stops if shape['id']=='bar' else b['stops']
                for i in range(1,len(stops)):
                    if t<=stops[i]['at']:
                        u=(t-stops[i-1]['at'])/(stops[i]['at']-stops[i-1]['at'])
                        pa,pb=_premul(stops[i-1]['color']),_premul(stops[i]['color'])
                        px=tuple(pa[k]+(pb[k]-pa[k])*u for k in range(4));break
                else:px=_premul(stops[-1]['color'])
            src=_over_normal(src,px)
        op=layer['opacity']
        dst=_over_blend(dst,(src[0]*op,src[1]*op,src[2]*op,src[3]*op),layer['blend'])
    r,g,b,a=dst
    return (0.0,0.0,0.0,0.0) if a<=0 else (_lin_to_srgb(r/a),_lin_to_srgb(g/a),_lin_to_srgb(b/a),a)
def _reference(doc,x,y,bar_stops):
    """Four-subsample average matching raster.Render, returning visible sRGB."""
    acc=[0.0,0.0,0.0,0.0]
    for sy in range(2):
        for sx in range(2):
            s=_sample(doc,x+(sx+0.5)/2,y+(sy+0.5)/2,bar_stops)
            for k in range(4):acc[k]+=s[k]/4
    return tuple(acc)
def _decode_png(b):
    import zlib
    pos=8;idat=b'';width=height=colt=None
    while pos<len(b):
        ln=struct.unpack('>I',b[pos:pos+4])[0];ctype=b[pos+4:pos+8];chunk=b[pos+8:pos+8+ln]
        if ctype==b'IHDR':width,height,_,colt=struct.unpack('>IIBB',chunk[:10])
        elif ctype==b'IDAT':idat+=chunk
        pos+=12+ln
    raw=zlib.decompress(idat);ch={6:4,2:3,0:1}[colt];stride=width*ch
    out=bytearray();prev=bytearray(stride);i=0
    for _ in range(height):
        ft=raw[i];line=bytearray(raw[i+1:i+1+stride]);i+=1+stride
        for x in range(stride):
            a=line[x-ch] if x>=ch else 0;bb=prev[x];cc=prev[x-ch] if x>=ch else 0;v=line[x]
            if ft==1:v=(v+a)&255
            elif ft==2:v=(v+bb)&255
            elif ft==3:v=(v+((a+bb)>>1))&255
            elif ft==4:
                pp=a+bb-cc;pa,pb,pc=abs(pp-a),abs(pp-bb),abs(pp-cc)
                pr=a if pa<=pb and pa<=pc else (bb if pb<=pc else cc);v=(v+pr)&255
            line[x]=v
        out+=line;prev=line
    return width,ch,out
def _png_px(b,x,y):
    w,ch,data=_decode_png(b);off=(y*w+x)*ch;return tuple(data[off:off+ch])

def correction_workflow(call, render):
    studio_render=lambda rev:call('POST',f'/v1/plates/studio-plate/render?revision={rev}&format=png',status=201)
    init=[_stop(0,0.30,0.90,0.40),_stop(1,0.80,0.70,0.20)]
    want=[_stop(0,0.95,0.20,0.10),_stop(1,0.05,0.35,0.90)]
    bar_path=_rect(10,10,118,40)+_rect(16,12,28,18)
    veil={'id':'veil-layer','opacity':0.6,'blend':'normal','shapes':[
        {'id':'veil','fill_rule':'nonzero','path':_rect(56,10,78,40),'transform':[1,0,0,1,0,0],
         'brush':{'kind':'solid','color':{'r':0.4,'g':0.45,'b':0.5,'a':0.7}}}]}
    bar={'id':'bar-layer','opacity':1,'blend':'normal','shapes':[
        {'id':'bar','fill_rule':'evenodd','path':bar_path,'transform':[1,0,0,1,0,0],
         'brush':_linear(init)}]}
    cap={'id':'cap-layer','opacity':1,'blend':'normal','mask':_rect(95,10,118,40),'shapes':[
        {'id':'cap','fill_rule':'nonzero','path':_rect(100,10,118,40),'transform':[1,0,0,1,0,0],
         'brush':_linear([_stop(0,1,1,1),_stop(1,1,1,1)],frm=(100,10),to=(118,10))}]}
    plate={'id':'studio-plate','title':'校色试片','document':{'width':128,'height':96,
        'background':{'r':0.02,'g':0.02,'b':0.03,'a':1},'layers':[veil,bar,cap]}}
    call('POST','/v1/plates',plate,201)
    # 1. Visibility rejections: evenodd hole, outside shape, full occlusion.
    rej=call('POST','/v1/plates/studio-plate/corrections/probe?revision=1',
        {'layer_id':'bar-layer','shape_id':'bar','targets':[
            {'x':22,'y':15,'color':{'r':1,'g':1,'b':1,'a':1}},
            {'x':5,'y':20,'color':{'r':1,'g':1,'b':1,'a':1}},
            {'x':109,'y':26,'color':{'r':1,'g':1,'b':1,'a':1}}]})
    reasons={r['index']:r['reason'] for r in rej['rejected_targets']}
    assert rej['usable_targets']==0 and reasons[0]=='hole' and reasons[1]=='outside_shape' and reasons[2]=='occluded',reasons
    masked=call('POST','/v1/plates/studio-plate/corrections/probe?revision=1',
        {'layer_id':'cap-layer','shape_id':'cap','targets':[{'x':30,'y':26,'color':{'r':1,'g':1,'b':1,'a':1}}]})
    assert masked['rejected_targets'][0]['reason']=='layer_masked_or_clipped'
    # 2. Exact recovery: targets derived from an independent reference compositor.
    targets=[]
    for x in (25,67,91):
        vis=_reference(plate['document'],x,25,want)
        targets.append({'x':x,'y':25,'color':{'r':vis[0],'g':vis[1],'b':vis[2],'a':1}})
    probe=call('POST','/v1/plates/studio-plate/corrections/probe?revision=1',
        {'layer_id':'bar-layer','shape_id':'bar','targets':targets})
    assert probe['usable_targets']==3,probe
    cand=next(c for c in probe['candidates'] if c['mode']=='colors_only')
    assert len(cand['stops'])==2 and [s['at'] for s in cand['stops']]==[0,1]
    assert all(abs(s['color']['a']-1.0)<1e-12 for s in cand['stops'])
    for got,w in zip(cand['stops'],want):
        for k in ('r','g','b'):assert abs(got['color'][k]-w['color'][k])<2e-3,(got,w)
    assert cand['weighted_linear_rmse']<1e-4
    assert all(d['png_max_abs_8bit']<=1 for d in cand['deviations'])
    # 3. Commit appends a revision; axis/alpha/stop-count kept; history immutable.
    old=studio_render(1)
    committed=call('POST','/v1/plates/studio-plate/corrections/commit',
        {'expected_revision':1,'layer_id':'bar-layer','shape_id':'bar','mode':'colors_only','targets':targets},201)
    after=committed['plate'];assert len(after['revisions'])==2
    assert after['revisions'][0]['digest']!=after['revisions'][1]['digest']
    nb=after['revisions'][1]['document']['layers'][1]['shapes'][0]['brush']
    assert nb['from']==_linear(init)['from'] and nb['to']==_linear(init)['to']
    call('POST','/v1/plates/studio-plate/corrections/commit',
        {'expected_revision':1,'layer_id':'bar-layer','shape_id':'bar','mode':'colors_only','targets':targets},409)
    new=studio_render(2)
    old_png=call('GET','/v1/artifacts/'+old['id'],raw=True)[0]
    new_png=call('GET','/v1/artifacts/'+new['id'],raw=True)[0]
    assert old['digest']!=new['digest']
    assert studio_render(1)['digest']==old['digest']  # old output byte-stable
    for t in targets:
        px=_png_px(new_png,t['x'],t['y']);w8=tuple(round(t['color'][k]*255) for k in ('r','g','b'))
        assert max(abs(px[i]-w8[i]) for i in range(3))<=1,(px,w8)
    # 4. Non-unique fit: 3-stop gradient, position mode reduces error.
    three=[_stop(0,0.2,0.2,0.2),_stop(0.5,0.5,0.5,0.5),_stop(1,0.8,0.8,0.8)]
    p2=copy.deepcopy(plate);p2['id']='three-stop-plate'
    p2['document']['layers'][1]['shapes'][0]['brush']=_linear(three)
    call('POST','/v1/plates',p2,201)
    tt=[{'x':x,'y':25,'color':{'r':r,'g':g,'b':b,'a':1}} for x,(r,g,b) in
        ((20,(0.9,0.1,0.2)),(40,(0.7,0.2,0.6)),(85,(0.2,0.6,0.7)),(90,(0.1,0.2,0.95)))]
    p3=call('POST','/v1/plates/three-stop-plate/corrections/probe?revision=1',
        {'layer_id':'bar-layer','shape_id':'bar','targets':tt})
    c_co=next(c for c in p3['candidates'] if c['mode']=='colors_only')
    c_cp=next(c for c in p3['candidates'] if c['mode']=='colors_and_positions')
    assert len(c_cp['stops'])==3 and c_cp['stops'][0]['at']==0 and c_cp['stops'][2]['at']==1
    if c_cp['weighted_linear_rmse']<c_co['weighted_linear_rmse']-1e-5:
        assert p3['recommended_mode']=='colors_and_positions' and c_cp['stop_position_drift']>0
    else:
        assert p3['recommended_mode']=='colors_only'
    # 5. Incompatible simultaneous targets are flagged, not silently averaged away.
    clash=[{'x':64,'y':25,'color':{'r':0.95,'g':0.1,'b':0.1,'a':1}},
           {'x':64,'y':25,'color':{'r':0.1,'g':0.9,'b':0.1,'a':1}}]
    cp=call('POST','/v1/plates/studio-plate/corrections/probe?revision=2',
        {'layer_id':'bar-layer','shape_id':'bar','targets':clash})
    assert sorted(cp['candidates'][0]['conflict_indexes'])==[0,1]
    # 6. Admission rejections and lifecycle errors.
    call('POST','/v1/plates/studio-plate/corrections/probe?revision=2',
        {'layer_id':'bar-layer','shape_id':'bar','targets':[]},400)
    call('POST','/v1/plates/studio-plate/corrections/probe?revision=2',
        {'layer_id':'bar-layer','shape_id':'bar','targets':[{'x':30,'y':25,'color':{'r':2,'g':0,'b':0,'a':1}}]},400)
    call('POST','/v1/plates/studio-plate/corrections/commit',
        {'expected_revision':2,'layer_id':'bar-layer','shape_id':'bar','mode':'nonsense','targets':targets},400)
    call('POST','/v1/plates/absent/corrections/probe?revision=1',
        {'layer_id':'bar-layer','shape_id':'bar','targets':targets},404)

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
            elif w=='correction':
                correction_workflow(call, render)
            else:raise RuntimeError('unknown workflow '+w)
            print(json.dumps({'workflow':w,'result':'passed','entry':'HTTP','port':port},ensure_ascii=False))
        finally:stop();log.close()
if __name__=='__main__':main()
