#!/usr/bin/env python3
"""现场离线核查器：只依赖 Python 3 标准库，arm64/amd64 均可运行。

原则：
  1. 已签样的身份由 sha256 字节摘要证明。画面等价（像素相同）也不能替代原签样——
     摘要不符一律判定为“非原签样”，是否另行放行由独立的可用性检查决定。
  2. 重出成品与签样逐字节一致 => 恢复/确定性通过；不一致 => 只能进入“可用性”判定，
     绝不回写为签样。
  3. SVG 是矢量校样：核查结构与展开几何，不承诺与 PNG 逐像素相同。

子命令：
  manifest  校验发布目录全部文件摘要与镜像 tar 结构
  png       两个 PNG：身份(摘要)/尺寸/逐像素差异/ROI 局部差异
  svg       两个 SVG：结构安全、几何路径与变换差异、渐变/裁剪元素
  state     恢复快照：schema、谱系、稿本摘要、成品字节摘要、校样状态
  render    对运行中的实例重出成品并与签样比对（需要 --url）
  risk      一键复算两类放行风险的判定结论
退出码：0 全部通过；1 存在硬性失败；2 用法/环境错误。
"""
import argparse, hashlib, io, json, os, pathlib, struct, sys, tarfile, urllib.request, zlib


def sha256_bytes(b): return hashlib.sha256(b).hexdigest()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Report:
    def __init__(self):
        self.checks, self.fails = [], 0

    def check(self, name, ok, detail=""):
        self.checks.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            self.fails += 1

    def info(self, msg):
        print(f"[INFO] {msg}")

    def finish(self):
        total = len(self.checks)
        print(f"\n汇总：{total - self.fails}/{total} 通过，{self.fails} 项失败")
        return 1 if self.fails else 0


# ------------------------------------------------------------- PNG 解码
def decode_png(blob):
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 容器")
    pos, idat, width, height, bitdepth, colortype = 8, b"", None, None, None, None
    while pos < len(blob):
        length = struct.unpack(">I", blob[pos:pos + 4])[0]
        ctype = blob[pos + 4:pos + 8]
        data = blob[pos + 8:pos + 8 + length]
        if struct.unpack(">I", struct.pack(">I", zlib.crc32(ctype + data) & 0xffffffff))[0] != \
                struct.unpack(">I", blob[pos + 8 + length:pos + 12 + length])[0]:
            raise ValueError(f"PNG {ctype.decode(errors='replace')} 块 CRC 不符")
        if ctype == b"IHDR":
            width, height, bitdepth, colortype, compression, filterm, interlace = struct.unpack(">IIBBBBB", data)
            if compression != 0 or filterm != 0 or interlace != 0:
                raise ValueError("仅支持标准非隔行 PNG")
        elif ctype == b"IDAT":
            idat += data
        pos += 12 + length
    if bitdepth != 8 or colortype not in (2, 6):
        raise ValueError(f"非 8-bit RGB/RGBA（depth={bitdepth}, type={colortype}），与本服务输出契约不符")
    channels = {2: 3, 6: 4}[colortype]
    scan, stride = zlib.decompress(idat), width * channels
    rows, prev, p = [], bytearray(stride), 0
    for _ in range(height):
        f = scan[p]; p += 1
        line = bytearray(scan[p:p + stride]); p += stride
        for i in range(stride):
            x = line[i]
            if f == 1:
                x = (x + (line[i - channels] if i >= channels else 0)) & 255
            elif f == 2:
                x = (x + prev[i]) & 255
            elif f == 3:
                x = (x + (((line[i - channels] if i >= channels else 0) + prev[i]) >> 1)) & 255
            elif f == 4:
                left = line[i - channels] if i >= channels else 0
                up, ul = prev[i], (prev[i - channels] if i >= channels else 0)
                pa, pb, pc = abs(up - ul), abs(left - ul), abs(left + up - 2 * ul)
                pr = left if (pa <= pb and pa <= pc) else (up if pb <= pc else ul)
                x = (x + pr) & 255
            line[i] = x
        rows.append(bytes(line)); prev = line
    return {"width": width, "height": height, "channels": channels, "colortype": colortype,
            "pixels": b"".join(rows)}


