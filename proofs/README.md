# 接点校样条（prism-plate proof strip）

分镜问题：镜头在接点附近看起来的减速，来自**原轨迹**还是**取样**？本目录交付
一组可复跑校样，让带非对称镂空的棱版图形沿两段相接的三次贝塞尔曲线移动，
用两套 48 帧画面加独立核对记录给出可区分的误差范围。

## 复跑

```sh
python3 proofs/src/generate_proofs.py   # 生成 96 帧、接触表、证据图、manifest
python3 proofs/src/verify_proofs.py     # 独立核对：路程与方向，写报告与 CSV
```

仅依赖 Python 3 标准库；渲染约定与仓库棱版引擎一致（每像素 2×2 覆盖采样、
线性光合成、nonzero 填充、screen 叠色，见 internal/raster、internal/paint）。
全部输出写入 `proofs/out/`，PNG 字节确定，可逐位复现（manifest 记录 SHA-256）。

## 设计（512×512 画布）

- 段1 `P0 P1 J J`、段2 `J J Q2 Q3`：两段在接点 J 处一阶导数均为零
  （`B1'(1)=3(P3−P2)=0`，`B2'(0)=3(Q1−Q0)=0`）。
- 靠近接点行进方向一致：`(J−P1)` 与 `(Q2−J)` 平行同向（夹角 0°）。
- 段1 平缓（P1 取在 P0→J 连线 30% 处法偏 18px）；段2 下压后急转上挑，
  曲率变化显著（独立核对测得段内 max|κ| 比值 4.89）。
- 调参 `mu`（Q3 位置）使 `L1/(L1+L2)=24/47`，因此 48 帧按弧长严格等距，
  且第 25 帧（序号 24）正好落在接点；两版该帧位置都精确等于 J。
- 图形只旋转不镜像；接点处朝向取极限方向，全程朝向连续、不翻转
  （相邻帧最大转角 13.6°，判据 <90°）。
- 非对称镂空：偏心圆孔 + 尾缘三角缺口 + 斜置长槽，任何翻转立即可见。

## 两套画面

| 版本 | 取样 | 用途 |
|---|---|---|
| `out/arc/frame_00..47.png` | 按路程（弧长）匀速 | 正式校样：48 帧带时间标记 |
| `out/time/frame_00..47.png` | 等参数 t（等时间） | 对照：画面平顺但按弧长不匀速 |

每帧 HUD 标有帧号、归一化时间 T、24fps 时间码、路程 S、朝向 HDG、与上一帧
的位移 DS；底部进度条白色=帧时间、琥珀=路程进度（ARC 版二者重合，TIME 版
在接点附近分离）。接点帧有 `JOINT HIT` 标记。

## 独立核对（`out/verification_report.md`）

`verify_proofs.py` 与生成器不共享代码（自适应辛普森独立积分），从
`manifest.json` 与交付 PNG 重算并判定 14 项：几何不变量（G1–G4）、路程
（A0–A3）、位置（P1）、方向（D1–D3）、PNG 摘要（H1）。关键数值：

| 量 | ARC（按路程） | TIME（等时间） |
|---|---|---|
| 逐帧弧长间距 mean | 10.052161 px | 10.052161 px |
| 间距 cv | 1.3e-14 | 0.5378 |
| 间距 max/min | 1.000000 | 35.10 |
| 接点邻域最小单帧位移 | 10.05 px | 0.78 px（均值的 7.8%） |

**判据**：接点附近若观测到与 TIME 版同量级（数倍）的减速 → 原轨迹在接点处
一阶导数为零所致，应改原轨迹（重定时/重参数化）；若偏差只在 ARC 版残差量级
（<0.5%）内 → 属取样环节，改取样即可。

## 文件清单

```
proofs/src/generate_proofs.py   生成器（曲线、取样、渲染、图表、manifest）
proofs/src/verify_proofs.py     独立核对（路程/方向/摘要，退出码即判定）
proofs/out/arc/                 48 帧正式校样（按路程匀速）
proofs/out/time/                48 帧对照（等时间）
proofs/out/contact_arc.png      接触表（8×6）
proofs/out/contact_time.png     接触表（8×6）
proofs/out/evidence_chart.png   间距 / 朝向 / 曲率证据图
proofs/out/manifest.json        全部取样数据与 PNG 摘要
proofs/out/verification_report.md  独立核对记录（判定与结论）
proofs/out/spacing_arc.csv      逐间距核对（ARC）
proofs/out/spacing_time.csv     逐间距核对（TIME）
proofs/out/direction.csv        逐帧朝向 / 转角 / 曲率核对
```
