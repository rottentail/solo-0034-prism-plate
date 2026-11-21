# 给设计工具开发者的解释材料

## 要避免的推断

1. **用有向面积符号/大小猜孔数**。本册 23 个对照对证明：同有向面积可以有 0/1/2/5 个连通块和 0/1 个孔。A1（+5760）两块零孔，A2（+5760）一块一孔，A4（0）两块共 6144，B1（+5760）一块零孔。
2. **用 PNG 里“看起来像洞”的暗区当孔**。抗锯齿与 4 点覆盖会把 1 px 级的接触、缺口抹平；本册有 4 个案例的像素统计随 4/8 邻接约定翻转，另有 3 个案例几何上成立但校样必然看不见。
3. **把多个形状的有向面积相加后当作并集面积**。B5 两个形状和为 4800 且并集也是 4800，但 E6/E9/E12 的重叠让并集面积小于有向面积和。
4. **忽略镜像**。镜像翻转有向面积符号而不改变拓扑（B3/B4）；但如果只比较“面积数值”，B2/B3 这类同值不同拓扑会被误判。

## 可以采用的判定流程

1. 以“形状 × 局部坐标”为单位计算有向面积（精确有理数），不要先把轮廓合并；
2. 要孔数/连通块时，用开集语义在展平几何上做区域分解；把点接触、共边、重合分别记录；
3. 对曲线，计算弦误差界 b，并检查所有关键间距是否 > 2b；否则不要给出拓扑结论；
4. UI 上区分三档：`determined`、`determined-with-contact`、`undetermined`，并把接触点/间距不足的位置直接标在画布上；
5. 不要把 PNG 像素连通性当作数据模型；PNG 只作为“这张校样看起来如何”的证据。

## 数值约定

- 填充规则：本册同时使用 `nonzero` 与 `evenodd`；D1/D2 同一路径仅规则不同。
- 绕数：`nonzero` 看非零，`evenodd` 看奇偶；两者在自交/重合处分歧最大。
- 接触自由度：开集读法与闭包读法的差异只出现在接触点，本册在 `annotations/*.svg` 用不同颜色标出横穿、顶点接触、共线与间距不足。

## 复算

```sh
python3 -I topology/tools/build_cases.py    # 生成稿本
PATH=/path/to/go/bin:$PATH GO=go python3 -I topology/tools/render_cases.py  # 真实服务出图
python3 -I topology/tools/verify.py        # 精确重算 + 证书
python3 -I topology/tools/selfcheck.py     # 不变量校验
python3 -I topology/tools/pixel_probe.py   # PNG 朴素计数（反例）
python3 -I topology/tools/annotate.py      # 区域/不确定标注
python3 -I topology/tools/report.py        # 本文件与案例册
```

