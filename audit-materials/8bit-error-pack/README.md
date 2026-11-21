# 8 位 PNG 误差材料包（prism-plate 自动比色审计）

面向使用棱版（prism-plate）成品做自动比色的团队。本包用**独立编写、不
复用生产颜色函数**的连续数值参照，把成品 PNG 与浮点真值之间的差异拆成
覆盖采样、颜色转换、8 位编码三段，给出可验证误差界、越界反例与不可外推
范围，并提供低透明像素的正确判读方法。**不修改任何生产算法，也不通过
统一放宽阈值消除差异。**

## 目录

```
8bit-error-pack/
├── sources/                 # 合法源稿（全部通过生产 HTTP 201 准入）
│   ├── quant-probe.json              # 主探针：低透明渐变/深浅底/多层混合
│   ├── code-lattice-probe.json       # 码值边界与 alpha 阈值探针
│   ├── veil-demo.json                # 低透明直通 RGB 反差演示
│   ├── alpha-channel-probe.json      # 透明背景 RGBA：alpha 量化与清零契约
│   └── quant-probe-{edge-shift,recolour,blend-swap,opacity,micro-tint}.json
├── png/                     # 生产服务实际输出的 PNG + 摘要清单
│   ├── *.png
│   ├── quant-probe.16bit-control.png # 16 位对照（控制实验，非生产输出）
│   └── production-manifest.json
├── reference/               # 独立参考实现（纯 Python 标准库，零生产导入）
│   ├── srgb.py              # sRGB 传递函数（binary64 + 50 位十进制两套）
│   ├── composite.py         # 预乘线性光 source-over / multiply / screen
│   ├── geometry.py          # 仿射、16/24 段细分、半开边射线判定
│   ├── raster.py            # 2×2 生产网格与 N×N 稠密网格渲染器
│   ├── metrics.py           # 码值统计、线性光 RMSE、CIEDE2000
│   ├── bounds.py            # 闭式/穷举误差界
│   ├── pngio.py             # 8 位 PNG 解码器（仅标准库）
│   └── png16.py             # 16 位 PNG 编码器（控制实验）
├── analysis/                # 可复现实验脚本（见下方运行顺序）
├── data/                    # 参考数据与实验结果（JSON/CSV/JSONL）
└── docs/
    └── 判读说明.md           # 给调用方的判读规则（先读这份）
```

## 运行环境与复现

- 生产服务：Go 1.24+（本包在 Go 1.27 linux/arm64 实测），仅标准库；
- 参考实现：Python 3.11+，仅标准库，以 `python3 -I` 风格无第三方依赖；
- 网络仅在首次 `go build` 工具链准备时需要，渲染与分析全程离线。

```sh
# 1. 生成源稿（确定性）
python3 analysis/generate_sources.py
# 2. 用未改动的生产服务渲染并归档真实 PNG（需要 go 工具链）
GO_BIN=/path/to/go python3 analysis/fetch_production_pngs.py
# 3. 三段误差拆分（2×2/4×4/8×8 网格，纯 Python，约数分钟）
python3 analysis/run_study.py
# 4. 控制实验：16 位 PNG + 50 位十进制转换参照
python3 analysis/build_controls.py
# 5. 真实 RGBA 成品的 alpha 取整与清零契约
python3 analysis/alpha_channel_study.py
# 6. 反例、阈值陷阱、闭式界验证
python3 analysis/counterexamples.py
# 7. 逐像素数值谱系（CSV + JSONL）
python3 analysis/export_pixel_reference.py
# 8. 一键校验：所有检查应输出 PASS
python3 analysis/verify_pack.py
```

## 关键结论摘要（数字以 data/ 内实测为准）

1. 独立参考对 7 份非 tie 稿的 8 位码值与生产 PNG **逐像素零失配**；两份
   tie 探针因两套 libm `pow` 的 1 ULP 实现差最多相差恰好 1 码，全部落在
   1 码量化包络内。
2. 8 位编码单通道界 `|Δc| ≤ 1/510`；线性光码距不均匀（亮端 254→255
   步长最大，约为最小步长的 29 倍），不可折算成固定线性容差。
3. 直边 2×2 覆盖误差界 1/4（穷举验证）；曲线无闭式界，只能稠密估计。
4. alpha=0.002 的红/蓝薄纱直通 RGB 差 1.0，同底摊平后差约 0.002、
   dE00 远小于 1——低透明像素必须先摊平再度量。
5. 0.5px 硬边平移即越过量化包络，证明覆盖/设计变化不能套用编码界；而
   1/255 面板微改色产生 21744 个恰好 1 码的真实变更像素，与量化不可由
   码距区分——必须先判稿本身份，禁止统一阈值。
6. 任何能吸收全部量化噪声的全局阈值都会放过真实设计变化，禁止统一放宽；
   按 `docs/判读说明.md` 的分阶段规则判读。

## 边界声明

- 稠密网格是**估计器**而非解析面积积分，其 4×4→8×8 收敛残差随数据公布；
- CIEDE2000 仅用于不透明（或已摊平）颜色，不对透明直通值计算；
- 本包不构成完整视觉正确性证明，只覆盖稿本契约内的量化与采样机制；
- 生产仓库代码未被修改，本包不在生产渲染路径上。