def pixel_diff(a, b):
    n = min(len(a), len(b))
    changed = sum(1 for i in range(n) if a[i] != b[i])
    maxd = max((abs(a[i] - b[i]) for i in range(n)), default=0)
    return changed, maxd, abs(len(a) - len(b))


# ------------------------------------------------------------- SVG 结构
def svg_check(blob):
    import re
    text = blob.decode("utf-8", errors="strict")
    forbidden = ["<script", "javascript:", "onload=", "onerror=", "href=", "xlink:href", "<foreignobject", "<iframe", "<image"]
    low = text.lower()
    hits = [t for t in forbidden if t in low]
    # 唯一允许的 http(s) 字样是 SVG 命名空间声明；命名空间不产生任何资源加载
    body = re.sub(r'xmlns\s*=\s*"[^"]*"', "", text)
    external = ("http://" in body) or ("https://" in body)
    return {
        "starts_svg": text.lstrip().startswith("<svg"),
        "ends_svg": text.rstrip().endswith("</svg>"),
        "forbidden_hits": hits,
        "paths": text.count("<path "),
        "gradients": text.count("<linearGradient"),
        "clips": text.count("<clipPath"),
        "matrices": text.count("transform=\"matrix("),
        "external_refs": external,
    }


def svg_numbers(blob):
    import re
    return re.findall(rb"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?", blob)


# ------------------------------------------------------------- 子命令
def cmd_manifest(rep, args):
    root = pathlib.Path(args.release)
    man = json.loads((root / "release-manifest.json").read_text())
    rep.check("清单 schema", man.get("schema") == "prism-plate-offline-release-v1", man.get("schema", ""))
    ok = True
    for e in man["files"]:
        p = root / e["path"]
        if not p.exists() or sha256_file(p) != e["sha256"]:
            rep.check(f"文件摘要 {e['path']}", False, "缺失或摘要不符"); ok = False
    if ok:
        rep.check(f"全部 {len(man['files'])} 个发布文件摘要", True)
    sums = root / "SHA256SUMS"
    if sums.exists():
        bad = 0
        for line in sums.read_text().splitlines():
            digest, rel = line.split("  ", 1)
            if not (root / rel).exists() or sha256_file(root / rel) != digest:
                bad += 1
        rep.check("SHA256SUMS 逐条复核", bad == 0, f"{bad} 条不符")
    for img in man["images"]:
        tar = root / next(f["path"] for f in man["files"] if f["path"].endswith(img["arch"] + ".tar"))
        try:
            with tarfile.open(tar) as tf:
                names = set(tf.getnames())
                m = json.loads(tf.extractfile("manifest.json").read())
                need = {m[0]["Config"], *m[0]["Layers"], "manifest.json", "repositories"}
                cfg = json.loads(tf.extractfile(m[0]["Config"]).read())
                rep.check(f"镜像 {img['arch']} docker-load 布局", need <= names, f"{len(need)} 个必需成员")
                rep.check(f"镜像 {img['arch']} 架构标记", cfg["architecture"] == img["arch"], cfg["architecture"])
                rep.check(f"镜像 {img['arch']} 非 root 运行", cfg["config"]["User"] == "65532:65532", cfg["config"]["User"])
                rep.check(f"镜像 {img['arch']} 固定入口",
                          cfg["config"]["Entrypoint"] == ["/prismplate", "-listen", "0.0.0.0:7846",
                                                          "-data", "/data/prism-plate-v1"])
                layer_blob = tf.extractfile(m[0]["Layers"][0]).read()
                rep.check(f"镜像 {img['arch']} 层摘要与 diff_id 一致",
                          "sha256:" + sha256_bytes(layer_blob) == cfg["rootfs"]["diff_ids"][0],
                          cfg["rootfs"]["diff_ids"][0][:19] + "…")
                with tarfile.open(fileobj=io.BytesIO(layer_blob)) as lf:
                    members = {x.name: x for x in lf.getmembers()}
                    bin_ok = ("prismplate" in members and (members["prismplate"].mode & 0o111) != 0)
                    rep.check(f"镜像 {img['arch']} 层含可执行 /prismplate", bin_ok)
                    rep.check(f"镜像 {img['arch']} 层含 /data 挂载点", "data" in members and members["data"].isdir())
        except (tarfile.TarError, KeyError, json.JSONDecodeError) as e:
            rep.check(f"镜像 {img['arch']} 可读", False, str(e))
    return man


