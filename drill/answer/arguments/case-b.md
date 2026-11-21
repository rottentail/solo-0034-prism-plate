# case-b：签样来源演练乙

版面 `press-beta`。接收包：`kit/case-b`。原始事实：`origin/case-b/`。

## 接收包能支持什么

- **结论**：`candidates_only`
- **可认定来源**：无
- **候选**：press-beta revision 1、press-beta revision 3
- **未纳入、无法排除**：press-beta revision 2 (material withheld)、press-beta revision 4 (material withheld)

## 测量事实（由真实服务渲染结果计算）

| 左 | 右 | 字节相同 | 像素相同 | 差异像素 | 包围盒 | 4x 缩略图相同 |
|---|---|---|---|---|---|---|
| R1 `png-r1` | R2 `png-r2` | 否 | 否 | 2 | [60, 40, 61, 40] | 是 |
| R1 `png-r1` | R3 `png-r3` | 是 | 是 | 0 | — | 是 |
| R1 `png-r1` | R4 `png-r4` | 是 | 是 | 0 | — | 是 |
| R2 `png-r2` | R3 `png-r3` | 否 | 否 | 2 | [60, 40, 61, 40] | 是 |
| R2 `png-r2` | R4 `png-r4` | 否 | 否 | 2 | [60, 40, 61, 40] | 是 |
| R3 `png-r3` | R4 `png-r4` | 是 | 是 | 0 | — | 是 |

## 排除与不可消除的歧义

- 原始事实备注（接收者看不到）：R2 与受签样本相差 2 像素，但 R2 未纳入接收包；接收者不能据此排除它。
- 原始事实备注（接收者看不到）：R4 与受签样本像素与字节完全相同，且未纳入接收包；接收者不能排除它。
- 歧义：R1 与 R3 的全部像素与成品字节完全相同，像素证据无法区分。
- 歧义：样本字节同时等于 R1 与 R3 的成品字节；派生记录只自述源自 R1，且不带签名，不能把两者区分开。
- 歧义：R2、R4 未纳入接收包，不能在接收包内排除；接收者必须把它们列为不可排除项。

## 缺少什么证据
- 能把接收样本绑定到具体成品的校样裁决或带认证的交接记录。
- R2、R4 的源稿与成品元数据，用于关闭候选集。

## 原始事实

- 样本出处：labelled rgba8 copy of service artifact png-r1 of press-beta revision 1
- 源成品字节摘要：`beee47beedd486eecaa13ba7c951ef206c3de5d3b1e435bc78d4bae3b7ec500e`
- 源成品 scene_digest：`5f477fb8ef7e996c70c8d2aec4d5c2252267eb214cc79f0c5fae25f8f0800d32`
- 像素摘要：`0ed6703eea988a2915c49c5dd89b45caba2f3ffee4483aae2ce6e1b17061d567`
- 接收包中样本文件摘要：`b1dfccccb68bc912bcf74fd5f5bfa384639d21743615a05b4e15451d6e18c4c3`

## 重跑

```sh
python3 tools/compare.py bind --case kit/case-b --json
python3 tools/compare.py diff --case kit/case-b --left signed/signed-sample.png --right artifacts/press-beta-r1.png
```
