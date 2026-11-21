# 交付清单与文件谱系（MANIFEST）

所有结果由本包脚本确定性生成；生产 PNG 由未修改的 `prismplate` 服务通过
其公开 HTTP API 渲染。校验入口：`python3 analysis/verify_pack.py`。

## 源稿（sources/，8 份，均通过生产准入 HTTP 201）

| 文件 | 用途 | 关键构造 |
| --- | --- | --- |
| quant-probe.json | 主探针 | 低透明渐变薄纱、深浅双底、screen/multiply 釉层、分数坐标硬边、三次曲线、组透明度嵌套 |
| code-lattice-probe.json | 码格/alpha 阈值探针 | 半码 tie 邻域取值、alpha 0→1 阈值、深浅两半底 |
| veil-demo.json | 低透明反差 | 同底 alpha=0.002 纯红/纯蓝薄纱 |
| alpha-channel-probe.json | alpha 通道探针 | 透明背景（成品为 RGBA/type6）、alpha 渐变、0→1 阈值、清零契约 |
| quant-probe-edge-shift.json | 设计变体 | 硬边平移 0.5px |
| quant-probe-recolour.json | 设计变体 | 可见薄纱色标改色 |
| quant-probe-blend-swap.json | 设计变体 | screen→multiply |
| quant-probe-opacity.json | 设计变体 | 组透明度 0.35→0.60 |
| quant-probe-micro-tint.json | 阈值陷阱变体 | 不透明面板 1/255 微改色，渲染后全部为 1 码差 |

## 实际 PNG（png/）

- `*.png`：生产服务真实输出，sha256 与 artifact id 记录在
  `png/production-manifest.json`；
- `quant-probe.16bit-control.png`：同一浮点像素改入 16 位码格的**对照件**，
  非生产输出，仅用于证明差异来自 8 位容器；
- 不透明画布由 Go 编码为 color type 2（RGB，无 alpha 通道），含透明像素的
  画布为 type 6（RGBA），本包解码器两种都支持。

## 参考实现（reference/，纯标准库，零生产导入）

| 模块 | 职责 |
| --- | --- |
| srgb.py | sRGB 传递函数：binary64 与 50 位十进制两套 |
| composite.py | 预乘线性光 source-over、multiply、screen、同底摊平 |
| geometry.py | 仿射求逆、Q=16/C=24 段细分、半开边射线判定、包围盒 |
| raster.py | 2×2 生产网格与 N×N 稠密网格渲染、8 位取整契约 |
| metrics.py | 码值统计、线性光 RMSE、CIEDE2000（仅不透明/摊平色） |
| bounds.py | 直边覆盖界穷举、码距步长、摊平闭式界 |
| pngio.py / png16.py | 8 位 PNG 解码 / 16 位 PNG 编码 |

## 数据（data/）

| 文件 | 内容 |
| --- | --- |
| `*.stages.json` | 每稿 E_coverage/E_transfer/E_encoding 三段误差、叠底 dE00、估计器收敛 |
| stage-summary.json | 跨稿汇总 |
| alpha-channel-facts.json | 真实 RGBA 成品的 alpha 取整与清零契约实测 |
| counterexamples.json | 薄纱悖论、越界反例、设计/噪声对照、阈值陷阱、闭式界数值 |
| controls.json | 16 位对照残差、十进制传递函数参照 |
| tie-divergence.json | Go/Python 半码 tie 的 1 码分歧清单（38 个边界） |
| pixel-reference.csv / .jsonl | 12 个代表性像素的完整数值谱系 |

## 分析脚本（analysis/，按 run_all.sh 顺序）

generate_sources → fetch_production_pngs → run_study → build_controls →
tie_analysis（需 Go）→ counterexamples → export_pixel_reference →
verify_pack；另含独立 Go 探针 `gotieprobe/`（只依赖标准库 math）。

## 已知且定性的差异

独立参考与生产 PNG 的分歧只出现在两份专门构造的 tie 探针上，且全部恰好
1 码：`code-lattice-probe`（64 像素）与 `alpha-channel-probe`（384 像素）。
根因是两套 libm 的 `pow` 在半码 tie 点相差 1 ULP（255 个半码边界中 38 个
受影响，见 `data/tie-divergence.json`，复现探针 `analysis/gotieprobe/`）。
这些差异在 1 码量化包络内，属实现间浮点取整语义差，不是设计或合成变化；
其余 7 稿全部逐码一致。
