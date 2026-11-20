# 线性渐变锚定转换

## 两种锚定

- `anchor: "object"`（默认，旧稿缺省即此值）：渐变轴定义在形状局部坐标，随形状 `transform` 一起移动、缩放、旋转、镜像。
- `anchor: "canvas"`：渐变轴定义在画布（文档）坐标，形状变换只改变被填充的轮廓，渐变本身固定在画布上。

字段缺省或为空字符串均按 object 解释，旧稿本不需要迁移，PNG/SVG 输出与升级前字节一致。

## 为什么直接搬端点不总是成立

线性渐变在点 x 的取色参数是

```
t(x) = clamp( dot(x - f0, w) / |w|² , 0, 1 )
```

其中 f0 是起点、w = to − from。若把端点直接经过仿射 M 搬动（设计器拖端点的等价操作），新参数使用 Mf0、Mw 和 |Mw|²。只有当 M 为相似变换（平移、旋转、等比缩放、任意镜像组合）时，或轴恰好落在 M 的特征方向上时，参数场才逐点不变。非等比缩放会改变投影比例与归一化分母，颜色分布随之移动；镜像会反转方向但正交变换下投影参数不变，纯镜像不产生误差。

## 精确保留的构造

t 是点的仿射标量场，可逆仿射坐标变换后仍是仿射标量场，因此总可以精确重建。令 x = Bq（object→canvas 时 B = M，canvas→object 时 B = M⁻¹），u = B⁻ᵀw，利用 dot(Ba, b) = dot(a, Bᵀb)：

```
t(q) = ( dot(q, u) − dot(B f0, u) ) / |w|²
```

新轴取 f′ = B f0、w′ = u · |w|² / |u|²，则 |w′|² = |w|² 且 dot(q − f′, w′) 与上式分子相等：参数 t、clamp 区间和色标插值逐点相同，色标与颜色完全不改动。

### 可精确保留的情形（误差仅浮点舍入）

| 变换 | 精确重建 | 直接搬端点也精确 |
|---|---|---|
| 平移 | 是 | 是 |
| 旋转 | 是 | 是 |
| 等比缩放 | 是 | 是 |
| 纯镜像 | 是 | 是（报告 `transform_reflected=true`） |
| 相似组合（旋转+等比+平移±镜像） | 是 | 是 |
| 非等比缩放，轴在特征方向上 | 是 | 是 |
| 非等比缩放/剪切，轴不在特征方向上 | 是 | 否 |

精确重建对任意可逆仿射都成立；若变换奇异（validate 已拒绝，转换接口再次防御）则无法转换。转换结果必须重新通过同一编译准入（坐标有限、轴非退化等），超出有限坐标边界会返回 400，不产生稿本。

### 需要说明的误差

服务不做有损转换，但报告如实量化"如果直接搬端点会怎样"，供设计人员理解朴素操作的偏差：

- `naive_endpoint_move_exact`：朴素端点变换是否保持参数场。
- `naive_max_t_error`：在形状局部包围盒 17×17 采样网格上，朴素法与精确场的最大取色参数差（0..1）。该值是包围区上界估计，不含像素覆盖率近似。
- `naive_axis_drift_degrees`：精确轴与朴素轴的夹角。
- `conformal_ratio`：M 的奇异值比（最大/最小），1 表示共形；越大表示非等比程度越强。
- 转换本身的实际误差仅为 IEEE-754 双精度浮点舍入（典型 1e-15 量级）；PNG 经量化为 8 位通道后，转换前后渲染为字节相同的成品。
- SVG 是矢量校样，浏览器自身的覆盖率与透明插值差异是既有边界，与锚定转换无关。

## 接口

```
POST /v1/plates/{id}/anchor
{
  "expected_revision": 1,
  "layer_id": "anchor-sheet",
  "shape_id": "anisotropic-blade",
  "anchor": "canvas"
}
```

201 响应含追加后的完整版面（新稿本走与 PUT 修订相同的摘要、冲突与容量规则）和一份 report：

```
{
  "plate": { ... },
  "report": {
    "layer_id": "anchor-sheet",
    "shape_id": "anisotropic-blade",
    "from_anchor": "object",
    "to_anchor": "canvas",
    "exact_field_preserved": true,
    "naive_endpoint_move_exact": false,
    "transform_reflected": false,
    "conformal_ratio": 2.43,
    "naive_max_t_error": 0.18,
    "naive_axis_drift_degrees": 12.7,
    "note": "exact field preserved via linear-form reconstruction; ..."
  }
}
```

拒绝情形：目标与当前锚定相同、锚定值非法、层或形状不存在（400）；expected_revision 不存在（404）；版本不匹配（409）；实色笔刷不支持锚定转换（400）。

## PNG 与 SVG 的核对方式

- PNG：转换前后对同一稿本系列渲染，成品摘要逐字节相同（walkthrough 的 anchor 流程对 5 个连续稿本断言同一 PNG 摘要）。
- SVG：object 锚定输出历史标记（无 gradientTransform）；canvas 锚定的渐变轴用画布坐标并附加形状逆矩阵 `gradientTransform="matrix(...)"`，与路径上的 M 复合后等价于画布场。两种锚定的 SVG 标记必然不同，但展开几何与色标一致。
- 持久化：含锚定字段的稿本写入同一快照，重启重编译后渲染摘要不变；封包导入同样经过编译准入。
