#!/usr/bin/env python3
"""接点校样条独立核对：路程与方向（纯标准库）。

与生成器 proofs/src/generate_proofs.py 不共享任何代码：曲线求值、弧长积分
（自适应辛普森，生成器用固定面板复合辛普森）、朝向与曲率均独立重算，输入只取
proofs/out/manifest.json 与已交付 PNG。

核对项：
  G  几何不变量：接点两侧一阶导数为零、行进方向一致、段2曲率变化显著
  A  路程：ARC 版逐帧弧长间距的均匀性（误差范围）；TIME 版间距分布（对照）
  D  方向：逐帧朝向复算、相邻帧转角连续性（不翻转判据）、接点处朝向连续
  P  位置：由 (seg,t) 复算坐标比对 manifest
  H  交付 PNG 的 SHA-256 与 manifest 记录一致

输出：proofs/out/verification_report.md、spacing_arc.csv、spacing_time.csv、
direction.csv；任一硬性判据失败时退出码为 1。

复跑：python3 proofs/src/verify_proofs.py
"""

import csv
import hashlib
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "proofs", "out")

PASS_TOL_CV = 0.005          # ARC 版间距变异系数判据：0.5%
PASS_TOL_HEADING = 1e-6      # 朝向复算容差（度）
PASS_TOL_POS = 1e-9          # 位置复算容差（px）
NO_FLIP_DEG = 90.0           # 相邻帧转角上限（度），超过视为翻转


# ---------------------------------------------------------------- 独立曲线实现

def bez(cp, t):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = cp
    u = 1.0 - t
    return (u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3,
            u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3)


def bez_d1(cp, t):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = cp
    u = 1.0 - t
    return (3 * (u * u * (x1 - x0) + 2 * u * t * (x2 - x1) + t * t * (x3 - x2)),
            3 * (u * u * (y1 - y0) + 2 * u * t * (y2 - y1) + t * t * (y3 - y2)))


def bez_d2(cp, t):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = cp
    u = 1.0 - t
    return (6 * (u * (x2 - 2 * x1 + x0) + t * (x3 - 2 * x2 + x1)),
            6 * (u * (y2 - 2 * y1 + y0) + t * (y3 - 2 * y2 + y1)))


def adap_simpson(f, a, b, eps=1e-12, max_depth=24):
    """自适应辛普森积分（与生成器的固定面板复合辛普森相互独立）。"""
    if b <= a:
        return 0.0
    fa, fb = f(a), f(b)
    m = 0.5 * (a + b)
    fm = f(m)
    whole = (b - a) * (fa + 4.0 * fm + fb) / 6.0
    result = [0.0]
    stack = [(a, b, fa, fb, fm, whole, eps, 0)]
    while stack:
        a, b, fa, fb, fm, whole, eps, depth = stack.pop()
        m = 0.5 * (a + b)
        lm, rm = 0.5 * (a + m), 0.5 * (m + b)
        flm, frm = f(lm), f(rm)
        left = (m - a) * (fa + 4.0 * flm + fm) / 6.0
        right = (b - m) * (fm + 4.0 * frm + fb) / 6.0
        delta = left + right - whole
        if depth >= max_depth or abs(delta) <= 15.0 * eps:
            result[0] += left + right + delta / 15.0
        else:
            stack.append((m, b, fm, fb, frm, right, eps * 0.5, depth + 1))
            stack.append((a, m, fa, fm, flm, left, eps * 0.5, depth + 1))
    return result[0]


def arc_len(cp, a, b):
    return adap_simpson(lambda t: math.hypot(*bez_d1(cp, t)), a, b)


def curvature(cp, t):
    d1 = bez_d1(cp, t)
    d2 = bez_d2(cp, t)
    sp = math.hypot(*d1)
    if sp < 1e-12:
        return float("nan")
    return abs(d1[0] * d2[1] - d1[1] * d2[0]) / sp ** 3


