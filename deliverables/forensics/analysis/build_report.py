"""Assemble the reproducible forensic manual from measured results.

The prose in FORENSICS.md is hand-written derivation; this script regenerates
every numeric table from render-manifest.json / forensic-results.json so the
document cannot drift from the actual PNG bytes.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import recipes


def fmt_codes(c):
    return "(" + ", ".join(str(v) for v in c) + ")"


def main():
    results = json.load(open(os.path.join(OUT, "forensic-results.json"),
                             encoding="utf-8"))
    manifest = json.load(open(os.path.join(OUT, "render-manifest.json"),
                              encoding="utf-8"))
    lines = []
    add = lines.append

    add("# 附录 A：实测码表（由 build_report.py 从真实 PNG 生成）")
    add("")
    add("每个 RGBA 元组是候选稿在该背景下，96×96 PNG 内点区域 "
        f"{recipes.SAMPLE_BOX} 的唯一像素码（全部内点同码）。")
    add("")

    family_members = {f: [c for c, _ in items]
                      for f, items in recipes.FAMILIES.items()}
    family_members["D"] = [c for c, _ in recipes.FAMILY_D]
    bg_names = [b[0] for b in recipes.BACKGROUNDS]
    measured = results["measured_codes"]

    for fam, members in family_members.items():
        add(f"## A.{fam} 家族")
        add("")
        add("| 候选 \\ 背景 | " + " | ".join(bg_names) + " |")
        add("|" + "---|" * (len(bg_names) + 1))
        for cid in members:
            row = [cid]
            for bg in bg_names:
                row.append(fmt_codes(measured[f"{cid}|{bg}"]))
            add("| " + " | ".join(row) + " |")
        add("")
        # Explicit same/split verdict per background.
        verdicts = []
        for bg in bg_names:
            grp = results["equifinality"][f"{fam}|{bg}"]
            verdicts.append(
                f"{bg}: {'不可分辨' if not grp['distinguishable'] else '可区分'}")
        add("判别结论：" + "；".join(verdicts) + "。")
        add("")

    add("# 附录 B：量化区间反演")
    add("")
    add("## B.1 家族 A 在中灰单背景下，色刷 sRGB 的可接受区间")
    add("")
    add("观测只给出 8 位码；下表对每个候选假设的覆盖度 o，反演仍能产生该码的全部 sRGB 值。")
    add("")
    add("| 通道 | 观测码 | sRGB 量化区间 | 候选 | o | sRGB 可接受区间 | 声明值 |")
    add("|---|---|---|---|---|---|---|")
    for ch in results["family_A_midgray_srgb_inversion"]:
        for row in ch["per_candidate"]:
            add("| {channel} | {code} | {qi} | {cand} | {o} | {iv} | {decl} |".format(
                channel=ch["channel"], code=ch["observed_code"],
                qi=ch["srgb_quant_interval"], cand=row["candidate"],
                o=row["o"], iv=row["admissible_srgb_s"],
                decl=row["declared_srgb"]))
    add("")
    add("## B.2 半透明背景探针（bg-alpha50）反演有效覆盖度 o")
    add("")
    add("输出直通 alpha 满足 a_out = 0.5 + 0.5·o，因此 o = 2·a_out − 1；")
    add("把 alpha 码翻译成 sRGB 区间后得到 o 的闭区间，而不是单点。")
    add("")
    add("| 家族 | 候选 | alpha 码 | o 的量化区间 | 声明 o |")
    add("|---|---|---|---|---|")
    for r in results["alpha_probe_coverage_inversion"]:
        add("| {family} | {candidate} | {code} | {iv} | {decl} |".format(
            family=r["family"], candidate=r["candidate"], code=r["alpha_code"],
            iv=r["coverage_o_interval"], decl=r["declared_o"]))
    add("")

    add("# 附录 C：谱系与可追溯性")
    add("")
    add(f"共 {len(manifest)} 张 PNG，来自 {len({m['plate'] for m in manifest})} "
        "个版面，每张图在 render-manifest.json 中记录：版面 id、稿本号、")
    add("artifact id、PNG 字节 sha256（与 GET /v1/artifacts 的 ETag 同源）、")
    add("scene_digest（稿本摘要）以及对应的源稿 JSON 路径。")
    add("")
    add("| 候选 | 版面 | 背景数 | 源稿样例 |")
    add("|---|---|---|---|")
    plates = {}
    for m in manifest:
        plates.setdefault(m["candidate"], m)
    for cid, m in sorted(plates.items()):
        add(f"| {cid} | {m['plate']} | 8 | {m['scene_source']} |")
    add("")

    with open(os.path.join(OUT, "appendix-tables.md"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(json.dumps({"appendix": "appendix-tables.md",
                      "rows": len(lines)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
