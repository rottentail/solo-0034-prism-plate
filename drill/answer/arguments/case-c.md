# case-c：签样来源演练丙

版面 `press-gamma`。接收包：`kit/case-c`。原始事实：`origin/case-c/`。

## 接收包能支持什么

- **结论**：`provenance_established`
- **可认定来源**：press-gamma revision 1
- **候选**：press-gamma revision 1
- **保证**：成品级绑定：样本就是校样已接受的那一件成品（按成品 id 与字节摘要）。
- **保证**：同版面内唯一：包内其余稿本的成品均与样本存在全分辨率像素差。

## 测量事实（由真实服务渲染结果计算）

| 左 | 右 | 字节相同 | 像素相同 | 差异像素 | 包围盒 | 4x 缩略图相同 |
|---|---|---|---|---|---|---|
| R1 `png-r1` | R2 `png-r2` | 否 | 否 | 1152 | [46, 30, 81, 61] | 否 |

## 排除与不可消除的歧义

- 排除 press-gamma revision 2：可见校样标记颜色不同，全分辨率像素差不为零。
- 歧义：派生记录是自述字段，不带密码学签名；它解释编码差，但不能单独证明保管链。

## 缺少什么证据
- 在本例范围内没有发现会改变结论的缺证。

## 原始事实

- 样本出处：labelled rgba8 copy of service artifact png-r1 of press-gamma revision 1
- 源成品字节摘要：`e55bbfd2e0a22a40db6d6df0b09a20779842cc0ee609e36a1a7aa8183cb7a642`
- 源成品 scene_digest：`a95b072906d2eada7bde802402b428fec568575b2fbdf56a9fc8c838ce7996e4`
- 像素摘要：`810e1d328b1e5ae0fba454a109e497881007d4a3833c8980d76c6a4a127a2997`
- 接收包中样本文件摘要：`3c0254fd166184e0e39b8f9f57629ae5bfe1c6d2cf1aa9d2be874cacd41f2902`

## 重跑

```sh
python3 tools/compare.py bind --case kit/case-c --json
python3 tools/compare.py diff --case kit/case-c --left signed/signed-sample.png --right artifacts/press-gamma-r1.png
```
