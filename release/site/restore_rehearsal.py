#!/usr/bin/env python3
"""现场离线恢复演练：在临时数据根上完整走一遍“恢复→原样核对→重出判定”。

无网络、无 root、仅 Python 标准库；可用本机构架服务二进制，也可经 qemu-user
模拟另一架构（由 --qemu 指定）。所有产物落在临时目录，演练结束自动清理，
不触碰真实数据根。

流程（对应 docs/operations.md 的备份恢复纪律）：
  阶段1 原样恢复：把 recovery/state.json 放进独立临时数据根启动，
         GET 已签 PNG/SVG，必须与随包签样逐字节相同（sha256 全等）。
  阶段2 重出判定：对同一稿本重新 render，重出成品与签样逐字节相同 => 确定性通过；
         若不同，绝不覆盖签样，只给出像素/SVG 差异，交人工独立放行。
  阶段3 封包演练：空实例导入 bundle，再重出，应得到与签样相同的成品摘要。
  阶段4 风险回归：对风险 A/B 材料跑核查器，确认两类放行风险都能被检出。

退出码：0 全部通过；1 硬性失败；2 用法/环境错误。
"""
import argparse, hashlib, json, pathlib, shutil, signal, socket, subprocess, sys, tempfile, time, urllib.request, urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import verify_release as vr  # noqa: E402


def sha256_bytes(b): return hashlib.sha256(b).hexdigest()


