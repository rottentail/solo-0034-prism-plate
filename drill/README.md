# 离线签样来源取证演练（棱版 Prism Plate）

这套材料的用途是训练和考核“接收离线签样的人”能否正确归因：**既不把资料缺失当篡改，也不把最像的一张重渲染图当作来源证明**。所有图像、响应与摘要都由真实棱版 HTTP 服务产生，接收包是真实会话的节选，答案来自同一批原始事实。

生产代码、`examples/` 与 `scripts/` 均未修改；本目录只新增材料。

## 目录

| 路径 | 给谁看 | 内容 |
|---|---|---|
| `kit/case-*/` | 接收者 | 节选源稿、PNG/SVG、零散响应、派生记录、缩略图、工作表 |
| `origin/` | 出题人/复核人 | 真实会话的完整原始事实：全部请求响应、源稿、成品字节、持久化快照 |
| `answer/` | 阅卷人 | 逐例答案、逐例论证、原始出处 |
| `lib/` | 工具 | PNG 编解码、Go 兼容摘要、真实服务驱动、场景库 |
| `tools/` | 接收者/复核人 | 构建、取证比对、反事实探针、交付校验 |
| `METHOD.md` | 接收者/复核人 | 采证与比对方法、能排除什么、缺证歧义、复核步骤 |

`kit/` 与 `answer/`、`origin/` 物理分离；`tools/verify.py` 会检查 `kit/` 中不含答案标记。

## 快速开始

```sh
# 1) 构建一次（需要先构建服务二进制；不会改动仓库内生产代码）
GOFLAGS=-mod=mod CGO_ENABLED=0 go build -o /tmp/prismplate ./cmd/prismplate
python3 drill/tools/build_drill.py --binary /tmp/prismplate

# 2) 作为接收者做一次比对
python3 drill/tools/compare.py inspect --case drill/kit/case-d
python3 drill/tools/compare.py diff --case drill/kit/case-d \
    --left signed/signed-sample.png --right artifacts/press-delta-r1.png
python3 drill/tools/compare.py bind --case drill/kit/case-d

# 3) 校验交付物（回执、结论、答案隔离）
python3 drill/tools/verify.py

# 4) 反事实探针：看哪些篡改抓得到、哪些抓不到
python3 drill/tools/tamper_probe.py --case drill/kit/case-a
python3 drill/tools/tamper_probe.py --case drill/kit/case-c
```

`compare.py bind` 只读取 `--case` 指向的接收包，不打开 `answer/` 或 `origin/`。

## 五种案例

| 案例 | 训练点 | 正确结论（仅凭接收包） |
|---|---|---|
| `case-a` | 完整谱系 + 已接受校样 + 原始成品 | 足以认定：`press-alpha revision 2` |
| `case-b` | 遮挡改色 / 零 alpha 层产生**同像素同字节**的另一稿；部分稿本未纳入 | 只能列候选：R1、R3；R2、R4 未纳入、不可排除 |
| `case-c` | 像素相同、**编码不同**的带标注派生副本 | 足以认定：`press-gamma revision 1` |
| `case-d` | 换位造成 2 像素差，**4x 缩略图相同**；另有同像素外部重渲染 | 足以认定：`press-delta revision 2`，但像素不唯一 |
| `case-e` | 只有部分源稿与 SVG，缺受签样本的源稿与元数据 | 无法判断：只能排除 R1 |

关键事实（均由服务真实渲染测得，`answer/arguments/*.md` 有逐例表格）：

- 乙案 R1 与 R3 的成品**字节完全相同**，但 `scene_digest` 与成品 id 不同：像素/字节证据无法区分不同源稿。
- 丙案样本与源成品**像素相同、字节不同**，差在 PNG 容器编码；派生记录解释差异，但它只是自述字段。
- 丁案 R1/R2 仅差 2 像素、包围盒 `[60,40,61,40]`，而 4x 缩略图完全相同；缩略图不能作身份证据。
- 丁案另有一张来自**别的版面**、与样本**像素与字节都相同**的图，但它没有谱系或校样绑定，因此不能算来源证明。

## 判定的三级语义

`compare.py bind` 输出三种结论之一：

1. **`provenance_established`（足以认定）**：存在已接受校样把样本绑定到某个成品 id，样本与该成品字节相同，且同版面内其余稿本均被全分辨率像素差排除，接收包声明本版无遗漏稿本。它证明的是**成品级绑定**，不是“世界上只有这张图”。
2. **`candidates_only`（只能列候选）**：像素能匹配一个或多个稿本，但缺校样/元数据绑定，或存在未纳入、无法排除的稿本。未纳入项单独列为 `withheld_unresolved`，不混入“已匹配候选”。
3. **`indeterminate`（现有材料无法判断）**：包内没有能匹配的候选，且缺证据无法闭合；只能说明已排除了什么。

保证级别写在输出的 `guarantees` 字段里，例如“像素级唯一性未成立”。不要把第 1 档读成第 2 档之外的更强主张。

## 编码副本与资料缺失

- 带派生记录的副本在 `derivations/` 中说明来源、变换和像素一致性；记录本身不带签名，只能解释字节差，不能替代校样或交接记录。
- `manifest.json` 的 `source_excerpt` 明确列出 `omitted_revision_numbers`；`responses.withheld` 只登记“调用发生过”，不提供其中任何稿本文本。
- 未纳入 ≠ 删除 ≠ 篡改。接收包缺什么，结论里就必须登记什么，否则只能降级为“候选”或“无法判断”。

## 可重跑与不可重跑

- 可复现：每个稿本的 `scene_digest`、成品 id、成品字节摘要、像素摘要、缩略图摘要。重跑后逐项一致（已验证）。
- 不可复现：`created` 时间戳，以及依赖它的版面封包 `digest`；`origin/state.json` 中的时间同理。这不影响像素与场景事实。

## 不修改生产来源规则

本演练没有改动 `internal/`、`cmd/`、`examples/` 或 `scripts/`。生成器只通过公开 HTTP 契约提交场景，读取公开响应；摘要复算工具是独立实现，用于让接收者能自行核对，不写入服务。
