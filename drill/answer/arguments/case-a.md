# case-a：签样来源演练甲

版面 `press-alpha`。接收包：`kit/case-a`。原始事实：`origin/case-a/`。

## 接收包能支持什么

- **结论**：`provenance_established`
- **可认定来源**：press-alpha revision 2
- **候选**：press-alpha revision 2
- **保证**：成品级绑定：样本就是校样已接受的那一件成品（按成品 id 与字节摘要）。
- **保证**：同版面内唯一：包内其余稿本的成品均与样本存在全分辨率像素差。

## 测量事实（由真实服务渲染结果计算）

| 左 | 右 | 字节相同 | 像素相同 | 差异像素 | 包围盒 | 4x 缩略图相同 |
|---|---|---|---|---|---|---|
| R1 `png-r1` | R2 `png-r2` | 否 | 否 | 144 | [60, 44, 71, 55] | 否 |

## 排除与不可消除的歧义

- 排除 press-alpha revision 1：可见校样标记颜色不同，全分辨率像素差不为零。
- 歧义：服务没有身份认证，摘要链只能证明包内谱系一致，不能证明保管链或排除整包伪造。

## 缺少什么证据
- 在本例范围内没有发现会改变结论的缺证。

## 原始事实

- 样本出处：service artifact png-r2 of press-alpha revision 2
- 源成品字节摘要：`5bba77509695c5437bf3facb7b4010aae0975f50f70b9c4bc9966ec63758325c`
- 源成品 scene_digest：`b9f92b7269314f383f1d010265bd9fa2571d5d36e15c0bc8dbb6a31d64bd142a`
- 像素摘要：`d789373b8ec37439771c7c2f69b23507a0fa42a0df67b4a58e2f4e9fe1035cfe`
- 接收包中样本文件摘要：`5bba77509695c5437bf3facb7b4010aae0975f50f70b9c4bc9966ec63758325c`

## 重跑

```sh
python3 tools/compare.py bind --case kit/case-a --json
python3 tools/compare.py diff --case kit/case-a --left signed/signed-sample.png --right artifacts/press-alpha-r2.png
```
