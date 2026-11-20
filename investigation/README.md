# investigation/

本目录保存“百万级仿射缩放下 SVG 几何退化”问题的全部复现材料，结论与证据
分级见 `FINDINGS.md`。

## 布局

- `repro/` — 可直接 POST 给服务的复现/对照稿（4 份）
  - `quad-degenerate-repro.json`：题面原案（det=100，SVG 塌缩成线）
  - `quad-degenerate-neighbor.json`：第二个仍合法但同样塌缩的变换（det=-400）
  - `quad-control-noncolliding.json`：同舍入网格、token 不碰撞，但 SVG 几何 10× 错误
  - `quad-workaround-baked.json`：预烘焙坐标 + 单位矩阵的规避对照
- `probe/` — Go 探针，直接调用真实 `internal/validate` 与 `internal/vector`
  代码路径（不复制序列化逻辑），输出扰动扫描与舍入边界
- `results/` — 所有机器生成的证据：真实 HTTP 成品（PNG/SVG）、像素统计、
  查看器重算、四层精度 JSON、CSV/TXT 扫描结果
- 分析脚本在仓库 `scripts/`：`precision_investigation.py`、`png_coverage.py`、
  `svg_viewer_check.py`、`verify_on_service.py`

## 复现步骤

```sh
go build -o /tmp/prismplate/bin/prismplate ./cmd/prismplate
/tmp/prismplate/bin/prismplate -listen 127.0.0.1:7846 -data /tmp/prismplate/data &
python3 -I scripts/verify_on_service.py
python3 -I scripts/precision_investigation.py | tee investigation/results/precision-layers.json
go run ./investigation/probe/ | tee investigation/results/perturbation-sweep.csv
go run ./investigation/probe/boundary/ | tee investigation/results/rounding-boundaries.txt
```

所有 Python 脚本仅用标准库，按要求以 `python3 -I` 运行。
探针构建产物应输出到仓库外，不要把二进制提交进来。
