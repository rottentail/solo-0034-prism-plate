#!/usr/bin/env python3
"""中央工位离线发布构建脚本（仅使用 Python 标准库 + 本机 Go 工具链）。

产物结构：
  release/out/
    images/    arm64、amd64 可离线 docker/podman load 的容器镜像 tar
    recovery/  已签样 state.json 快照、封包、已签 PNG/SVG、清单
    risks/     两类放行风险的稿件、渲染成品与等价/偏差材料
    site/      现场离线核查脚本（随包带走，不依赖网络）
    SHA256SUMS 全部产物字节摘要

构建期需要网络仅用于安装 Go/交叉编译工具链；本脚本本身不访问网络。
"""
import argparse, hashlib, io, json, os, pathlib, shutil, signal, socket, struct, subprocess, tarfile, time, urllib.request, zlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILD = ROOT / "release" / "build"


def sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Server:
    def __init__(self, binary: pathlib.Path, data: pathlib.Path, qemu: pathlib.Path | None = None, log: pathlib.Path | None = None):
        self.binary, self.data, self.qemu = binary, data, qemu
        self.log = log
        self.proc = None
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        self.url = f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.data.mkdir(parents=True, exist_ok=True)
        cmd = ([str(self.qemu)] if self.qemu else []) + [str(self.binary), "-listen", f"127.0.0.1:{self.port}", "-data", str(self.data)]
        lf = open(self.log, "w") if self.log else subprocess.DEVNULL
        self.proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
        for _ in range(200):
            if self.proc.poll() is not None:
                raise RuntimeError(f"service exited early rc={self.proc.returncode}")
            try:
                urllib.request.urlopen(self.url + "/healthz", timeout=1).close()
                return self
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("service startup timed out")

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                rc = self.proc.wait(timeout=20)
                if rc != 0:
                    raise RuntimeError(f"service shutdown rc={rc}")
            except subprocess.TimeoutExpired:
                self.proc.kill()
                raise

    def call(self, method, path, data=None, status=200, raw=False, headers=None):
        body = data if isinstance(data, bytes) else (json.dumps(data).encode() if data is not None else None)
        req = urllib.request.Request(self.url + path, data=body, method=method,
                                     headers=headers or {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=35) as r:
                code, payload, hs = r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            code, payload, hs = e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}
        if code != status:
            raise RuntimeError(f"{method} {path}: expected {status}, got {code}: {payload[:200]!r}")
        return (payload, hs) if raw else (json.loads(payload) if payload else None)

    def render(self, plate, revision, fmt):
        return self.call("POST", f"/v1/plates/{plate}/render?revision={revision}&format={fmt}", status=201)

    def artifact(self, aid):
        return self.call("GET", f"/v1/artifacts/{aid}", raw=True, status=200)


# ---------------------------------------------------------------- OCI 镜像
def oci_image_tar(out_tar: pathlib.Path, repo_tag: str, binary: pathlib.Path, arch: str, created: str):
    """手工组装 Docker v1.2 布局的单镜像 tar，scratch 基础 + 静态 Go 二进制。

    无 docker 守护进程参与；digest 为确定性 sha256，可在现场用 verify_release.py 复核。
    """
    layer_dir = "layer"
    bin_blob = binary.read_bytes()
    # layer.tar: /prismplate（0755）、/data（0755，UID 65532 可写由宿主挂载保证）
    layer_buf = io.BytesIO()
    with tarfile.open(fileobj=layer_buf, mode="w") as lt:
        info = tarfile.TarInfo("prismplate")
        info.size = len(bin_blob); info.mode = 0o755; info.mtime = 0
        info.uid = info.gid = 65532; info.uname = info.gname = "nonroot"
        lt.addfile(info, io.BytesIO(bin_blob))
        dinfo = tarfile.TarInfo("data"); dinfo.type = tarfile.DIRTYPE
        dinfo.mode = 0o755; dinfo.mtime = 0; dinfo.uid = dinfo.gid = 65532
        lt.addfile(dinfo)
    layer_bytes = layer_buf.getvalue()
    layer_digest = "sha256:" + sha256_bytes(layer_bytes)

    config = {
        "created": created,
        "architecture": arch,
        "os": "linux",
        "config": {
            "User": "65532:65532",
            "Env": ["PATH=/"],
            "Entrypoint": ["/prismplate", "-listen", "0.0.0.0:7846", "-data", "/data/prism-plate-v1"],
            "ExposedPorts": {"7846/tcp": {}},
            "WorkingDir": "/",
            "Volumes": {"/data": {}},
        },
        "rootfs": {"type": "layers", "diff_ids": [layer_digest]},
        "history": [{"created": created, "comment": "offline prism-plate build (scratch, static)"}],
    }
    config_bytes = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    config_digest = "sha256:" + sha256_bytes(config_bytes)
    layer_name = layer_digest.split(":", 1)[1] + ".tar"
    manifest = [{
        "Config": config_digest.split(":", 1)[1] + ".json",
        "RepoTags": [repo_tag],
        "Layers": [layer_name],
    }]
    name, tag = repo_tag.split(":", 1)
    repositories = {name: {tag: layer_digest}}

    def add(tf, arcname, blob: bytes):
        ti = tarfile.TarInfo(arcname); ti.size = len(blob); ti.mode = 0o644; ti.mtime = 0
        tf.addfile(ti, io.BytesIO(blob))

    # docker/podman save 兼容布局：层以未压缩 tar 存放，diff_id 即未压缩摘要
    out_tar.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_tar, "w") as tf:
        add(tf, manifest[0]["Config"], config_bytes)
        add(tf, layer_name, layer_bytes)
        add(tf, "manifest.json", json.dumps(manifest, separators=(",", ":")).encode())
        add(tf, "repositories", json.dumps(repositories, separators=(",", ":")).encode())
    return {"tag": repo_tag, "arch": arch, "config_digest": config_digest, "layer_digest": layer_digest,
            "layer_member": layer_name, "size": out_tar.stat().st_size}


