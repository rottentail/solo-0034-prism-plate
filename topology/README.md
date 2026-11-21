# 拓扑案例组（棱版合法输入）

本目录是交付物，不修改产品代码。它只使用 `POST /v1/plates` 的合法稿本，
不新增布尔运算或填充转换。

## 目录

| 路径 | 内容 |
|---|---|
| `cases/` | 35 份合法稿本（`case-*.json`）与 `index.json` |
| `out/` | 真实服务生成的 PNG/SVG（`case-*.png`、`case-*.svg`）与 `manifest.json` |
| `annotations/` | 每例的精确重算结果（`case-*.json`）、区域/不确定叠加图（`case-*.svg`）、栅格预览（`png/`）、`ledger.json`、`pixel_probe.json` |
| `docs/` | `casebook.md`、`method.md`、`design-tool-notes.md`、`summary.json` |
| `tools/` | 生成、出图、重算、自检、像素反例、标注、报告 |

## 案例分组

- **A** 有向面积与自交/复合：领结、反向内环、零面积领结、反向分离形状。
- **B** 朝向、包含、分离、镜像、多形状并集。
- **C** 曲线：三次椭圆精确面积、复合曲线、横穿、浅交、近切、镜像。
- **D** 填充规则与重合边界：五角星 nonzero/evenodd、完全重合双环。
- **E** 四组“分离 → 接触 → 重叠”家族（孔/边界、点接触、共边、曲线外切），
  外加亚像素边框与反向重合双环。

## 结论摘要

- 35 例中 23 对“有向面积相同、填充拓扑不同”的对照（另有 6 对接触前后拓扑不变的控制对）；
- 20 例 `determined`、13 例 `determined-with-contact`、2 例 `undetermined`；
- PNG 朴素连通统计在 4/8 两种邻接约定下互相矛盾的案例有 4 个；
  另有 3 个案例几何上成立但 512 像素校样必然看不见（E14、C3、C6）；
  2 组不同几何产生了逐字节相同的 PNG（C3/C6，D4/E13/E14）。

详见 `docs/casebook.md` 与 `docs/method.md`。

## 复算

```sh
# 1. 生成稿本
python3 -I tools/build_cases.py

# 2. 由本仓库构建服务并出图（需要 Go；CGO 关闭）
CGO_ENABLED=0 go build -trimpath -o /tmp/prismplate ./cmd/prismplate
GO=go python3 -I tools/render_cases.py

# 3. 精确重算、证书、自检
python3 -I tools/verify.py
python3 -I tools/selfcheck.py

# 4. PNG 反例与标注
python3 -I tools/pixel_probe.py
python3 -I tools/annotate.py

# 5. 生成文档
python3 -I tools/report.py
```

`tools/render_cases.py` 会用 `CGO_ENABLED=0 go build -trimpath` 自行构建，并在
临时数据根、临时端口上启动服务；不会写入 `var/` 或示例文件。
