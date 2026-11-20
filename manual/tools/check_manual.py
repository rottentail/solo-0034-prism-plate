#!/usr/bin/env python3
"""坐标排错手册的可重跑校验，只依赖 Python 标准库（可选 Go 工具链用于重渲染）。

用法:
  python3 manual/tools/check_manual.py              # 只校验已产出的 outputs/
  python3 manual/tools/check_manual.py --rerender   # 先调用 Go 渲染器再校验

校验内容:
  1. outputs/manifest.json 登记的每个 PNG/SVG 存在且 sha256 与清单一致
  2. 三对“PNG 不可区分”案例确实逐字节相同，对应 SVG 几何确实不同
  3. 各案例的关键区分像素与清单登记值一致
  4. 源稿数量与成品数量一致（16 个小稿）
"""
import hashlib, json, pathlib, subprocess, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import probe  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANUAL = ROOT / "manual"

# 逐字节相同的 PNG 等价对，以及它们在 SVG 中必须出现的区分性几何片段。
PNG_EQUIVALENT = [
    ("c03-hole-edge320", "c03-hole-edge321",
     ('M32 24', 'M32.1 24'),
     "镂空边界 32.0 与 32.1 落不进任何采样点"),
    ("c04-baseline", "c04-clip-mirrored",
     ('M8 12L52 12', 'M12 12L56 12'),
     "裁域留原处 [8,52] 与裁域被镜像到 [12,56]"),
    ("c04-no-mirror", "c04-stops-reversed",
     ('matrix(1 0 0 1 0 0)', 'matrix(-1 0 0 1 64 0)'),
     "几何没镜像 与 几何镜像但停点对调，两种错误画面相同"),
]

# 必须不同的案例对，以及一个能在 PNG 上直接分开的关键像素。
PNG_DISTINGUISHED = [
    ("c01-shape-shift", (44, 32), "c01-mask-shift", (44, 32),
     "实色：形状错放时 x=44 仍在交集带，蒙版错放时形状只到 x=40"),
    ("c02-shape-shift", (36, 32), "c02-mask-shift", (36, 32),
     "渐变：同坐标 x=36，跟随的渐变读暖色，未动的渐变轴读冷色"),
    ("c05-shape-shift", (44, 32), "c05-mask-shift", (44, 32),
     "screen 叠色：换观察点到 x=44 即可分开两类错误"),
    ("c03-hole-edge320", (32, 34), "c03-hole-edge325", (32, 34),
     "边界压中线 x=32.5 时产生半覆盖像素，与压网格线不同"),
]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(msg):
    raise SystemExit("校验失败: " + msg)


def png_pixel(case_id, x, y):
    png = probe.read_png(str(MANUAL / "outputs" / "png" / (case_id + ".png")))
    return probe.pixel(png, x, y)


def main():
    if "--rerender" in sys.argv:
        print(">> go run ./manual/tools/rendercases ./manual", flush=True)
        rc = subprocess.run(["go", "run", "./manual/tools/rendercases", "./manual"],
                            cwd=ROOT).returncode
        if rc != 0:
            fail("Go 渲染器退出码 %d，需要 Go 1.24+ 工具链" % rc)

    sources = sorted((MANUAL / "sources").glob("*.json"))
    manifest = json.loads((MANUAL / "outputs" / "manifest.json").read_text())
    cases = {c["id"]: c for c in manifest["cases"]}
    if len(sources) != len(cases):
        fail("源稿 %d 个但清单登记 %d 个" % (len(sources), len(cases)))

    # 1. 成品存在性与摘要。
    for cid, case in cases.items():
        for kind in ("png", "svg"):
            info = case[kind]
            path = MANUAL / info["file"]
            if not path.exists():
                fail("%s 缺少成品 %s" % (cid, info["file"]))
            if sha256(path) != info["sha256"]:
                fail("%s 的 %s 摘要与清单不符，先重跑渲染器" % (cid, kind))

    # 2a. PNG 等价对逐字节相同。
    for left, right, (mark_l, mark_r), why in PNG_EQUIVALENT:
        hl, hr = cases[left]["png"]["sha256"], cases[right]["png"]["sha256"]
        if hl != hr:
            fail("%s 与 %s 的 PNG 应当相同（%s）" % (left, right, why))
        sl, sr = (MANUAL / cases[left]["svg"]["file"]).read_text(), \
                 (MANUAL / cases[right]["svg"]["file"]).read_text()
        if mark_l not in sl or mark_r not in sr:
            fail("%s/%s 的 SVG 缺少几何标记 %r / %r" % (left, right, mark_l, mark_r))
        if sl == sr:
            fail("%s 与 %s 的 SVG 应当不同才能作为几何证据" % (left, right))

    # 2b. 关键区分像素：两组错误在同一观察点必须读出不同值，且与清单一致。
    for left, lp, right, rp, why in PNG_DISTINGUISHED:
        pl, pr = png_pixel(left, *lp), png_pixel(right, *rp)
        if pl == pr:
            fail("%s 与 %s 在区分点同色，案例失效：%s" % (left, right, why))
        for cid, pt, got in ((left, lp, pl), (right, rp, pr)):
            want = next(p["rgba8"] for p in cases[cid]["probes"]
                        if (p["point"]["x"], p["point"]["y"]) == pt)
            if list(got) != want:
                fail("%s 在 %s 实测 %s 与清单 %s 不符" % (cid, pt, got, want))

    # 3. 探针清单里的所有像素都与实际 PNG 对得上。
    for cid, case in cases.items():
        png = probe.read_png(str(MANUAL / case["png"]["file"]))
        for p in case["probes"]:
            x, y = p["point"]["x"], p["point"]["y"]
            if list(probe.pixel(png, x, y)) != p["rgba8"]:
                fail("%s 探针 (%d,%d) 与清单不一致" % (cid, x, y))

    print("全部通过：%d 个小稿、%d 组 PNG 等价对、%d 组区分点、所有探针与摘要一致"
          % (len(cases), len(PNG_EQUIVALENT), len(PNG_DISTINGUISHED)))


if __name__ == "__main__":
    main()