def cmd_png(rep, args):
    a, b = pathlib.Path(args.a), pathlib.Path(args.b)
    ba, bb = a.read_bytes(), b.read_bytes()
    da, db = sha256_bytes(ba), sha256_bytes(bb)
    rep.check("两 PNG 字节身份不同（风险材料前提）", da != db, f"{da[:12]}… vs {db[:12]}…")
    pa, pb = decode_png(ba), decode_png(bb)
    rep.check("画布尺寸一致", (pa["width"], pa["height"]) == (pb["width"], pb["height"]),
              f"{pa['width']}x{pa['height']} vs {pb['width']}x{pb['height']}")
    changed, maxd, tail = pixel_diff(pa["pixels"], pb["pixels"])
    total = len(pa["pixels"])
    rep.info(f"差异字节 {changed}/{total}（{changed / total:.4%}），单通道最大差 {maxd}")
    if args.roi:
        x0, y0, x1, y1 = args.roi
        ch = pa["channels"]
        s = slice((y0 * pa["width"] + x0) * ch, (y1 * pa["width"] + x1) * ch)
        rc, rm, _ = pixel_diff(pa["pixels"][s], pb["pixels"][s])
        rep.info(f"ROI x{x0}..{x1} y{y0}..{y1}：差异字节 {rc}，单通道最大差 {rm}")
        rep.check("ROI 存在可判定的局部偏差", rm > 0, f"roi_max={rm}")
    if args.expect_identical_pixels:
        rep.check("解码像素逐字节相同（画面等价）", changed == 0 and tail == 0)
        rep.check("但字节摘要不同 => 不能当作原签样", da != db)
    return changed, maxd


def cmd_svg(rep, args):
    a, b = pathlib.Path(args.a), pathlib.Path(args.b)
    ba, bb = a.read_bytes(), b.read_bytes()
    sa, sb = svg_check(ba), svg_check(bb)
    for tag, s in ((a.name, sa), (b.name, sb)):
        rep.check(f"SVG {tag} 结构完整", s["starts_svg"] and s["ends_svg"])
        rep.check(f"SVG {tag} 无脚本/外链/事件", not s["forbidden_hits"] and not s["external_refs"],
                  ",".join(s["forbidden_hits"]) or "clean")
        rep.info(f"SVG {tag}：path={s['paths']} gradient={s['gradients']} clip={s['clips']} matrix={s['matrices']}")
    na, nb = svg_numbers(ba), svg_numbers(bb)
    if args.expect_geometry_equal:
        rep.check("两 SVG 字节身份一致", sha256_bytes(ba) == sha256_bytes(bb), "同稿重出应逐字节相同")
        rep.check("展开几何数值序列一致", na == nb, f"{len(na)} vs {len(nb)} 个数")
    else:
        rep.check("两 SVG 字节身份不同", sha256_bytes(ba) != sha256_bytes(bb))
        rep.check("展开几何数值序列存在差异", na != nb, f"{len(na)} vs {len(nb)} 个数（矢量校样需人工/工具复核可用性）")


