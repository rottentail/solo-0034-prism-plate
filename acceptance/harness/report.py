#!/usr/bin/env python3
"""Render the committed observation data as the markdown tables used in the
accepting guide, so the prose in docs/ can never drift from the JSON.

    python3 acceptance/harness/report.py

Writes acceptance/observations/tables.md and prints the same content.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OBS = ROOT / "acceptance" / "observations"
V = json.loads((OBS / "verdicts.json").read_text())
M = json.loads((OBS / "measurements.json").read_text())
REF_L = json.loads((OBS / "reference_check.json").read_text())
REF_S = json.loads((OBS / "reference_check_srgb.json").read_text())
FMT = json.loads((OBS / "format_experiment.json").read_text())
SWEEP = json.loads((OBS / "blend_gap_sweep.json").read_text())

VIEWERS = ["librsvg", "resvg", "cairosvg", "chromium", "firefox", "imagemagick"]
MECH_ORDER = [
    ("solid-fill", "实色填充"), ("affine-transform", "仿射实色"),
    ("multiply-over-composited", "multiply 叠在已合成图层上"),
    ("multiply-over-backdrop", "multiply 叠在文档底色上"),
    ("screen-over-backdrop", "screen 叠在文档底色上"),
    ("group-isolation", "组隔离不变量"),
    ("group-multiply", "组内 multiply 展开"),
    ("group-alpha", "组内半透明叠加"),
    ("gradient-linear", "不透明渐变线性光插值"),
    ("gradient-alpha", "透明渐变预乘插值"),
    ("blend-over-transparent", "透明底上的 multiply/screen"),
    ("knockout-hole", "镂空孔洞内部"),
    ("knockout-body", "镂空环体"),
    ("knockout-edge", "镂空边缘扫描"),
]
RELEASE_ZH = {"SVG-with-condition": "SVG（有条件）", "SVG": "SVG（有条件）",
              "PNG-only": "仅 PNG", "PNG-only-for-colour": "结构看 SVG，颜色仅 PNG",
              "SVG-structure-PNG-colour": "结构看 SVG，颜色仅 PNG"}

out = []
w = out.append

w("# 观测表（由 `acceptance/harness/report.py` 从原始 JSON 生成）\n")
w(f"参考模型校验：linear 空间模型对真实 PNG 的最大差 **{REF_L['max_delta_all']}**"
  f"（容差 {REF_L['tolerance']}，故 PNG 可作基准）；同一结构换成 sRGB 画布合成后最大差 "
  f"**{REF_S['max_delta_all']}**，这就是后面反复引用的格式固有差。"
  "两个模型都只用了契约里的公式，没有调用服务代码。\n")

w("## 1. 每种机制在各查看器下的最好结果\n")
w("Δ 为与 Prism PNG 的最大通道差（在 0–255 上）。`模型` 列说明该查看器"
  "精确落在哪个独立合成模型上：`线性` = Prism 记录的模型，`sRGB` = 直接对 sRGB 值做"
  "source-over/混合。`固有差` 是两种模型在同一探针上的差——任何只做 sRGB 画布合成的"
  "查看器都不可能比它更接近 Prism。\n")
w("| 机制 | 判定 | 最好 Δ | 固有差 | 能表现该机制的查看器 | 达到保真度的查看器 | 模型归属 |")
w("|---|---|---:|---:|---|---|---|")
for mech, label in MECH_ORDER:
    c = V["mechanisms"][mech]
    sem = sorted({x["viewer"] for x in c["semantics_ok"]})
    fid = sorted({x["viewer"] for x in c["faithful_ok"]})
    models = "; ".join(f"{k}={','.join(v)}" for k, v in c.get("model_attribution", {}).items())
    w(f"| {label} | {RELEASE_ZH[c['release']]} | {c['best_observed_delta']} | "
      f"{c.get('format_gap', {}).get('max', '-')} | {', '.join(sem) or '—'} | "
      f"{', '.join(fid) or '—'} | {models or '—'} |")

w("\n## 2. 各查看器 × 场景：语义（S）与保真度（F）\n")
w("S = 该查看器表现出该机制；F = 绝对颜色也在阈值内。`own`/`img`/`doc`/`post` 是查看方式，"
  "见第 4 节。\n")
scenes = sorted(V["gates"])
w("| 场景 | " + " | ".join(VIEWERS) + " |")
w("|---|" + "---|" * len(VIEWERS))
for sc in scenes:
    mechs = V["gates"][sc]["mechanisms"]
    cells = []
    for vw in VIEWERS:
        e = V["viewers"].get(vw, {})
        if not e.get("available"):
            cells.append("不可用")
            continue
        by_mode = {}
        for mech in mechs:
            for key, r in e["mechanisms"].get(mech, {}).items():
                scene, mode = key.split(":")
                if scene != sc:
                    continue
                cur = by_mode.setdefault(mode, {"states": [], "fstates": []})
                cur["states"].append(r.get("semantics_state", "pass" if r["semantics_pass"] else "fail"))
                cur["fstates"].append(r.get("fidelity_state", "pass" if r["fidelity_pass"] else "fail"))

        def sem_cell(states):
            if not states:
                return "S?"
            if all(s == "n/a" for s in states):
                return "S–"          # not applicable in this mode
            if any(s == "fail" for s in states):
                return "S✗"
            if any(s == "pass" for s in states):
                return "S✓"
            return "S–"
        def fid_cell(states):
            if not states or all(s == "n/a" for s in states):
                return "F–"
            return "F✗" if any(s == "fail" for s in states) else "F✓"
        cells.append(" ".join(
            f"{m}{sem_cell(v['states'])}{fid_cell(v['fstates'])}"
            for m, v in sorted(by_mode.items())) or "—")
    w(f"| {sc} | " + " | ".join(cells) + " |")

w("\n## 3. 三个关键机制的原始像素\n")
w("### 3.1 组隔离不变量：同色两个不透明形状叠在同一个 0.5 不透明度层里\n")
w("重叠处与单形状处输入完全相同，隔离正确时两者必须相等。\n")
w("单形状像素还必须真的被 0.5 组不透明度压暗过；否则“三点相等”只是因为查看器"
  "整体忽略了组不透明度。Prism 此处为 181,106,116。\n")
w("| 查看器 | 重叠 | 形状 C 单独 | 形状 D 单独 | 三点最大差 | 单形状被压暗 |")
w("|---|---|---|---|---:|---|")
for vw in VIEWERS:
    e = M["viewers"].get(vw, {})
    if not e.get("available"):
        continue
    pr = e["scenes"]["c-group-isolation"]["probes"]
    vals = [pr[n]["per_page"]["white"]["viewer"] for n in ("same-overlap", "same-c-only", "same-d-only")]
    spread = max(max(abs(a - b) for a, b in zip(x, y)) for x in vals for y in vals)
    dimmed = "是" if vals[1][0] < 230 else "否（按全不透明画出）"
    w(f"| {vw} | {vals[0][:3]} | {vals[1][:3]} | {vals[2][:3]} | {spread} | {dimmed} |")

w("\n### 3.2 透明渐变：从两个页面颜色反解直线色与 alpha\n")
w("洋红(1,0,1,1) → 绿(0,1,0,0)。预乘插值下直线色应始终是洋红，只有 alpha 下降；"
  "把颜色和 alpha 各自独立插值则直线色会走向灰绿。Firefox 没有 alpha 截图通道，"
  "下列数值由白/黑两次截图反解，因此仍然可用。\n")
w("| 查看器 | 反解 alpha | 反解直线色 | 直线色仍是洋红 |")
w("|---|---:|---|---|")
for vw in VIEWERS:
    e = M["viewers"].get(vw, {})
    if not e.get("available"):
        continue
    r = (e["scenes"]["e-gradient-alpha"].get("recovered_straight") or {}).get("t05")
    if not r:
        continue
    if r.get("rgb"):
        ok = "是" if r["rgb"][1] <= min(r["rgb"][0], r["rgb"][2]) - 40 else "否"
        w(f"| {vw} | {r['alpha']} | {[round(x) for x in r['rgb']]} | {ok} |")
    else:
        w(f"| {vw} | {r['alpha']} | — | — |")

w("\n### 3.3 换底漂移：同一不透明像素在三张页面颜色下的取值\n")
w("Prism 在自己的透明文档底上合成，不透明结果不应随查看器页面改变。"
  "下表是各查看器在**全部场景**的不透明探针上的最大换底漂移（0 = 不受影响）；"
  "非零只出现在 `f-transparent-backdrop` 上。\n")
w("| 查看器 | own 内联/截图 | img 嵌入 | post 事后叠底 |")
w("|---|---:|---:|---:|")
for vw in VIEWERS:
    e = M["viewers"].get(vw, {})
    if not e.get("available"):
        continue
    s = e["scenes"]
    own = max((sc.get("own_page_spread_opaque", 0) for sc in s.values()), default=0)
    img = max((sc.get("img_page_spread_opaque", 0) for sc in s.values()), default=0)
    post = max((sc.get("post_page_spread_opaque", 0) for sc in s.values()), default=0)
    w(f"| {vw} | {own} | {img if img else '—'} | {post} |")
w("\n`doc`（直接打开文件）只有一个页面，换底不变量在定义上不可测，故不列。"
  "它的作用是另一件事：验证“双击打开”这种最自然的方式是否会把页面底色卷进混合；"
  "结果见 §2 的 `docS–F✓` 组合与下面的场景级结论。")

w("\n## 3.4 场景级结论\n")
w("| 场景 | 结论 | 逐机制 |")
w("|---|---|---|")
for sc, g in V["gates"].items():
    per = "; ".join(f"{m}={RELEASE_ZH.get(r, r)}" for m, r in g["per_mechanism"].items())
    w(f"| {sc} | {RELEASE_ZH.get(g['release'], g['release'])} | {per} |")

w("\n## 4. 格式能力实验：`color-interpolation` 能表达哪一部分线性光\n")
w("Prism 的合成在线性光里；SVG 根节点带 `color-interpolation=\"linearRGB\"`。"
  "下面只改这一个属性，其余字节不变。\n")
pts = {"gradient": (18, 32), "multiply": (32, 22), "screen": (32, 55)}
w("| 浏览器 | 场景 | 原样 | 根改 sRGB | 删除该属性 | 只给渐变加 linearRGB | 只给渐变加 sRGB |")
w("|---|---|---|---|---|---|---|")
for b, files in FMT.get("files", {}).items():
    for scene, variants in files.items():
        vals = []
        for key in ("shipped", "root_sRGB", "root_removed", "gradient_linear", "gradient_sRGB"):
            p = ROOT / variants[key]
            try:
                from PIL import Image
                im = Image.open(p).convert("RGB")
                vals.append(str(im.getpixel(pts[scene])))
            except Exception:
                vals.append("—")
        w(f"| {b} | {scene} | " + " | ".join(vals) + " |")
w("\n结论：渐变中点随该属性移动（184→123），multiply/screen 的像素完全不动。"
  "也就是说 SVG 只能把线性光用于色标插值，无法把图层混合放到线性光里。\n")

w("\n## 5. 整色域固有差扫描\n")
w("遍历全部 256×256 背底/源色对，比较同一公式在线性光与 sRGB 下的结果。"
  "阈值 8（0–255 通道差）。\n")
w("| 运算 | 固有差最大值 | 最坏组合 | ≤8 的比例 | ≤16 的比例 |")
w("|---|---:|---|---:|---:|")
NAME = {"multiply": "multiply", "screen": "screen", "over": "半透明 over（α=25/50/75%）"}
for mode in ("multiply", "screen", "over"):
    d = SWEEP[mode]
    wc = d["worst_case"]
    w(f"| {NAME[mode]} | {d['worst_delta']} | 背底 {wc['backdrop']}、源 {wc['source']}"
      + (f"、α={wc['alpha']}" if mode == "over" else "")
      + f"（线性 {round(wc['linear'])} / sRGB {round(wc['srgb'])}） | "
      f"{d['share_within']['8']:.1%} | {d['share_within']['16']:.1%} |")
w("\n" + SWEEP["reading"] + "\n")

text = "\n".join(out) + "\n"
(OBS / "tables.md").write_text(text)
print(text)