# ---------------------------------------------------------------- PNG 重编码
def reencode_png_identical_pixels(src: pathlib.Path, dst: pathlib.Path):
    """仅改变 PNG 容器字节（zlib 压缩级别/策略），解码后像素逐字节相同。"""
    raw = src.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks, width, height, bitdepth, colortype = 8, [], None, None, None, None
    idat = b""
    while pos < len(raw):
        length = struct.unpack(">I", raw[pos:pos + 4])[0]
        ctype = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bitdepth, colortype = struct.unpack(">IIBB", data[:10])
        if ctype == b"IDAT":
            idat += data
        else:
            chunks.append((ctype, data))
        pos += 12 + length
    assert bitdepth == 8 and colortype in (2, 6), f"非预期 PNG 类型 colortype={colortype}"
    scan = zlib.decompress(idat)
    out = io.BytesIO(); out.write(b"\x89PNG\r\n\x1a\n")

    def chunk(ctype, data):
        out.write(struct.pack(">I", len(data))); out.write(ctype); out.write(data)
        out.write(struct.pack(">I", zlib.crc32(ctype + data) & 0xffffffff))

    for ctype, data in chunks:
        if ctype == b"IHDR":
            chunk(ctype, data)
    # 非默认压缩级别 + 不同策略 => 字节必然不同，图像像素不变
    co = zlib.compressobj(1, zlib.DEFLATED, zlib.MAX_WBITS, zlib.DEF_MEM_LEVEL, zlib.Z_FILTERED)
    chunk(b"IDAT", co.compress(scan) + co.flush())
    for ctype, data in chunks:
        if ctype not in (b"IHDR", b"IEND"):
            chunk(ctype, data)
    chunk(b"IEND", b"")
    dst.write_bytes(out.getvalue())
    return width, height, colortype


def _unfilter(scan: bytes, width: int, height: int, channels: int):
    stride = width * channels
    rows, prev, p = [], bytearray(stride), 0
    for _ in range(height):
        f = scan[p]; p += 1
        line = bytearray(scan[p:p + stride]); p += stride
        bpp = channels
        for i in range(stride):
            a = line[i]
            if f == 1:
                a = (a + (line[i - bpp] if i >= bpp else 0)) & 255
            elif f == 2:
                a = (a + prev[i]) & 255
            elif f == 3:
                a = (a + (((line[i - bpp] if i >= bpp else 0) + prev[i]) >> 1)) & 255
            elif f == 4:
                left = line[i - bpp] if i >= bpp else 0
                up, ul = prev[i], (prev[i - bpp] if i >= bpp else 0)
                pa, pb, pc = abs(up - ul), abs(left - ul), abs(left + up - 2 * ul)
                pr = left if (pa <= pb and pa <= pc) else (up if pb <= pc else ul)
                a = (a + pr) & 255
            line[i] = a
        rows.append(bytes(line)); prev = line
    return b"".join(rows)