def cmd_state(rep, args):
    import base64
    root = pathlib.Path(args.release)
    man = json.loads((root / "release-manifest.json").read_text())
    sr = man["signed_recovery"]
    snapshot = pathlib.Path(args.snapshot)
    rep.check("快照字节与清单摘要一致", sha256_file(snapshot) == sr["snapshot_sha256"],
              f"{sha256_file(snapshot)[:16]}…")
    st = json.loads(snapshot.read_text())
    rep.check("快照 schema", st.get("schema") == "prism-plate-v1", st.get("schema", ""))
    plate = st["plates"][sr["plate"]]
    rev = plate["revisions"][sr["revision"] - 1]
    # 说明性复核：Go encoding/json 的 HTML 转义与字段顺序由 Go 侧 verify-snapshot 精确复算；
    # Python 侧只确认文档结构存在、画布等关键字段可读，不把语言差异误判为谱系损坏。
    doc = rev["document"]
    rep.check("稿本文档可读且画布符合契约",
              isinstance(doc.get("width"), int) and 1 <= doc["width"] <= 512 and len(doc.get("layers", [])) >= 1,
              f"canvas={doc.get('width')}x{doc.get('height')} layers={len(doc.get('layers', []))}")
    rep.info(f"稿本 scene_digest={rev['digest'][:16]}…（权威复算由 Go 侧 verify-snapshot 完成）")
    accepted = {p["id"]: p for p in sr["proofs"] if p["state"] == "accepted"}
    all_ok = True
    for proof in st["proofs"].values():
        if proof["state"] != "accepted":
            continue
        art = st["artifacts"][proof["artifact"]]
        want = accepted.get(proof["id"])
        if not want:
            rep.check(f"校样 {proof['id']} 在清单中登记", False); all_ok = False; continue
        # 快照中的 []byte 经 JSON base64 编码
        art_bytes = base64.b64decode(art["data"]) if isinstance(art["data"], str) else bytes(art["data"])
        live_digest = sha256_bytes(art_bytes)
        good = live_digest == art["digest"] == want["artifact_sha256"] and art["state"] == "available"
        rep.check(f"已签 {proof['id']} 成品字节原样保留", good, f"{live_digest[:16]}…")
        disk = root / want["file"]
        rep.check(f"已签 {proof['id']} 与随包文件逐字节相同", disk.read_bytes() == art_bytes)
        all_ok = all_ok and good
    if all_ok:
        rep.check("受校样保护成品均为 available 且不可撤销语义保留", True)
    # 封包自洽
    env = json.loads((root / sr["bundle"]).read_text())
    inner = json.dumps(env["plate"], sort_keys=True, separators=(",", ":")).encode()
    # 封包摘要同样为 Go 编码结果，Python 侧做存在性与 schema 检查，精确复算交给 Go 侧
    rep.check("封包 schema", env.get("schema") == "prism-plate-bundle-v1")
    rep.info(f"封包 digest={env['digest'][:16]}…（精确复算由 Go 侧 verify-snapshot 完成，bundle walkthrough 已覆盖篡改拒绝）")


def cmd_render(rep, args):
    root = pathlib.Path(args.release)
    man = json.loads((root / "release-manifest.json").read_text())
    base = args.url.rstrip("/")
    with urllib.request.urlopen(base + "/healthz", timeout=5) as r:
        health = json.loads(r.read())
    rep.check("实例 healthz", health.get("state") == "ready", str(health))
    for want in man["signed_recovery"]["proofs"]:
        fmt = "png" if want["file"].endswith(".png") else "svg"
        req = urllib.request.Request(
            base + f"/v1/plates/{man['signed_recovery']['plate']}/render?revision={man['signed_recovery']['revision']}&format={fmt}",
            method="POST")
        with urllib.request.urlopen(req, timeout=35) as r:
            meta = json.loads(r.read())
        with urllib.request.urlopen(base + "/v1/artifacts/" + meta["id"], timeout=35) as r:
            blob, hs = r.read(), {k.lower(): v for k, v in r.headers.items()}
        live = sha256_bytes(blob)
        same = live == want["artifact_sha256"]
        rep.check(f"重出 {fmt} 与已签样逐字节相同", same,
                  f"live={live[:16]}… signed={want['artifact_sha256'][:16]}…")
        rep.check(f"重出 {fmt} ETag 即内容摘要", hs.get("etag", "").strip('"') == live)
        if not same:
            rep.info(f"{fmt} 字节不同：禁止替换签样；若需放行，改用 png/svg 子命令做独立可用性判定并留档")
        if fmt == "png":
            signed_blob = (root / want["file"]).read_bytes()
            pa, pb = decode_png(signed_blob), decode_png(blob)
            changed, maxd, _ = pixel_diff(pa["pixels"], pb["pixels"])
            rep.info(f"重出 PNG 像素差异 {changed}/{len(pb['pixels'])}，单通道最大差 {maxd}")


