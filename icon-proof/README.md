# 签样终端图标判读包（icon-proof）

三态 16×16 图标的可编辑源稿、棱版实际成品、以及逐格判读记录。
结论与判据见 **[REPORT.md](REPORT.md)**。

```sh
cd icon-proof
python3 tools/run_all.py      # 渲染 → 测量 → 判据 → 证据图 → 复核
```

流水线会在临时数据根启动真实的棱版服务（`cmd/prismplate`），所以需要 Go 1.24+
在本机 `PATH` 上；不需要网络、不需要第三方 Python 包。

## 目录

```
src/design.py            可编辑源稿（唯一真源：几何、颜色、笔画、变体）
out/plates/*.json        132 份棱版场景文档：可直接 POST/PUT 的“实际稿”
out/instruments/*.json   测量用的场景文档（局部底色、缩减稿、探针）
out/icons/*.png          132 张 16×16 实际成品（服务渲染）
out/local/*.png          局部底色探针（某部件下方图形的真实渲染）
out/reduced/*.png        缩减稿：去掉决定状态那一笔后的同版场景
out/probes/*.png         水平/竖直 × 1 px/2 px × 四种半像素位置的对齐探针
out/contact/*.png        四种底色 × 两种位置的接触表
out/manifest.json        每份稿的版面、稿本摘要、成品摘要、PNG 摘要
record/findings.json     逐格判读结果（反差、灰度、消失探针、笔画不透明度）
record/measurements.csv  同上，扁平表
record/sheet-*.png       四张证据图
record/policy-table.md   自动生成的判据表
record/tables.json       报告中引用的表格（自动生成）
policy/decision.json     终端可直接消费的选型判据
tools/*.py               渲染、测量、判据、证据图、复核
REPORT.md                判读记录正文
```

## 结论速览

- 一枚通用透明图标可用：`mid`（中灰 `#6E7580`）在浅色列表、深色校样、透明棋盘三种底色
  与四种半像素位置上全部通过；`duplex` 是并列候选。
- 单色深浅墨（`plain-ink` / `plain-paper`）各自只在一种底色成立，必须按底色切换版本。
- 细笔画（1.0–2.0 px）在任一轴的 0.5 px 位移下都会失去可判读性，回退到“图标 + 文字状态”。
- `halo` 在浅底上“图标还在但镂空没了”：这是“恰好抹掉决定状态那一笔”的实际例子，
  与“整枚图标变淡”由消失探针区分（判据第 6 节）。
