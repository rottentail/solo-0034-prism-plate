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
            elif w=='compare':
                def cmp(a,b):return call('GET',f'/v1/plates/amber-plate/compare?base={a}&candidate={b}')
                a1=render();raw,_=call('GET','/v1/artifacts/'+a1['id'],raw=True);assert hashlib.sha256(raw).hexdigest()==a1['digest']
                # 稿本2：对象改名、轮廓起点轮换与共线边拆分，渲染结果必须完全一致
                doc2=copy.deepcopy(seed['document'])
                for l in doc2['layers']:
                    l['id']=l['id']+'-r2'
                    for s in l['shapes']:s['id']=s['id']+'-r2'
                p=doc2['layers'][0]['shapes'][0]['path'];v0=p[0]['points'][0];c=p[1]['points']
                doc2['layers'][0]['shapes'][0]['path']=[{'kind':'M','points':[c[2]]},{'kind':'L','points':[v0]},{'kind':'C','points':c},{'kind':'Z'}]
                q=doc2['layers'][1]['shapes'][0]['path'];q.insert(1,{'kind':'L','points':[{'x':93,'y':48}]})
                r2=call('PUT','/v1/plates/amber-plate',{'expected_revision':1,'document':doc2})
                c12=cmp(1,2);assert c12['summary']['verdict']=='identical' and c12['summary']['changed_pixels']==0 and c12['summary']['noise_pixels']==0 and c12['regions']==[]
                assert c12['framing']['noise_floor']==1
                png12,_=call('GET','/v1/plates/amber-plate/compare?base=1&candidate=2&format=png',raw=True)
                assert png12[:8]==b'\x89PNG\r\n\x1a\n' and struct.unpack('>II',png12[16:24])==(192,144)
                # 稿本3：镂空内边移动2px，细小变化必须独立成区而非被平均掉
                doc3=copy.deepcopy(doc2);q3=doc3['layers'][1]['shapes'][0]['path']
                assert q3[6]['points'][0]=={'x':64,'y':64} and q3[9]['points'][0]=={'x':64,'y':104}
                q3[6]['points'][0]['x']=66;q3[9]['points'][0]['x']=66
                call('PUT','/v1/plates/amber-plate',{'expected_revision':2,'document':doc3})
                c23=cmp(2,3);s23=c23['summary']
                assert s23['verdict']=='design_changes' and 0<s23['changed_fraction']<0.05 and len(c23['regions'])==1
                reg=c23['regions'][0]
                assert reg['pixels']<3000 and reg['max_delta']>1 and reg['origin'] in ('added','removed','modified','backdrop')
                assert any(o['shape']=='cyan-window-r2' for o in reg['objects'])
                # 稿本4：透明渐变止点不透明度变化，大面积真实变化
                doc4=copy.deepcopy(doc3);doc4['layers'][0]['shapes'][0]['brush']['stops'][1]['color']['a']=0.8
                call('PUT','/v1/plates/amber-plate',{'expected_revision':3,'document':doc4})
                c34=cmp(3,4);assert c34['summary']['verdict']=='design_changes' and c34['summary']['changed_fraction']>0.1
                assert max(r['pixels'] for r in c34['regions'])>5000
                # 稿本5：背景1/510级扰动，只能判为量化噪声
                doc5=copy.deepcopy(doc4);doc5['background']['r']=0.04+1/510
                call('PUT','/v1/plates/amber-plate',{'expected_revision':4,'document':doc5})
                c45=cmp(4,5);s45=c45['summary']
                assert s45['verdict']=='quantization_noise_only' and s45['changed_pixels']==0 and s45['noise_pixels']>1000 and c45['regions']==[]
                # 稿本6：画布192x144改为160x160，报告必须说明并集坐标与背景语义
                doc6=copy.deepcopy(doc5);doc6['width']=160;doc6['height']=160
                call('PUT','/v1/plates/amber-plate',{'expected_revision':5,'document':doc6})
                c56=cmp(5,6)
                assert c56['framing']['width']==192 and c56['framing']['height']==160
                assert c56['base']['canvas']=={'width':192,'height':144} and c56['candidate']['canvas']=={'width':160,'height':160}
                assert all(c56['framing'][k] for k in ('origin','outside_canvas','background','metric','noise_floor_meaning','legend'))
                assert c56['summary']['verdict']=='design_changes' and c56['summary']['changed_pixels']>1000 and len(c56['regions'])>=1
                png56,hs=call('GET','/v1/plates/amber-plate/compare?base=5&candidate=6&format=png',raw=True)
                assert png56[:8]==b'\x89PNG\r\n\x1a\n' and struct.unpack('>II',png56[16:24])==(192,160)
                assert hs['x-base-scene-digest']==c56['base']['scene_digest'] and hs['x-candidate-scene-digest']==c56['candidate']['scene_digest']
                # 比较是只读的：稿本、成品与校样保持不动
                assert render()['digest']==a1['digest']
                raw,_=call('GET','/v1/artifacts/'+a1['id'],raw=True);assert hashlib.sha256(raw).hexdigest()==a1['digest']
                plate=call('GET','/v1/plates/amber-plate');assert len(plate['revisions'])==6
                assert plate['revisions'][0]['digest']==created['revisions'][0]['digest'] and plate['revisions'][1]['digest']==r2['revisions'][1]['digest']
                call('POST','/v1/proofs',{'id':'proof-compare','artifact':a1['id']},status=201)
                assert call('PUT','/v1/proofs/proof-compare',{'state':'accepted','note':'比较流程后成品仍可裁决'})['state']=='accepted'
                call('GET','/v1/plates/amber-plate/compare?base=1&candidate=99',status=404)
                call('GET','/v1/plates/amber-plate/compare?base=0&candidate=1',status=400)
                call('GET','/v1/plates/amber-plate/compare?base=1&candidate=2&format=tiff',status=400)
                call('GET','/v1/plates/missing-plate/compare?base=1&candidate=2',status=404)
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