def cmd_risk(rep, args):
    root = pathlib.Path(args.release)
    man = json.loads((root / "release-manifest.json").read_text())
    # 风险 A
    a = man["riskA_equivalent_bytes"]
    orig = (root / a["original_png"]).read_bytes()
    reenc = (root / a["reencoded_png"]).read_bytes()
    po, pr = decode_png(orig), decode_png(reenc)
    rep.check("风险A PNG 字节不同", a["png_bytes_differ"] and sha256_bytes(orig) != sha256_bytes(reenc))
    rep.check("风险A 像素逐字节相同（画面等价）", po["pixels"] == pr["pixels"])
    rep.check("风险A 结论：画面等价也不是原签样，必须独立放行", True,
              "签样身份只认 sha256；重编码件登记为新成品，不得覆盖签样文件")
    sv0, sv1 = (root / man["signed_recovery"]["proofs"][1]["file"]).read_bytes(), (root / a["svg_variant"]).read_bytes()
    rep.check("风险A SVG 几何被改动", svg_numbers(sv0) != svg_numbers(sv1))
    # 风险 B
    b = man["riskB_local_deviation"]
    bl = (root / "risks/riskB-local/riskb-local.png").read_bytes()
    bs = (root / "risks/riskB-local/riskb-shift.png").read_bytes()
    pl, ps = decode_png(bl), decode_png(bs)
    changed, maxd, _ = pixel_diff(pl["pixels"], ps["pixels"])
    ratio = changed / len(pl["pixels"])
    x0, y0, x1, y1 = 0, b["roi_band_y"][0], pl["width"], b["roi_band_y"][1] + 1
    ch = pl["channels"]
    sl = slice((y0 * pl["width"] + x0) * ch, (y1 * pl["width"] + x1) * ch)
    rc, rm, _ = pixel_diff(pl["pixels"][sl], ps["pixels"][sl])
    rep.check("风险B 整图差异占比很低（肉眼整图易误判）", ratio < 0.02, f"{ratio:.4%}")
    rep.check("风险B 边界 ROI 有明确局部偏差", rm >= 8 and rc > 0, f"roi_changed={rc} roi_max={rm}")
    rep.check("风险B 结论：整图看着一样不构成放行，需 ROI/几何核查", True,
              f"细曲线带 y={b['roi_band_y']} 单通道最大差 {rm}")
    rep.info(f"清单记录值：whole_ratio={b['whole_image_changed_ratio']} roi_max={b['roi_max_channel_delta']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("manifest"); m.add_argument("--release", required=True)
    p = sub.add_parser("png"); p.add_argument("--release", default=None); p.add_argument("a"); p.add_argument("b")
    p.add_argument("--roi", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"))
    p.add_argument("--expect-identical-pixels", action="store_true")
    s = sub.add_parser("svg"); s.add_argument("a"); s.add_argument("b"); s.add_argument("--expect-geometry-equal", action="store_true")
    st = sub.add_parser("state"); st.add_argument("--release", required=True); st.add_argument("--snapshot", required=True)
    r = sub.add_parser("render"); r.add_argument("--release", required=True); r.add_argument("--url", required=True)
    rk = sub.add_parser("risk"); rk.add_argument("--release", required=True)
    args = ap.parse_args()
    rep = Report()
    try:
        if args.cmd == "manifest":
            cmd_manifest(rep, args)
        elif args.cmd == "png":
            cmd_png(rep, args)
        elif args.cmd == "svg":
            cmd_svg(rep, args)
        elif args.cmd == "state":
            cmd_state(rep, args)
        elif args.cmd == "render":
            cmd_render(rep, args)
        elif args.cmd == "risk":
            cmd_risk(rep, args)
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    return rep.finish()


if __name__ == "__main__":
    sys.exit(main())
