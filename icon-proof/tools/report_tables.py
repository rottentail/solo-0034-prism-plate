#!/usr/bin/env python3
"""Emit the markdown tables quoted by REPORT.md, straight from the record.

Keeping the tables generated means the prose cannot drift from the numbers.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
POL = json.loads((ROOT / "policy" / "decision.json").read_text())
FIND = json.loads((ROOT / "record" / "findings.json").read_text())

PAGES = ["light", "dark", "checker", "mid"]
OFFS = ["dx0p0_dy0p0", "dx0p5_dy0p0", "dx0p0_dy0p5", "dx0p5_dy0p5"]
SHORT = {"dx0p0_dy0p0": "0/0", "dx0p5_dy0p0": ".5/0", "dx0p0_dy0p5": "0/.5", "dx0p5_dy0p5": ".5/.5"}
CELLS = {(c["variant"], c["page"], c["offset"]): c for c in POL["cells"]}
ITEMS = {(i["status"], i["variant"], i["offset"]): i for i in FIND["items"]}


def f1(x):
    return "—" if x is None else f"{x:.1f}".rstrip("0").rstrip(".")


def grid():
    out = ["| 变体 | 构造 | light | dark | checker | mid(压力页) |", "|---|---|---|---|---|---|"]
    note = {"plain-ink": "单色深墨", "plain-paper": "单色浅墨", "mid": "单色中灰",
            "fine": "2.0 px 笔画", "hair": "1.5 px 笔画", "thread": "1.0 px 笔画",
            "halo": "浅色描边+深芯", "duplex": "浅描边+中灰内圈", "badge": "不透明底盘",
            "erasure-light": "演示：决定笔画涂成白", "erasure-dark": "演示：决定笔画涂成近黑"}
    for v in [x["variant"] for x in POL["variants"]]:
        r = next(x for x in POL["variants"] if x["variant"] == v)
        row = []
        for pg in PAGES:
            ok = r["per_page"][pg]["pass_all_offsets"]
            row.append("过" if ok else "不过")
        out.append(f"| `{v}` | {note[v]} | " + " | ".join(row) + " |")
    return "\n".join(out)


def decisive_table():
    out = ["| 变体 | 页面 | 墨色反差 | 决定笔画反差 | 决定笔画不透明占比 | 判定 |",
           "|---|---|---|---|---|---|"]
    cases = [("plain-ink", "light", "dx0p0_dy0p0", "approved"),
             ("plain-ink", "dark", "dx0p0_dy0p0", "approved"),
             ("plain-paper", "light", "dx0p0_dy0p0", "approved"),
             ("plain-paper", "dark", "dx0p0_dy0p0", "approved"),
             ("mid", "mid", "dx0p0_dy0p0", "approved"),
             ("halo", "light", "dx0p0_dy0p0", "pending"),
             ("halo", "dark", "dx0p0_dy0p0", "pending"),
             ("erasure-light", "light", "dx0p0_dy0p0", "approved"),
             ("erasure-light", "dark", "dx0p0_dy0p0", "approved"),
             ("erasure-dark", "dark", "dx0p0_dy0p0", "approved"),
             ("erasure-dark", "light", "dx0p0_dy0p0", "approved"),
             ("badge", "mid", "dx0p0_dy0p0", "pending")]
    for v, pg, off, st in cases:
        c = CELLS[(v, pg, off)]
        g = c["statuses"][st]
        out.append(f"| `{v}` / {st} | {pg} | {f1(g['ink_contrast'])} | {f1(g['decisive_contrast'])} | "
                   f"{f1((g['decisive_opaque_fraction'] or 0) * 100)}% | {g['mode']} |")
    return "\n".join(out)


def placement_table():
    out = ["| 变体 / 状态 | 页面 | 0/0 | .5/0 | 0/.5 | .5/.5 |", "|---|---|---|---|---|---|"]
    for v, st, pg in [("fine", "rejected", "light"), ("hair", "approved", "light"),
                      ("thread", "rejected", "light"), ("plain-ink", "approved", "light"),
                      ("mid", "approved", "light"), ("badge", "approved", "light"),
                      ("duplex", "approved", "light")]:
        row = []
        for off in OFFS:
            g = CELLS[(v, pg, off)]["statuses"][st]
            ok = g["mode"] == "intact"
            eye = ITEMS[(st, v, off)]["decisive_alpha"]
            frac = "—" if not eye else f"{eye['opaque_fraction']*100:.0f}%"
            row.append(("过" if ok else "不过") + f" ({frac})")
        out.append(f"| `{v}` / {st} | {pg} | " + " | ".join(row) + " |")
    return "\n".join(out)


def alignment_table():
    out = ["| 探针 | 0/0 | .5/0 | 0/.5 | .5/.5 |", "|---|---|---|---|---|"]
    for orient, width in (("h", 1.0), ("h", 2.0), ("v", 1.0), ("v", 2.0)):
        row = []
        for off in OFFS:
            dx = 0.0 if off in ("dx0p0_dy0p0", "dx0p0_dy0p5") else 0.5
            dy = 0.0 if off in ("dx0p0_dy0p0", "dx0p5_dy0p0") else 0.5
            a = next(x for x in FIND["alignment"]
                     if x["orientation"] == orient and x["width"] == width
                     and x["dx"] == dx and x["dy"] == dy)
            row.append(f"{a['peak_alpha']}/{a['cross_axis_spans']}px")
        name = ("水平条" if orient == "h" else "竖直条") + f" {width} px"
        out.append(f"| {name} | " + " | ".join(row) + " |")
    return "\n".join(out)


def gray_table():
    out = ["| 变体 | light | dark | checker | mid |", "|---|---|---|---|---|"]
    for v in ["plain-ink", "plain-paper", "mid", "fine", "hair", "thread",
              "halo", "duplex", "badge", "erasure-light", "erasure-dark"]:
        row = []
        for pg in PAGES:
            g = next(x for x in FIND["gray_separation"]
                     if x["variant"] == v and x["page"] == pg and x["offset"] == "dx0p0_dy0p0")
            row.append(f"{g['rms']:.1f}")
        out.append(f"| `{v}` | " + " | ".join(row) + " |")
    return "\n".join(out)


def hole_table():
    out = ["| 变体 | 镂空像素最大 alpha | 孔对孔缘 light | dark | checker | mid |", "|---|---|---|---|---|---|"]
    for v in [x["variant"] for x in POL["variants"]]:
        a = ITEMS[("pending", v, "dx0p0_dy0p0")]["hole_alpha"]
        row = []
        for pg in PAGES:
            row.append(f1(CELLS[(v, pg, "dx0p0_dy0p0")]["statuses"]["pending"]["decisive_contrast"]))
        out.append(f"| `{v}` | {a['max_alpha']} | " + " | ".join(row) + " |")
    return "\n".join(out)


def page_choice_table():
    out = ["| 页面 | 推荐（通用透明） | 同页也可用的变体 | 回退 |", "|---|---|---|---|"]
    for pg in ["light", "dark", "checker"]:
        ch = POL["per_page_choice"][pg]
        others = [v for v in ch["passing_variants"] if v != ch["recommended"]]
        out.append(f"| {pg} | `{ch['recommended']}` | "
                   + (", ".join(f"`{v}`" for v in others) or "—")
                   + f" | {ch['fallback'] or '—'} |")
    return "\n".join(out)


def one_asset_line():
    oa = POL["one_asset"]
    return (f"单一透明资产 `{oa['variant']}`：light/dark/checker 三种页面、"
            f"四种放置全部通过，最差决定笔画反差 "
            f"{oa['worst_decisive_contrast'][oa['variant']]:.2f}:1；"
            f"候选为 " + "、".join(f"`{v}`" for v in oa["candidates"]) + "。")


# render the gray and hole tables into the report too
sections = {
    "grid": grid(),
    "decisive": decisive_table(),
    "placement": placement_table(),
    "alignment": alignment_table(),
    "gray": gray_table(),
    "hole": hole_table(),
    "page_choice": page_choice_table(),
    "one_asset": one_asset_line(),
}
(ROOT / "record" / "tables.json").write_text(json.dumps(sections, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

# refresh REPORT.md between the generated-table markers
report = ROOT / "REPORT.md"
if report.exists():
    text = report.read_text(encoding="utf-8")
    for name, body in sections.items():
        start = f"<!-- TABLE:{name} -->"
        end = f"<!-- /TABLE:{name} -->"
        if start in text and end in text:
            pre = text[:text.index(start) + len(start)]
            post = text[text.index(end):]
            text = pre + "\n" + body + "\n" + post
    report.write_text(text, encoding="utf-8")

for name, body in sections.items():
    print(f"===== {name}\n{body}\n")