def heading_of(cp, t, limit_dir):
    """接点端一阶导数为零，朝向取极限方向（与生成器约定相同，独立实现）。"""
    if t <= 0.0 or t >= 1.0:
        d = bez_d1(cp, min(max(t, 0.0), 1.0))
        if math.hypot(*d) < 1e-12:
            d = limit_dir
    else:
        d = bez_d1(cp, t)
    return math.degrees(math.atan2(d[1], d[0]))


def stats(values):
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return {"n": n, "mean": mean, "sd": math.sqrt(var),
            "cv": math.sqrt(var) / mean if mean else float("nan"),
            "min": min(values), "max": max(values),
            "ratio": max(values) / min(values) if min(values) > 0 else float("inf")}


def fmt(v, nd=6):
    if isinstance(v, float):
        return ("%.*f" % (nd, v)).rstrip("0").rstrip(".") if abs(v) < 1e6 else "%.6g" % v
    return str(v)


def main():
    with open(os.path.join(OUT, "manifest.json")) as f:
        man = json.load(f)

    seg1 = [tuple(p) for p in (man["curve"]["seg1"]["P0"], man["curve"]["seg1"]["P1"],
                               man["curve"]["seg1"]["P2"], man["curve"]["seg1"]["P3"])]
    seg2 = [tuple(p) for p in (man["curve"]["seg2"]["Q0"], man["curve"]["seg2"]["Q1"],
                               man["curve"]["seg2"]["Q2"], man["curve"]["seg2"]["Q3"])]
    J = tuple(man["curve"]["joint"])
    jf = man["joint_frame"]
    nf = man["frames_total"]

    checks = []

    def check(cid, name, ok, detail):
        checks.append((cid, name, bool(ok), detail))
        return ok

    # ---- G：几何不变量 -------------------------------------------------
    d1_end = bez_d1(seg1, 1.0)
    d2_start = bez_d1(seg2, 0.0)
    check("G1", "seg1 接点端一阶导数为零", math.hypot(*d1_end) == 0.0,
          "|B1'(1)| = %r（P2=P3=J 的解析结果）" % math.hypot(*d1_end))
    check("G2", "seg2 接点端一阶导数为零", math.hypot(*d2_start) == 0.0,
          "|B2'(0)| = %r（Q0=Q1=J 的解析结果）" % math.hypot(*d2_start))

    arr = (J[0] - seg1[1][0], J[1] - seg1[1][1])          # 到达方向极限 (J-P1)
    dep = (seg2[2][0] - J[0], seg2[2][1] - J[1])          # 离开方向极限 (Q2-J)
    cross = arr[0] * dep[1] - arr[1] * dep[0]
    dot = arr[0] * dep[0] + arr[1] * dep[1]
    ang = math.degrees(math.atan2(abs(cross), dot))
    check("G3", "接点两侧行进方向一致", ang < 0.01 and dot > 0,
          "angle((J-P1),(Q2-J)) = %.9f deg，同向" % ang)

    def max_kappa(cp):
        best = 0.0
        for k in range(3001):
            t = 0.03 + 0.94 * k / 3000          # 避开接点奇异邻域的段内区间
            kap = curvature(cp, t)
            if kap == kap:
                best = max(best, kap)
        return best

    k1, k2 = max_kappa(seg1), max_kappa(seg2)
    check("G4", "段2 有明显曲率变化", k2 > k1,
          "max|kappa| seg1=%.6f seg2=%.6f 1/px（t∈[0.03,0.97]），比值 %.2f" % (k1, k2, k2 / k1))

    # ---- A：路程 -------------------------------------------------------
    L1 = arc_len(seg1, 0.0, 1.0)
    L2 = arc_len(seg2, 0.0, 1.0)
    L = L1 + L2
    mt = man["tuning"]
    check("A0", "独立弧长与 manifest 一致",
          abs(L1 - mt["L1"]) / L < 1e-9 and abs(L2 - mt["L2"]) / L < 1e-9,
          "L1=%.9f (manifest %.9f)  L2=%.9f (manifest %.9f)  L=%.9f"
          % (L1, mt["L1"], L2, mt["L2"], L))
    check("A0b", "弧长比 L1/L = 24/47（接点帧等距前提）",
          abs(L1 / L - jf / (nf - 1)) < 1e-9,
          "L1/L=%.12f  target=%.12f" % (L1 / L, jf / (nf - 1)))

    def frame_arc_pos(fr):
        if fr["seg"] == 1:
            return arc_len(seg1, 0.0, fr["t"])
        return L1 + arc_len(seg2, 0.0, fr["t"])

    spacing_rows = {}
    summaries = {}
    for ver in ("arc", "time"):
        frames = man["versions"][ver]
        svals = [frame_arc_pos(fr) for fr in frames]
        rows = []
        for i in range(1, nf):
            ds = svals[i] - svals[i - 1]
            chord = math.hypot(frames[i]["x"] - frames[i - 1]["x"],
                               frames[i]["y"] - frames[i - 1]["y"])
            rows.append({"interval": i, "frame_a": i - 1, "frame_b": i,
                         "ds_arc_px": ds, "ds_chord_px": chord})
        spacing_rows[ver] = rows
        summaries[ver] = stats([r["ds_arc_px"] for r in rows])
        # 位置复算（P 判据顺带在逐帧循环外统一做）

    sa = summaries["arc"]
    check("A1", "ARC 版按路程近似匀速", sa["cv"] < PASS_TOL_CV,
          "ds_arc mean=%.6f sd=%.3e cv=%.3e min=%.6f max=%.6f max/min=%.6f"
          % (sa["mean"], sa["sd"], sa["cv"], sa["min"], sa["max"], sa["ratio"]))
    st = summaries["time"]
    near = [r["ds_arc_px"] for r in spacing_rows["time"] if abs(r["frame_b"] - jf) <= 4]
    check("A2", "TIME 版（对照）按弧长显著不匀速", st["ratio"] > 3.0,
          "ds_arc mean=%.6f cv=%.4f min=%.6f max=%.6f max/min=%.2f；接点邻域(±4帧) min=%.6f"
          % (st["mean"], st["cv"], st["min"], st["max"], st["ratio"], min(near)))

    joint_arc = man["versions"]["arc"][jf]
    joint_time = man["versions"]["time"][jf]
    check("A3", "第 %d 帧精确落在接点" % (jf + 1),
          joint_arc["x"] == J[0] and joint_arc["y"] == J[1]
          and joint_time["x"] == J[0] and joint_time["y"] == J[1]
          and abs(frame_arc_pos(joint_arc) - L1) < 1e-6,
          "arc 帧 pos=(%r,%r) s=%.9f；time 帧 pos=(%r,%r)；J=(%r,%r)"
          % (joint_arc["x"], joint_arc["y"], frame_arc_pos(joint_arc),
             joint_time["x"], joint_time["y"], J[0], J[1]))

    # ---- P：位置复算 ----------------------------------------------------
    pos_err = 0.0
    for ver in ("arc", "time"):
        for fr in man["versions"][ver]:
            cp = seg1 if fr["seg"] == 1 else seg2
            x, y = bez(cp, fr["t"])
            pos_err = max(pos_err, abs(x - fr["x"]), abs(y - fr["y"]))
    check("P1", "帧位置可由 (seg,t) 复算", pos_err <= PASS_TOL_POS,
          "max |pos 复算 - manifest| = %.3e px" % pos_err)

    # ---- D：方向 --------------------------------------------------------
    lim1 = (J[0] - seg1[1][0], J[1] - seg1[1][1])
    lim2 = (seg2[2][0] - J[0], seg2[2][1] - J[1])
    dir_rows = []
    head_err = 0.0
    max_turn = {"arc": 0.0, "time": 0.0}
    joint_turn = {}
    for ver in ("arc", "time"):
        frames = man["versions"][ver]
        heads = []
        for fr in frames:
            cp = seg1 if fr["seg"] == 1 else seg2
            lim = lim1 if fr["seg"] == 1 else lim2
            hd = heading_of(cp, fr["t"], lim)
            heads.append(hd)
        # 与 manifest 的展开朝向比较（允许 360 整数倍差异）
        for fr, hd in zip(frames, heads):
            diff = hd - fr["heading_deg"]
            diff -= 360.0 * round(diff / 360.0)
            head_err = max(head_err, abs(diff))
        unwrapped = [heads[0]]
        for hd in heads[1:]:
            while hd - unwrapped[-1] > 180.0:
                hd -= 360.0
            while hd - unwrapped[-1] < -180.0:
                hd += 360.0
            unwrapped.append(hd)
        for i in range(1, nf):
            turn = abs(unwrapped[i] - unwrapped[i - 1])
            max_turn[ver] = max(max_turn[ver], turn)
        joint_turn[ver] = (abs(unwrapped[jf] - unwrapped[jf - 1]),
                           abs(unwrapped[jf + 1] - unwrapped[jf]))
        for i, fr in enumerate(frames):
            cp = seg1 if fr["seg"] == 1 else seg2
            t_k = min(max(fr["t"], 1e-3), 1.0 - 1e-3)
            dir_rows.append({"version": ver, "frame": i, "seg": fr["seg"], "t": fr["t"],
                             "heading_deg": unwrapped[i],
                             "dtheta_from_prev": (unwrapped[i] - unwrapped[i - 1]) if i else "",
                             "curvature_1px": curvature(cp, t_k)})

    check("D1", "逐帧朝向可复算", head_err <= PASS_TOL_HEADING,
          "max |heading 复算 - manifest| = %.3e deg" % head_err)
    check("D2", "相邻帧转角连续，图形不翻转",
          max(max_turn["arc"], max_turn["time"]) < NO_FLIP_DEG,
          "max|dtheta| arc=%.4f deg time=%.4f deg（判据 < %.0f deg）"
          % (max_turn["arc"], max_turn["time"], NO_FLIP_DEG))
    check("D3", "朝向过接点连续",
          max(joint_turn["arc"][0], joint_turn["arc"][1],
              joint_turn["time"][0], joint_turn["time"][1]) < NO_FLIP_DEG,
          "接点处 |dtheta| arc: %.4f/%.4f deg, time: %.4f/%.4f deg"
          % (joint_turn["arc"][0], joint_turn["arc"][1],
             joint_turn["time"][0], joint_turn["time"][1]))

    # ---- H：交付 PNG 摘要 ------------------------------------------------
    bad = []
    for rel, want in man["png_sha256"].items():
        path = os.path.join(OUT, rel)
        if not os.path.exists(path):
            bad.append(rel + " (missing)")
            continue
        with open(path, "rb") as f:
            got = hashlib.sha256(f.read()).hexdigest()
        if got != want:
            bad.append(rel)
    check("H1", "交付 PNG 与 manifest 摘要一致", not bad,
          "%d 个文件核验%s" % (len(man["png_sha256"]), "，全部一致" if not bad else "，不符: " + ",".join(bad[:5])))

    # ---- 输出 ------------------------------------------------------------
    for ver in ("arc", "time"):
        mean = summaries[ver]["mean"]
        with open(os.path.join(OUT, "spacing_%s.csv" % ver), "w", newline="") as f:
            wtr = csv.DictWriter(f, fieldnames=["interval", "frame_a", "frame_b",
                                                "ds_arc_px", "ds_chord_px", "ds_over_mean"])
            wtr.writeheader()
            for r in spacing_rows[ver]:
                r2 = dict(r)
                r2["ds_over_mean"] = r["ds_arc_px"] / mean
                wtr.writerow(r2)
    with open(os.path.join(OUT, "direction.csv"), "w", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=["version", "frame", "seg", "t",
                                            "heading_deg", "dtheta_from_prev", "curvature_1px"])
        wtr.writeheader()
        for r in dir_rows:
            wtr.writerow(r)

    all_ok = all(ok for _, _, ok, _ in checks)
    lines = []
    lines.append("# 接点校样条独立核对记录（路程 / 方向）")
    lines.append("")
    lines.append("核对程序 `proofs/src/verify_proofs.py`，与生成器不共享代码；弧长用自适应")
    lines.append("辛普森独立积分，输入仅取 `manifest.json` 与已交付 PNG。判定：**%s**。"
                 % ("全部通过" if all_ok else "存在失败项"))
    lines.append("")
    lines.append("## 判定汇总")
    lines.append("")
    lines.append("| 编号 | 核对项 | 结果 | 数值 |")
    lines.append("|---|---|---|---|")
    for cid, name, ok, detail in checks:
        lines.append("| %s | %s | %s | %s |" % (cid, name, "PASS" if ok else "FAIL", detail))
    lines.append("")
    lines.append("## 逐帧路程间距统计（独立积分 ds_arc，单位 px）")
    lines.append("")
    lines.append("| 版本 | mean | sd | cv | min | max | max/min |")
    lines.append("|---|---|---|---|---|---|---|")
    for ver in ("arc", "time"):
        s = summaries[ver]
        lines.append("| %s | %.6f | %.3e | %.3e | %.6f | %.6f | %.3f |"
                     % (ver, s["mean"], s["sd"], s["cv"], s["min"], s["max"], s["ratio"]))
    lines.append("")
    lines.append("## 结论：两种误差范围的区分")
    lines.append("")
    arc_pct = 100 * max(abs(sa["max"] - sa["mean"]), abs(sa["min"] - sa["mean"])) / sa["mean"]
    arc_pct_s = ("%.4f" % arc_pct) if arc_pct >= 0.001 else ("%.2e" % arc_pct)
    lines.append("- **取样（ARC 版）残差范围**：逐帧弧长间距 cv = %.3e，max/min = %.6f，"
                 "即按路程匀速取样的间距误差在 ±%s%% 以内（仅数值求积残差）。"
                 % (sa["cv"], sa["ratio"], arc_pct_s))
    lines.append("- **原轨迹（TIME 版对照）范围**：同一组等时间画面按弧量的间距 cv = %.4f，"
                 "max/min = %.2f，接点邻域单帧位移最低 %.3f px（均值 %.3f px 的 %.1f%%）。"
                 "减速来自轨迹本身在接点处一阶导数为零，而非取样。"
                 % (st["cv"], st["ratio"], min(near), st["mean"], 100.0 * min(near) / st["mean"]))
    lines.append("- **分镜判据**：镜头里接点附近若出现与 TIME 版同量级（数倍）的减速，应改")
    lines.append("  原轨迹（重定时/重参数化）；若观测偏差只在 ARC 版残差量级（<0.5%）内，")
    lines.append("  则属取样环节，改取样即可。方向核对（D1–D3）表明两版朝向均连续、无翻转，")
    lines.append("  接点帧（第 %d 帧）精确落在接点。" % (jf + 1))
    lines.append("")
    lines.append("证据文件：`evidence_chart.png`（间距/朝向/曲率图）、`contact_arc.png`、")
    lines.append("`contact_time.png`（接触表）、`spacing_arc.csv`、`spacing_time.csv`、")
    lines.append("`direction.csv`。")
    lines.append("")
    with open(os.path.join(OUT, "verification_report.md"), "w") as f:
        f.write("\n".join(lines))

    print("\n".join("| %s %-4s %s" % (cid, "PASS" if ok else "FAIL", name)
                    for cid, name, ok, _ in checks))
    print("report -> proofs/out/verification_report.md")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
