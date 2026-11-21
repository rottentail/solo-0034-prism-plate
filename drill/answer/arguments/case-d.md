# case-d：签样来源演练丁

版面 `press-delta`。接收包：`kit/case-d`。原始事实：`origin/case-d/`。

## 接收包能支持什么

- **结论**：`provenance_established`
- **可认定来源**：press-delta revision 2
- **候选**：press-delta revision 2
- **保证**：成品级绑定：样本就是校样已接受的那一件成品（按成品 id 与字节摘要）。
- **保证**：同版面内唯一：包内其余稿本的成品均与样本存在全分辨率像素差。
- **保证**：像素级唯一性未成立：包外 `unfiled/other-plate-same-pixels.png`（press-delta-unfiled）与样本像素相同，说明相同像素也可由另一版面产生。
- **保证**：结论是“样本就是被接受的该成品”，不是“只有它能产生这些像素”。

## 测量事实（由真实服务渲染结果计算）

| 左 | 右 | 字节相同 | 像素相同 | 差异像素 | 包围盒 | 4x 缩略图相同 |
|---|---|---|---|---|---|---|
| R1 `png-r1` | R2 `png-r2` | 否 | 否 | 2 | [60, 40, 61, 40] | 是 |
| R1 `png-r1` | R3 `png-r3` | 是 | 是 | 0 | — | 是 |
| R2 `png-r2` | R3 `png-r3` | 否 | 否 | 2 | [60, 40, 61, 40] | 是 |

### 外部重渲染对照

| 外部版面 | 文件 | 像素相同 | 字节相同 | 差异像素 |
|---|---|---|---|---|
| `press-delta-unfiled` | `unfiled/other-plate-same-pixels.png` | 是 | 是 | 0 |

外部图即使像素相同，也不等于来源：它没有被任何校样或谱系绑定到接收样本。

## 排除与不可消除的歧义

- 排除 press-delta revision 1：缩略图相同，但全分辨率在 (60,40)、(61,40) 两像素不同。
- 排除 press-delta revision 3：与 R1 像素相同，因此同样被上述两像素差排除。
- 歧义：缩略图不是身份证据；若只给缩略图，R1 与受签样本不可区分。
- 歧义：包外另有一张像素相同的重渲染图；没有谱系或校样把它与受签样本绑定。

## 缺少什么证据
- 在本例范围内没有发现会改变结论的缺证。

## 原始事实

- 样本出处：service artifact png-r2 of press-delta revision 2
- 源成品字节摘要：`b44b2d7398e0a78893bab30c28960dfafadaee11609e5acdcec8ff6b9f6a80c5`
- 源成品 scene_digest：`3293ca2ed5fa125e820d0fa0dbe53221eaa560fe1518347b2e7729819acfcfe5`
- 像素摘要：`d1b12825c68f9baeef99d3d5dd87e05b21a2075c36666c9ca13708b698091b12`
- 接收包中样本文件摘要：`b44b2d7398e0a78893bab30c28960dfafadaee11609e5acdcec8ff6b9f6a80c5`

## 重跑

```sh
python3 tools/compare.py bind --case kit/case-d --json
python3 tools/compare.py diff --case kit/case-d --left signed/signed-sample.png --right artifacts/press-delta-r2.png
```