class TempServer:
    def __init__(self, binary, data, qemu=None):
        self.binary, self.data, self.qemu = binary, data, qemu
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc = None

    def __enter__(self):
        self.data.mkdir(parents=True, exist_ok=True)
        cmd = ([str(self.qemu)] if self.qemu else []) + [str(self.binary), "-listen",
                                                          f"127.0.0.1:{self.port}", "-data", str(self.data)]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(200):
            if self.proc.poll() is not None:
                raise RuntimeError("service exited: " + (self.proc.stdout.read() or ""))
            try:
                urllib.request.urlopen(self.url + "/healthz", timeout=1).close()
                return self
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("startup timed out")

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                rc = self.proc.wait(timeout=20)
                if rc != 0:
                    raise RuntimeError(f"shutdown rc={rc}")
            except subprocess.TimeoutExpired:
                self.proc.kill()
                raise

    def call(self, method, path, data=None, status=200):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.url + path, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=35) as r:
                code, payload = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, payload = e.code, e.read()
        if code != status:
            raise RuntimeError(f"{method} {path}: want {status}, got {code}: {payload[:160]!r}")
        return json.loads(payload) if payload else None

    def get(self, path, status=200):
        req = urllib.request.Request(self.url + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=35) as r:
                code, payload, hs = r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            code, payload, hs = e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}
        if code != status:
            raise RuntimeError(f"GET {path}: want {status}, got {code}")
        return payload, hs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release", required=True, help="发布目录（含 release-manifest.json）")
    ap.add_argument("--arch", choices=("arm64", "amd64"), required=True)
    ap.add_argument("--qemu", help="可选 qemu-user 二进制（用于非本机构架模拟执行）")
    ap.add_argument("--work", help="保留临时目录以便人工复核（默认自动清理）")
    args = ap.parse_args()

    root = pathlib.Path(args.release).resolve()
    man = json.loads((root / "release-manifest.json").read_text())
    binary = root / "site/tools" / f"prismplate-linux-{args.arch}"
    if not binary.exists():
        print(f"[ERROR] 缺少 {binary}", file=sys.stderr)
        return 2
    qemu = pathlib.Path(args.qemu) if args.qemu else None
    if qemu and not qemu.exists():
        print(f"[ERROR] qemu 不存在: {qemu}", file=sys.stderr)
        return 2
    mode = f"{args.arch}{' 经 qemu 模拟' if qemu else ' 本机实机'}"
    print(f"== 离线恢复演练（{mode}） ==")

    base = pathlib.Path(args.work) if args.work else pathlib.Path(tempfile.mkdtemp(prefix="prism-restore-"))
    base.mkdir(parents=True, exist_ok=True)
    if not args.work:
        shutil.rmtree(base, ignore_errors=True); base.mkdir(parents=True)
    fails = 0

    def expect(name, ok, detail=""):
        nonlocal fails
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            fails += 1

    sr = man["signed_recovery"]
    signed_files = {p["file"]: p["artifact_sha256"] for p in sr["proofs"]}

    # ---- 阶段1：原样恢复
    d1 = base / "stage1-restore"
    d1.mkdir(parents=True)
    shutil.copy2(root / sr["snapshot"], d1 / "state.json")
    try:
        with TempServer(binary, d1, qemu) as svc:
            expect("阶段1 恢复实例 healthz", json.loads(
                urllib.request.urlopen(svc.url + "/healthz", timeout=5).read())["state"] == "ready")
            plate = svc.call("GET", f"/v1/plates/{sr['plate']}")
            expect("阶段1 版面稿本号恢复", plate["revisions"][sr["revision"] - 1]["digest"] == sr["scene_digest"])
            for rel, want_digest in signed_files.items():
                fmt = "png" if rel.endswith(".png") else "svg"
                meta = svc.call("POST", f"/v1/plates/{sr['plate']}/render?revision={sr['revision']}&format={fmt}", status=201)
                blob, hs = svc.get("/v1/artifacts/" + meta["id"])
                live = sha256_bytes(blob)
                expect(f"阶段1 恢复后 GET 已签 {fmt} 逐字节原样", live == want_digest,
                       f"{live[:16]}… vs {want_digest[:16]}…")
                expect(f"阶段1 随包签样文件 {fmt} 与快照成品一致", (root / rel).read_bytes() == blob)
                expect(f"阶段1 {fmt} ETag 即字节摘要", hs["etag"].strip('"') == live)
    except Exception as e:
        expect("阶段1 恢复演练执行", False, repr(e))

    # ---- 阶段2：重出独立判定（新数据根，只放 bundle，不预置任何成品）
    d2 = base / "stage2-rerender"
    try:
        with TempServer(binary, d2, qemu) as svc:
            env = (root / sr["bundle"]).read_bytes()
            imported = svc.call("POST", "/v1/bundles", json.loads(env), status=201)
            expect("阶段2 封包导入成功且未覆盖既有版面", imported["id"] == sr["plate"])
            for rel, want_digest in signed_files.items():
                fmt = "png" if rel.endswith(".png") else "svg"
                meta = svc.call("POST", f"/v1/plates/{sr['plate']}/render?revision={sr['revision']}&format={fmt}", status=201)
                blob, _ = svc.get("/v1/artifacts/" + meta["id"])
                live = sha256_bytes(blob)
                same = live == want_digest
                expect(f"阶段2 重出 {fmt} 与签样逐字节相同（确定性）", same)
                if not same:
                    print(f"        → {fmt} 画面等价也不得替换签样；进入独立可用性判定并留档")
                    if fmt == "png":
                        pa = vr.decode_png((root / rel).read_bytes()); pb = vr.decode_png(blob)
                        changed, maxd, _ = vr.pixel_diff(pa["pixels"], pb["pixels"])
                        print(f"        → 像素差异 {changed}/{len(pb['pixels'])}，单通道最大差 {maxd}")
    except Exception as e:
        expect("阶段2 重出演练执行", False, repr(e))

    # ---- 阶段3：重名导入拒绝（导入纪律）
    d3 = base / "stage3-conflict"
    try:
        with TempServer(binary, d3, qemu) as svc:
            svc.call("POST", "/v1/bundles", json.loads((root / sr["bundle"]).read_bytes()), status=201)
            try:
                svc.call("POST", "/v1/bundles", json.loads((root / sr["bundle"]).read_bytes()), status=409)
                expect("阶段3 重名封包导入被 409 拒绝", True)
            except RuntimeError as e:
                expect("阶段3 重名封包导入被 409 拒绝", False, str(e))
    except Exception as e:
        expect("阶段3 冲突演练执行", False, repr(e))

    # ---- 阶段4：风险回归（复用纯 Python 核查，无服务依赖）
    rep = vr.Report()
    vr.cmd_risk(rep, argparse.Namespace(release=str(root)))
    if rep.fails:
        fails += rep.fails

    print(f"\n演练工作目录：{base}")
    print(f"汇总（{mode}）：{fails} 项失败")
    if not args.work:
        shutil.rmtree(base, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