def raw_pixels(png_bytes: bytes):
    """解码 8-bit RGB/RGBA PNG，返回 (w,h,colortype, 逐扫描线像素 bstr)。"""
    pos, width, height = 8, None, None
    idat, colortype, bitdepth = b"", None, None
    while pos < len(png_bytes):
        length = struct.unpack(">I", png_bytes[pos:pos + 4])[0]
        ctype = png_bytes[pos + 4:pos + 8]
        data = png_bytes[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bitdepth, colortype = struct.unpack(">IIBB", data[:10])
        elif ctype == b"IDAT":
            idat += data
        pos += 12 + length
    assert bitdepth == 8 and colortype in (2, 6)
    channels = {2: 3, 6: 4}[colortype]
    return width, height, colortype, _unfilter(zlib.decompress(idat), width, height, channels)


def pixel_stats(a: bytes, b: bytes):
    assert len(a) == len(b)
    diff = sum(1 for x, y in zip(a, b) if x != y)
    maxc = max((abs(x - y) for x, y in zip(a, b)), default=0)
    return diff, maxc


# ---------------------------------------------------------------- SVG 改写
def svg_geometry_variant(svg: bytes) -> bytes:
    """在 SVG 末尾闭合标签前注入一条 1px 偏移的重复轮廓：画面近似等价、几何不同。"""
    assert svg.rstrip().endswith(b"</svg>")
    extra = (b'<g opacity="0.02"><path d="M0 0L1 0L1 1L0 1Z" fill="rgb(100%,100%,100%)" '
             b'fill-opacity="1" fill-rule="nonzero" transform="matrix(1 0 0 1 0.5 0.5)"/></g>')
    return svg.rstrip()[:-6] + extra + b"</svg>"


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", default=os.environ.get("GOROOT_BIN", str(pathlib.Path.home() / "tools/go/bin/go")))
    ap.add_argument("--qemu", default=str(pathlib.Path.home() / "tools/qemu-deb/usr/bin/qemu-x86_64-static"))
    ap.add_argument("--out", default=str(ROOT / "release/out"))
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args()
    go = pathlib.Path(args.go)
    qemu = pathlib.Path(args.qemu) if pathlib.Path(args.qemu).exists() else None
    out = pathlib.Path(args.out)
    work = BUILD / "work"
    if work.exists():
        shutil.rmtree(work)
    if out.exists():
        shutil.rmtree(out)
    for d in ("images", "recovery/signed", "risks/riskA-equivalent", "risks/riskB-local", "site/tools"):
        (out / d).mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True)

    env = dict(os.environ, CGO_ENABLED="0", GOFLAGS="-mod=mod")
    def build(pkg, arch_goarch, target):
        e = dict(env, GOOS="linux", GOARCH=arch_goarch)
        subprocess.run([str(go), "build", "-trimpath", "-o", str(target), pkg],
                       cwd=ROOT, check=True, env=e)
    bin_arm, bin_amd = work / "prismplate-arm64", work / "prismplate-amd64"
    ver_arm, ver_amd = work / "verify-arm64", work / "verify-amd64"
    print("[1/8] 交叉编译 linux/arm64 与 linux/amd64 服务与核查二进制（全部静态）")
    build("./cmd/prismplate", "arm64", bin_arm); build("./cmd/prismplate", "amd64", bin_amd)
    build("./cmd/verify", "arm64", ver_arm); build("./cmd/verify", "amd64", ver_amd)

    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print("[2/8] 组装 scratch 离线镜像 tar（无 docker 守护进程）")
    images = [
        oci_image_tar(out / "images/prismplate-linux-arm64.tar", "prismplate:offline-arm64", bin_arm, "arm64", created),
        oci_image_tar(out / "images/prismplate-linux-amd64.tar", "prismplate:offline-amd64", bin_amd, "amd64", created),
    ]

    print("[3/8] 中央实例：建立琥珀版面、渲染并签样 PNG 与 SVG")
    seed = json.loads((ROOT / "examples/amber-proof.json").read_text())
    central_data = work / "central-data"
    central_log = work / "central.log"
    with Server(bin_arm, central_data, log=central_log) as svc:
        created_plate = svc.call("POST", "/v1/plates", seed, status=201)
        scene_digest = created_plate["revisions"][0]["digest"]
        png_meta = svc.render("amber-plate", 1, "png")
        svg_meta = svc.render("amber-plate", 1, "svg")
        png_bytes, png_hs = svc.artifact(png_meta["id"])
        svg_bytes, svg_hs = svc.artifact(svg_meta["id"])
        assert sha256_bytes(png_bytes) == png_meta["digest"] == png_hs["etag"].strip('"')
        assert sha256_bytes(svg_bytes) == svg_meta["digest"] == svg_hs["etag"].strip('"')
        proof_png = svc.call("POST", "/v1/proofs", {"id": "proof-amber-png", "artifact": png_meta["id"]}, status=201)
        proof_svg = svc.call("POST", "/v1/proofs", {"id": "proof-amber-svg", "artifact": svg_meta["id"]}, status=201)
        svc.call("PUT", "/v1/proofs/proof-amber-png", {"state": "accepted", "note": "中央工位签样：琥珀叠色 PNG 像素校样通过"}, status=200)
        svc.call("PUT", "/v1/proofs/proof-amber-svg", {"state": "accepted", "note": "中央工位签样：琥珀叠色 SVG 矢量校样通过"}, status=200)
        bundle_bytes, _ = svc.call("GET", "/v1/plates/amber-plate/bundle", raw=True)
        # 受校样保护的成品必须拒绝撤销
        svc.call("DELETE", f"/v1/artifacts/{png_meta['id']}", status=409)
        svc.call("DELETE", f"/v1/artifacts/{svg_meta['id']}", status=409)

    signed = out / "recovery/signed"
    (signed / "amber-proof-rev1.png").write_bytes(png_bytes)
    (signed / "amber-proof-rev1.svg").write_bytes(svg_bytes)
    (out / "recovery/amber-plate-bundle.json").write_bytes(bundle_bytes)

    print("[4/8] 正常关停后冻结 state.json 备份（恢复材料的权威快照）")
    state_json = central_data / "state.json"
    assert not (central_data / "writer.lock").exists(), "未干净关停，锁文件仍在"
    backup = out / "recovery/state.json"
    shutil.copy2(state_json, backup)

    # 风险 A：字节不同但画面等价（PNG 重编码 + SVG 注入极淡重复几何）
    print("[5/8] 风险A：构造字节不同但画面等价的放行材料")
    ra = out / "risks/riskA-equivalent"
    re_w, re_h, _ = reencode_png_identical_pixels(signed / "amber-proof-rev1.png", ra / "amber-proof-reencoded.png")
    w, h, _, px_orig = raw_pixels((signed / "amber-proof-rev1.png").read_bytes())
    _, _, _, px_re = raw_pixels((ra / "amber-proof-reencoded.png").read_bytes())
    assert (w, h) == (re_w, re_h) and px_orig == px_re, "重编码后像素必须逐字节相同"
    svg_variant = svg_geometry_variant(svg_bytes)
    (ra / "amber-proof-geometry-variant.svg").write_bytes(svg_variant)
    assert b"<linearGradient" in svg_variant and b"clipPath" in svg_variant

    # 风险 B：整图近似、关键局部有偏差（服务真实渲染两条稿件）
    print("[6/8] 风险B：服务真实渲染边界细曲线/低透明渐变的原稿与亚像素偏移稿")
    rb = out / "risks/riskB-local"
    b_local = json.loads((BUILD / "riskb-local.json").read_text())
    b_shift = json.loads((BUILD / "riskb-shift.json").read_text())
    (rb / "riskb-local.json").write_text(json.dumps(b_local, ensure_ascii=False, indent=2))
    (rb / "riskb-shift.json").write_text(json.dumps(b_shift, ensure_ascii=False, indent=2))
    with Server(bin_arm, work / "risk-data", log=work / "risk.log") as svc:
        svc.call("POST", "/v1/plates", b_local, status=201)
        svc.call("POST", "/v1/plates", b_shift, status=201)
        for plate, tag in (("riskb-local", "local"), ("riskb-shift", "shift")):
            pm = svc.render(plate, 1, "png"); pb, _ = svc.artifact(pm["id"])
            (rb / f"riskb-{tag}.png").write_bytes(pb)
            sm = svc.render(plate, 1, "svg"); sb, _ = svc.artifact(sm["id"])
            (rb / f"riskb-{tag}.svg").write_bytes(sb)
    rw, rh, rct, px_local = raw_pixels((rb / "riskb-local.png").read_bytes())
    _, _, _, px_shift = raw_pixels((rb / "riskb-shift.png").read_bytes())
    channels = {2: 3, 6: 4}[rct]
    diff_bytes, max_channel = pixel_stats(px_local, px_shift)
    total = len(px_local)
    # ROI：细曲线所在行带 y=27..34，全宽
    roi_w = rw
    roi = slice(27 * roi_w * channels, 35 * roi_w * channels)
    roi_diff, roi_max = pixel_stats(px_local[roi], px_shift[roi])

    print("[7/8] 复制 Go/Python 核查工具与现场文档（静态二进制，现场免安装）")
    shutil.copy2(ROOT / "release/README.md", out / "README.md")
    shutil.copy2(ROOT / "release/site/CHECKLIST.md", out / "site/CHECKLIST.md")
    # 现场核查脚本随包复制；Go 核查器与服务二进制按架构分别放置（静态，无外部依赖）
    for script in ("verify_release.py", "restore_rehearsal.py"):
        shutil.copy2(ROOT / "release/site" / script, out / "site" / script)
        os.chmod(out / "site" / script, 0o755)
    for src, name in ((ver_arm, "verify-linux-arm64"), (ver_amd, "verify-linux-amd64"),
                      (bin_arm, "prismplate-linux-arm64"), (bin_amd, "prismplate-linux-amd64")):
        shutil.copy2(src, out / "site/tools" / name)
        os.chmod(out / "site/tools" / name, 0o755)

    print("[8/8] 生成发布清单与 SHA256SUMS")
    files = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "SHA256SUMS" and p.name != "release-manifest.json":
            files.append({"path": str(p.relative_to(out)), "sha256": sha256_file(p), "size": p.stat().st_size})
    manifest = {
        "schema": "prism-plate-offline-release-v1",
        "built_at_utc": created,
        "toolchain": {"go": subprocess.run([str(go), "version"], capture_output=True, text=True).stdout.strip(),
                      "host_arch": "arm64", "qemu_x86_64": bool(qemu)},
        "images": images,
        "signed_recovery": {
            "plate": "amber-plate",
            "revision": 1,
            "scene_digest": scene_digest,
            "snapshot": "recovery/state.json",
            "snapshot_sha256": sha256_file(backup),
            "bundle": "recovery/amber-plate-bundle.json",
            "bundle_sha256": sha256_file(out / "recovery/amber-plate-bundle.json"),
            "proofs": [
                {"id": "proof-amber-png", "artifact_id": png_meta["id"], "artifact_sha256": png_meta["digest"],
                 "file": "recovery/signed/amber-proof-rev1.png", "state": "accepted"},
                {"id": "proof-amber-svg", "artifact_id": svg_meta["id"], "artifact_sha256": svg_meta["digest"],
                 "file": "recovery/signed/amber-proof-rev1.svg", "state": "accepted"},
            ],
        },
        "riskA_equivalent_bytes": {
            "note": "PNG 仅压缩容器不同，解码像素逐字节相同；SVG 多一条 2% 不透明偏移轮廓。画面等价不等于签样可替换。",
            "original_png": "recovery/signed/amber-proof-rev1.png",
            "reencoded_png": "risks/riskA-equivalent/amber-proof-reencoded.png",
            "pixels_identical": px_orig == px_re,
            "png_bytes_differ": sha256_file(signed / "amber-proof-rev1.png") != sha256_file(ra / "amber-proof-reencoded.png"),
            "svg_variant": "risks/riskA-equivalent/amber-proof-geometry-variant.svg",
            "svg_bytes_differ": sha256_bytes(svg_variant) != svg_meta["digest"],
        },
        "riskB_local_deviation": {
            "note": "低透明渐变两稿完全一致；唯一差异是边界细曲线亚像素 0.55px 偏移，整图差异占比极低但边界 ROI 明显。",
            "canvas": {"width": 180, "height": 140},
            "roi_band_y": [27, 34],
            "whole_image_changed_bytes": diff_bytes,
            "whole_image_total_bytes": total,
            "whole_image_changed_ratio": round(diff_bytes / total, 6),
            "whole_image_max_channel_delta": max_channel,
            "roi_changed_bytes": roi_diff,
            "roi_max_channel_delta": roi_max,
            "local_scene": "risks/riskB-local/riskb-local.json",
            "shift_scene": "risks/riskB-local/riskb-shift.json",
        },
        "files": files,
    }
    (out / "release-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with open(out / "SHA256SUMS", "w") as f:
        for e in files:
            f.write(f"{e['sha256']}  {e['path']}\n")
        f.write(f"{sha256_file(out / 'release-manifest.json')}  release-manifest.json\n")

    if not args.keep_work:
        shutil.rmtree(work)
    print(json.dumps({"ok": True, "out": str(out),
                      "riskB": {"whole_ratio": manifest["riskB_local_deviation"]["whole_image_changed_ratio"],
                                "roi_changed_bytes": roi_diff, "roi_max": roi_max}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
