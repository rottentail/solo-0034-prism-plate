#!/usr/bin/env python3
"""Assemble the human-readable casebook and method notes from the ledger.

Inputs: cases/index.json, annotations/<case>.json, annotations/ledger.json,
out/manifest.json, annotations/pixel_probe.json.
Outputs: docs/casebook.md, docs/method.md, docs/design-tool-notes.md,
         docs/summary.json
"""
from __future__ import annotations

import json
import pathlib
import sys
from fractions import Fraction as F

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
DOCS = ROOT / "docs"
CASES = ROOT / "cases"
ANN = ROOT / "annotations"
OUT = ROOT / "out"


def frac_display(s):
    if s is None:
        return "—"
    if isinstance(s, int):
        return str(s)
    if "/" in str(s):
        n, d = str(s).split("/")
        v = F(int(n), int(d))
        return f"`{n}/{d}` = {float(v):.6f}"
    return str(s)


def exact(s):
    if s is None:
        return "—"
    if isinstance(s, int):
        return str(s)
    return str(s)


def group_name(g):
    return {
        "A": "A 有向面积与自交/复合",
        "B": "B 朝向、包含、分离、镜像、并集",
        "C": "C 曲线",
        "D": "D 填充规则与重合边界",
        "E": "E 退化与接触家族",
    }.get(g, g)


def topology_rows(entry, ann):
    t = ann["topology"]
    a = ann["areas"]
    cert = ann.get("curve_certificate") or {}
    relation = ""
    if cert.get("applies") and cert.get("pairs"):
        relation = ", ".join(f"{p['relation']}/{p['transfer']}" for p in cert["pairs"])
    return {
        "id": entry["id"], "title": entry["title"], "group": entry["group"],
        "signed": a["canvas_signed_exact"], "signed_f": a["canvas_signed"],
        "flattened": a["flattened_signed"],
        "filled": a["filled_exact"], "filled_f": a["filled"],
        "c_open": t["components_open"], "c_close": t["components_closure"],
        "h_open": t["holes_open"], "h_close": t["holes_closure"],
        "status": ann["status"], "reasons": ann["status_reasons"],
        "region_count": len(ann["regions"]),
        "uncertain_count": len(ann["uncertain"]),
        "bound": ann["approximation"]["error_bound"],
        "clearance": ann["approximation"]["minimum_clearance"],
        "curve_relation": relation,
        "mirrored": any(s.get("determinant", "1") not in (1, "1")
                        for s in ann.get("per_shape", [])),
    }


def pair_relation(a, b):
    same_area = a["signed"] == b["signed"]
    same_topo = (a["c_open"], a["h_open"]) == (b["c_open"], b["h_open"])
    if same_area and not same_topo:
        return "contrast"
    if same_area and same_topo:
        return "control"
    return "different-area"


