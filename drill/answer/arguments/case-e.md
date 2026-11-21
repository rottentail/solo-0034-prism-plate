# case-e：签样来源演练戊

版面 `press-epsilon`。接收包：`kit/case-e`。原始事实：`origin/case-e/`。

## 接收包能支持什么

- **结论**：`indeterminate`
- **可认定来源**：无
- **候选**：无

## 测量事实（由真实服务渲染结果计算）

| 左 | 右 | 字节相同 | 像素相同 | 差异像素 | 包围盒 | 4x 缩略图相同 |
|---|---|---|---|---|---|---|
| R1 `png-r1` | R2 `png-r2` | 否 | 否 | 1440 | [44, 28, 83, 63] | 否 |

### 外部重渲染对照

| 外部版面 | 文件 | 像素相同 | 字节相同 | 差异像素 |
|---|---|---|---|---|
| `press-epsilon-lookalike` | `lookalike/rebased-approximation.png` | 否 | 否 | 2100 |

外部图即使像素相同，也不等于来源：它没有被任何校样或谱系绑定到接收样本。

## 排除与不可消除的歧义

- 排除 press-epsilon revision 1：全分辨率像素差不为零，可以排除 R1。
- 歧义：R1 被排除后，包内没有可验证的候选；SVG 只是矢量校样，不能证明像素来源。
- 歧义：缩略图只能用于筛查，不能用来认定来源。

## 缺少什么证据
- 受签样本对应的源稿或版面封包。
- 成品元数据（artifact id、scene_digest、字节摘要）或校样裁决。
- 若样本经过重编码，需要可核对的派生记录与原始成品。

## 原始事实

- 样本出处：service artifact png-r2 of press-epsilon revision 2
- 源成品字节摘要：`766c2589452033fa1119125f19490caac803d6e2538478700a1994237b7bd2be`
- 源成品 scene_digest：`dfd3d70f8373030d5aa83c244e2aae0e0e01d400d8afc56ba98fb4aeb31c8531`
- 像素摘要：`73dd258178efad606b73a7f0d15a9e72d5431851402e57b8dae488c0c0f263d0`
- 接收包中样本文件摘要：`766c2589452033fa1119125f19490caac803d6e2538478700a1994237b7bd2be`

## 重跑

```sh
python3 tools/compare.py bind --case kit/case-e --json
python3 tools/compare.py diff --case kit/case-e --left signed/signed-sample.png --right artifacts/press-epsilon-r2.png
```