def main() -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    index = json.loads((CASES / "index.json").read_text())
    ledger = json.loads((ANN / "ledger.json").read_text())["cases"]
    by = {c["id"]: c for c in ledger}
    manifest = json.loads((OUT / "manifest.json").read_text())
    probe = {c["id"]: c for c in json.loads((ANN / "pixel_probe.json").read_text())["cases"]}
    arts = {r["id"]: r for r in manifest["records"]}

    full = []
    for entry in index["cases"]:
        ann = json.loads((ANN / (entry["id"] + ".json")).read_text())
        full.append((entry, ann))

    pair_rows = []
    seen_pairs = set()
    for entry in index["cases"]:
        a = by[entry["id"]]
        for pid in entry["pairs"]:
            if pid == entry["id"]:
                continue
            key = tuple(sorted((entry["id"], pid)))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            b = by[pid]
            same_area = a["canvas_signed_exact"] == b["canvas_signed_exact"]
            same_topo = (a["components_open"], a["holes_open"]) == (b["components_open"], b["holes_open"])
            relation = "contrast" if (same_area and not same_topo) else (
                "control" if same_area else "different-area")
            pair_rows.append({
                "a": entry["id"], "b": pid, "relation": relation,
                "signed": a["canvas_signed_exact"],
                "a_topo": f"{a['components_open']}/{a['holes_open']}",
                "b_topo": f"{b['components_open']}/{b['holes_open']}",
            })

    summary = {
        "cases": len(full),
        "groups": {g: sum(1 for e, _ in full if e["group"] == g) for g in "ABCDE"},
        "status": {s: sum(1 for _, a in full if a["status"] == s)
                   for s in ("determined", "determined-with-contact", "undetermined")},
        "below_pixel": sum(1 for _, a in full if a.get("visibility") == "below-pixel"),
        "contrast_pairs": sum(1 for p in pair_rows if p["relation"] == "contrast"),
        "control_pairs": sum(1 for p in pair_rows if p["relation"] == "control"),
        "pixel_naive_conflicts": sum(1 for c in probe.values() if c["naive_conflict"]),
    }
    (DOCS / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---------------------------------------------------------------- casebook
    lines = ["# 拓扑案例册（棱版合法输入）", ""]
    lines.append("本册只用 `POST /v1/plates` 的合法稿本构成，未新增布尔运算或填充转换。")
    lines.append("所有数字由 `tools/verify.py` 从稿本重算；图像由真实服务 `cmd/prismplate` 生成。")
    lines.append("")
    lines.append(f"- 案例数：**{summary['cases']}**（A {summary['groups']['A']}，B {summary['groups']['B']}，"
                 f"C {summary['groups']['C']}，D {summary['groups']['D']}，E {summary['groups']['E']}）")
    lines.append(f"- 同有向面积、不同拓扑的对照对：**{summary['contrast_pairs']}**")
    lines.append(f"- 判定：determined **{summary['status']['determined']}**，"
                 f"determined-with-contact **{summary['status']['determined-with-contact']}**，"
                 f"undetermined **{summary['status']['undetermined']}**")
    lines.append(f"- 朴素 PNG 连通统计（4/8 两种约定）互相冲突的案例：**{summary['pixel_naive_conflicts']}**")
    lines.append(f"- 几何精确但 512 像素校样必然看不见的案例（填充面积或间隙 < 1 像素）："
                 f"**{summary['below_pixel']}**")
    lines.append("")
    lines.append("每例给出：精确曲线有向面积、折线有向面积、填充面积、开/闭两种接触约定下的"
                 "连通块与孔数、状态与原因、以及产物文件。")
    lines.append("")

    current = None
    for entry, ann in full:
        if entry["group"] != current:
            current = entry["group"]
            lines.append(f"## {group_name(current)}")
            lines.append("")
        row = topology_rows(entry, ann)
        a = ann["areas"]
        t = ann["topology"]
        lines.append(f"### {entry['id']} — {entry['title']}")
        lines.append("")
        lines.append(f"- 问题：{entry['question']}")
        lines.append(f"- 主张：{entry['claim']}")
        lines.append(f"- 填充规则：`{ann['fill_rule']}`；"
                     f"形体数 {len(ann['per_shape'])}；镜像：{'是' if row['mirrored'] else '否'}")
        lines.append(f"- 有向面积（曲线精确，画布）：{frac_display(a['canvas_signed_exact'])}")
        if a["flattened_signed"] != a["canvas_signed"]:
            lines.append(f"- 有向面积（16/24 折线）：{a['flattened_signed']:.6f}"
                         f"（差 {a['canvas_signed'] - a['flattened_signed']:+.6f}）")
        lines.append(f"- 填充面积（折线精确排列）：{frac_display(a['filled_exact'])}")
        if t.get("hole_area_exact") and t["hole_area_exact"] not in ("0", 0):
            lines.append(f"- 孔面积合计（开集读法）：{frac_display(t['hole_area_exact'])}")
        lines.append(f"- 连通块：开 **{t['components_open']}** / 闭 **{t['components_closure']}**；"
                     f"孔：开 **{t['holes_open']}** / 闭 **{t['holes_closure']}**")
        lines.append(f"- 判定：**{ann['status']}**"
                     + (f"；可见性：{ann['visibility']}" if ann.get("visibility") else ""))
        if ann["status_reasons"]:
            lines.append("- 原因：" + "；".join(ann["status_reasons"]))
        if ann.get("curve_certificate", {}).get("applies"):
            for p in ann["curve_certificate"]["pairs"]:
                lines.append(f"- 曲线证书：{p['a']} vs {p['b']} → 真实曲线 `{p['relation']}`，"
                             f"折线传递 `{p['transfer']}`"
                             + (f"，共享点 {p['shared_points']}" if p["shared_points"] else ""))
        lines.append(f"- 折线误差界 {row['bound']:.6g}；最小非接触间距 "
                     + ("—（全部接触）" if row["clearance"] is None else f"{row['clearance']:.6g}"))
        lines.append("- 区域与不确定标注："
                     + f"`annotations/{entry['id']}.svg`（{row['region_count']} 个填充区域，"
                       f"{row['uncertain_count']} 个边界标注）")
        lines.append(f"- 产物：`out/{entry['id']}.png`（{arts[entry['id']]['artifacts']['png']['bytes']} B，"
                     f"sha256 `{arts[entry['id']]['artifacts']['png']['digest'][:16]}…`）、"
                     f"`out/{entry['id']}.svg`")
        pr = probe.get(entry["id"])
        if pr:
            lines.append(f"- PNG 朴素计数：4/8 约定 {pr['png_fg4_bg8']['components']} 块 / "
                         f"{pr['png_fg4_bg8']['holes']} 孔，8/4 约定 {pr['png_fg8_bg4']['components']} 块 / "
                         f"{pr['png_fg8_bg4']['holes']} 孔"
                         + ("（两者不一致，不能作为结论）" if pr["naive_conflict"] else ""))
        lines.append("")

    lines.append("## 手工核对样例")
    lines.append("")
    lines.append("下面三例的数值可以直接用纸笔复核，用来检验工具链没有系统性偏差。")
    lines.append("")
    lines.append("**A1 领结**：顶点 `(80,80) (80,160) (224,16) (224,176)`，"
                 "鞋带公式 `½Σ(x_i y_{i+1} − y_i x_{i+1})` 得")
    lines.append("`½[(80·160−80·80) + (80·16−160·224) + (224·176−16·224) + (224·80−176·80)]"
                 " = ½[6400 − 34560 + 35840 + 3840] = 5760`。")
    lines.append("两瓣面积分别为 `½|…| = 7680` 与 `1920`。")
    lines.append("")
    lines.append("**C1 椭圆**：控制点系数 `k = 2/3` 的三次椭圆每段精确面积是 `5/6·a·b`，"
                 "四段共 `10/3·a·b`。外椭圆 `a=96, b=63` → `20160`；"
                 "内椭圆反向 `a=33, b=24` → `−2640`；合计 `17520`。")
    lines.append("")
    lines.append("**E14 亚像素边框**：外环 `96×80 = 7680`；内环 `95.96×79.96 = 7672.9616`；"
                 "差 `7.0384 = 4399/625`，与工具输出一致。")
    lines.append("")
    lines.append("## 产物对照：SVG 与 PNG")
    lines.append("")
    lines.append("`out/*.svg` 与 `out/*.png` 都来自同一份展开几何。SVG 是矢量，"
                 "因此 E14 的 0.02 单位边框在 SVG 中仍然可见，在 512 像素 PNG 中必然消失；"
                 "C3/C6 的亚像素间隙同理。这说明“PNG 看起来怎样”与“几何拓扑是什么”"
                 "是两个不同的问题。")
    lines.append("")
    lines.append("## 对照对")
    lines.append("")
    lines.append("`contrast` = 有向面积相同、填充拓扑不同（本册要说明的现象）；"
                 "`control` = 有向面积与拓扑都相同（接触前后的拓扑不变，用来固定其它变量）；"
                 "`different-area` = 有向面积也不同（不属于对照，只作上下文）。")
    lines.append("")
    lines.append("| A | B | 关系 | 有向面积 | A 块/孔 | B 块/孔 |")
    lines.append("|---|---|---|---|---|---|")
    for p in pair_rows:
        lines.append(f"| {p['a']} | {p['b']} | {p['relation']} | {exact(p['signed'])} | "
                     f"{p['a_topo']} | {p['b_topo']} |")
    lines.append("")
    (DOCS / "casebook.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------------------------------------------------------------- method
    m = []
    m.append("# 区域与拓扑推导方法")
    m.append("")
    m.append("## 1. 被分析的对象")
    m.append("")
    m.append("产品（`internal/geom.Flatten`）把每条 Q/C 以固定 16/24 步采样为折线，"
             "`internal/raster` 对每个采样点以其形状的逆变换回到局部坐标，再用形状声明的"
             "`fill_rule` 判定。因此“被绘制的填充集”是：")
    m.append("")
    m.append("> 每个形状：`M_s( {q : fill(local_path_s, q, rule_s)} )` 的并集，"
             "其中 `M_s` 是该形状的仿射矩阵。")
    m.append("")
    m.append("本册所有形体都是不透明实色（alpha = 1）、图层 opacity = 1、图层 blend = normal，"
             "所以逐形状的“先画后画”在拓扑上就是并集；若改用半透明叠色，"
             "应把判定对象改成每种颜色的可视区域，而不是并集。")
    m.append("")
    m.append("本册的拓扑结论都针对这个折线集合，而不是 PNG 像素。PNG 是覆盖采样结果，"
             "只用于校样；像素连通性受抗锯齿、4 点覆盖和 4/8 邻接约定影响。")
    m.append("")
    m.append("## 2. 有向面积：曲线精确值")
    m.append("")
    m.append("对每段多项式曲线直接用 Green 定理 `A = ½∮(x dy − y dx)` 做精确有理积分：")
    m.append("")
    m.append("- 直线段 `(x₀y₁ − y₀x₁)/2`；")
    m.append("- 二次/三次段把 `x(t), y(t)` 展开为幂基，`∫(x y′ − y x′)dt` 逐项积分；")
    m.append("- 全程使用 `Fraction`，没有浮点误差。")
    m.append("")
    m.append("对 `k=2/3` 控制点的三次椭圆，精确面积是 `10/3·a·b`；本册 C 组的所有椭圆都用这一族，"
             "因此“椭圆复合轮廓的有向面积”可以手算核对（例如 C1 = 20160 − 2640 = 17520）。")
    m.append("")
    m.append("## 3. 区域：精确竖向条带排列")
    m.append("")
    m.append("1. 用折线（16/24 契约）作为边界，按所有顶点 x 坐标把平面切成开条带；")
    m.append("2. 条带内边是线性的，按竖直次序排序，相邻两条边之间是一个开单元；")
    m.append("3. 每个开通/闭单元用精确有理点做绕数或奇偶判定（`nonzero` / `evenodd`）；")
    m.append("4. 相邻条带的单元按共享竖线上的重叠区间连接：正长度重叠 → 连通；"
             "只共享端点 → 仅在闭包读法下连通；")
    m.append("5. 单元面积用梯形公式精确求和。")
    m.append("")
    m.append("独立校验（`tools/selfcheck.py`）：")
    m.append("")
    m.append("- 面恒等式：`Σ (绕数 × 面积) = 折线有向面积`，单形状全册成立；")
    m.append("- 扫描线：用完全不同的水平扫描 + 绕数中点判定重算填充面积，与排列结果一致；")
    m.append("- 细分收敛：折线有向面积随 8/12 → 16/24 → 32/48 → 64/96 单调逼近曲线精确值。")
    m.append("")
    m.append("## 4. 孔与连通块：两种接触约定")
    m.append("")
    m.append("填充集是路径定义的开集，因此“开”读法是规范答案：")
    m.append("")
    m.append("- 连通块 = 填充开集的连通分量；")
    m.append("- 孔 = 有界、且与填充块共享正长度边界的未填充开分量（外部无界分量不计）。")
    m.append("")
    m.append("“闭”读法（点接触也连、共边也连）对应 8 邻接栅格与人眼的直观。"
             "两者不同时，说明结论只在“接触点是否算同一块”这一自由度下成立——"
             "本册把它标为 `determined-with-contact`，并把接触点逐点列出。")
    m.append("")
    m.append("## 5. 曲线近似到什么程度")
    m.append("")
    m.append("对每段曲线给出弦误差上界：C² 曲线在一段参数区间长度 `1/n` 内，"
             "弦与曲线距离 ≤ `(1/8)(1/n)² max|B″|`；二次 `B″=2(P₀−2P₁+P₂)`，"
             "三次 `B″=6((1−t)d₀+t d₁)`。本册 16/24 契约下界为 0.02–0.06 个画布单位。")
    m.append("")
    m.append("要支撑拓扑判断，还需要把真实曲线与折线的关系证书化：")
    m.append("")
    m.append("- **分离证书**：对两条曲线的控制点盒做精确分支定界；若每对盒的距离都 > 2b，"
             "真实曲线相距 > 2b，折线不可能改变相交结构；")
    m.append("- **相交证书**：在真实曲线上取有理参数点 p，若 p 到折线的距离 > b，"
             "则 p 对真实曲线与折线的绕数相同；若沿曲线取到两个不同绕数的点，"
             "真实曲线必然穿越；")
    m.append("- **相切证书**：共享的精确端点就是真实公共点；其余部分若被 2b 分离，"
             "则相切只发生在该点。")
    m.append("")
    m.append("C3（间隙 ≈ 1e-4 ≪ b）无法通过：真实曲线被证明分离，但间距小于 2b，"
             "折线相交而曲线不相交，因此其折线拓扑不能当作曲线拓扑，判为未定。")
    m.append("")
    m.append("## 6. 仍保留未定的情形")
    m.append("")
    m.append("- 间距小于 2b 的近切（C3）；")
    m.append("- 折线本身在细分之间改变计数（本册未出现，但检测器会报告）；")
    m.append("- 需要“曲线相切但未共享端点”的精确切点（本册只用共享端点或近切，"
             "不猜切点）。")
    m.append("")
    m.append("除此之外的案例，开集计数是精确的；接触自由度单独标注，不混入“有向面积”。")
    m.append("")
    (DOCS / "method.md").write_text("\n".join(m) + "\n", encoding="utf-8")

    # ---------------------------------------------------------------- design notes
    d = []
    d.append("# 给设计工具开发者的解释材料")
    d.append("")
    d.append("## 要避免的推断")
    d.append("")
    d.append(f"1. **用有向面积符号/大小猜孔数**。本册 {summary['contrast_pairs']} 个对照对证明："
             "同有向面积可以有 0/1/2/5 个连通块和 0/1 个孔。A1（+5760）两块零孔，"
             "A2（+5760）一块一孔，A4（0）两块共 6144，B1（+5760）一块零孔。")
    d.append(f"2. **用 PNG 里“看起来像洞”的暗区当孔**。抗锯齿与 4 点覆盖会把 1 px 级的接触、"
             f"缺口抹平；本册有 {summary['pixel_naive_conflicts']} 个案例的像素统计随 4/8 邻接约定翻转，"
             f"另有 {summary['below_pixel']} 个案例几何上成立但校样必然看不见。")
    d.append("3. **把多个形状的有向面积相加后当作并集面积**。B5 两个形状和为 4800 且并集也是 "
             "4800，但 E6/E9/E12 的重叠让并集面积小于有向面积和。")
    d.append("4. **忽略镜像**。镜像翻转有向面积符号而不改变拓扑（B3/B4）；"
             "但如果只比较“面积数值”，B2/B3 这类同值不同拓扑会被误判。")
    d.append("")
    d.append("## 可以采用的判定流程")
    d.append("")
    d.append("1. 以“形状 × 局部坐标”为单位计算有向面积（精确有理数），不要先把轮廓合并；")
    d.append("2. 要孔数/连通块时，用开集语义在展平几何上做区域分解；"
             "把点接触、共边、重合分别记录；")
    d.append("3. 对曲线，计算弦误差界 b，并检查所有关键间距是否 > 2b；"
             "否则不要给出拓扑结论；")
    d.append("4. UI 上区分三档：`determined`、`determined-with-contact`、`undetermined`，"
             "并把接触点/间距不足的位置直接标在画布上；")
    d.append("5. 不要把 PNG 像素连通性当作数据模型；PNG 只作为“这张校样看起来如何”的证据。")
    d.append("")
    d.append("## 数值约定")
    d.append("")
    d.append("- 填充规则：本册同时使用 `nonzero` 与 `evenodd`；D1/D2 同一路径仅规则不同。")
    d.append("- 绕数：`nonzero` 看非零，`evenodd` 看奇偶；两者在自交/重合处分歧最大。")
    d.append("- 接触自由度：开集读法与闭包读法的差异只出现在接触点，"
             "本册在 `annotations/*.svg` 用不同颜色标出横穿、顶点接触、共线与间距不足。")
    d.append("")
    d.append("## 复算")
    d.append("")
    d.append("```sh")
    d.append("python3 -I topology/tools/build_cases.py    # 生成稿本")
    d.append("PATH=/path/to/go/bin:$PATH GO=go python3 -I topology/tools/render_cases.py  # 真实服务出图")
    d.append("python3 -I topology/tools/verify.py        # 精确重算 + 证书")
    d.append("python3 -I topology/tools/selfcheck.py     # 不变量校验")
    d.append("python3 -I topology/tools/pixel_probe.py   # PNG 朴素计数（反例）")
    d.append("python3 -I topology/tools/annotate.py      # 区域/不确定标注")
    d.append("python3 -I topology/tools/report.py        # 本文件与案例册")
    d.append("```")
    d.append("")
    (DOCS / "design-tool-notes.md").write_text("\n".join(d) + "\n", encoding="utf-8")

    print("wrote docs/casebook.md, docs/method.md, docs/design-tool-notes.md, docs/summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
