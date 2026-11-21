# 颜色模型迁移与回退路径

本文件记录 prism-plate v1（直通 sRGB）到 v2（显式版本、线性光预乘）的迁移契约、回退边界，以及新表示无法恢复的信息。

## 1. 两种文档模型

| | v1（旧稿） | v2（新稿） |
|---|---|---|
| 判定 | `document.color_model` 缺省 | `{"version":2,"space":"linear-srgb","alpha":"premultiplied"}` |
| RGB 编码 | sRGB 直通（0..1） | 线性光（0..1） |
| 预乘 | 否，采样时乘以 alpha | 是，存储值已乘 alpha |
| 渐变插值 | 线性化并预乘后插值 | 同左，但输入已预乘 |
| 稿本摘要 | `LegacySceneHash`（与迁移前字节完全一致） | `SceneHash`（含 `color_model`） |
| 快照 `schema` | `prism-plate-v1` | `prism-plate-v2` |
| 封包 `schema` | `prism-plate-bundle-v1` | `prism-plate-bundle-v2` |

模型按版面（plate）逐稿判定，不按服务实例。因此同一版面的第 1 稿可以是 v1、第 2 稿是 v2，`GET /v1/plates/{id}` 会原样返回混合历史，两端分别按各自模型渲染。

## 2. 迁移：`POST /v1/plates/{id}/upgrade`

```json
{"expected_revision": 1}
```

语义：

- 把**最新**稿本按其自身模型转换为 v2，并追加为新稿本（序号 +1）。转换是纯数据变换：背景、实色、渐变各 stop、坐标、变换、clip/mask、opacity、blend、stop 偏移都不变，只有颜色分量改为线性光预乘，并写入 `color_model`。
- 旧稿本一个字节都不改：摘要、成品 id、已通过校样全部继续有效。
- `expected_revision` 不符返回 409；归档版面返回 409；版面不存在 404。
- 最新稿已是 v2 时为幂等成功，`changed=false` 且不追加稿本。该操作可安全重试。
- 响应为 `{"plate": {...}, "changed": true|false}`。

转换的可重现性：v1 渲染器在合成前本就把直通 sRGB 线性化并预乘，所以对 **alpha > 0** 的颜色，两种模型的合成结果逐像素相同。`walkthrough.py --workflow migration` 用固定摘要验证升级前后 PNG 摘要相同；`--workflow color-model` 进一步用 alpha=0 端点、近透明非零 stop 和透明/不透明背景验证 PNG 逐字节相同。SVG 除新增模型标记与 alpha=0 stop 的隐藏 RGB 归零外逐字节相同（见第 5 节）。

## 3. 编辑规则（防止静默降级）

- **同一版面不允许从 v2 退回 v1。** 若最新稿为 v2，`PUT` 提交无 `color_model` 的文档返回 409。否则新文档会冒充旧语义、被旧工具误读。
- 新稿（v2）中每个颜色的每个分量必须 `≤ alpha`；不满足即 400（`invalid solid color` / `invalid gradient stops` / `invalid canvas`）。v1 稿不受此限，历史数据不会被追溯拒绝。
- 未知 `color_model.version` 或 `space`/`alpha` 不匹配均 400，不做默认回退。
- 需要在旧模型上继续工作时，用 `POST /v1/plates/{id}/branch` 从某个 v1 历史稿分支出一个全新版面（第 1 稿为原 v1 文档），这样新身份可交给旧版编辑器继续。

## 4. 回退路径

回退粒度是**版面**，不是服务或数据根。

| 需求 | 可用的回退 | 说明 |
|---|---|---|
| 整机回旧版 | 回退前用 v1-only 数据根（快照仍为 `prism-plate-v1`） | 只要没有任何 v2 稿，旧二进制可正常恢复同一数据根 |
| 已在旧版签下校样 | 无需动作 | v1 稿及其成品、已通过校样在升级后按原 id 与摘要可取回，撤销保护仍生效 |
| 把新版内容交给旧版 | `branch` 出 v1 稿 → `GET /v1/plates/{new}/bundle` 得到 `prism-plate-bundle-v1` | 旧二进制只接受 v1 封包；v2 封包会被拒绝（未知字段 `color_model`） |
| 从 v2 稿退回 v1 编辑 | **只能退回原快照 / 原 v1 历史稿** | v2 → v1 的有损转换不提供：见第 5 节，无法从预乘值唯一恢复原始直通 RGB |
| 整个数据根已含 v2 | 保留原快照，在独立目录以旧版启动会拒绝（`unsupported snapshot schema`） | 这是刻意的：宁可拒绝也不静默改写颜色 |

要点：

- **能交给旧版的**：任一版面的 v1 历史稿、由 v1 稿分支出的版面、以及这些版面的 `bundle-v1` 封包。
- **不能交给旧版的**：v2 稿、含 v2 稿的封包、含 v2 稿的数据根。必须先在旧版侧重新按 v1 编辑，或使用迁移前保留的快照。

## 5. 无法从新表示恢复的信息

1. **alpha == 0 时的隐藏 RGB。** v2 预乘存储把它乘成 0。若要检查或复用隐藏 RGB，回到 v1 稿或原快照；v2 稿与由其导出的 SVG 中该 stop 固定为 `rgb(0%,0%,0%)`、`stop-opacity="0"`。
2. **原始 sRGB 编码值 on alpha>0。** `R/alpha` 可以精确还原直通值至浮点误差；升级写出的是浮点值而非用户输入的十进制字面量，若需要逐字面量复现，请保留原 v1 稿。
3. **`upgrade` 之前某个 v1 稿的精确浮点字面量**在逻辑上可由公式重算，但版本身份（摘要）只属于 v1 稿本身；不要假设 v2 稿摘要等于任何 v1 摘要。
4. **升级不改变**几何、变换、stop 偏移、蒙版、裁域、层级顺序与透明度；这些不是丢失项。

## 6. 摘要与身份

- v1 稿摘要沿用迁移前的规范化字节（`LegacySceneHash`），因此旧成品、旧封包、旧校样的 `X-Scene-Digest` 与引用全部继续校验通过。
- 成品 id 由 `(plate, revision, scene digest, format, content digest)` 派生；升级只新增稿本，旧成品 id 不变。
- v2 稿使用包含 `color_model` 的完整摘要；两种摘要空间不互通，封包 `schema` 与内容不符会被 400 拒绝。

## 7. SVG 差异

- v1 稿的 SVG 与迁移前逐字节相同（无模型标记），历史矢量稿可复现。
- v2 稿的 SVG 增加 `<desc>color-model:prism-plate-v2-linear-premultiplied</desc>`，颜色按 `R/alpha` 反预乘后重新 sRGB 编码，stop 的 `stop-opacity` 仍为 alpha。
- 浏览器对渐变 alpha 插值与覆盖率的处理仍与 PNG 权威结果可能不同，这一点不因本次迁移改变。

## 8. 验证

```sh
python3 scripts/walkthrough.py --workflow migration
python3 scripts/walkthrough.py --workflow color-model
```

`migration` 覆盖：旧校样签下 → 升级 → 旧成品/摘要/校样保护不变 → 新稿编辑渐变 → 混合历史读取 → 重启与 v2 封包导入 → v1 分支回退。`color-model` 覆盖：透明端点不同隐藏 RGB 的往返、接近透明非零 stop、透明与不透明背景、PNG 与 SVG 输出，以及不可表示的预乘颜色被拒绝。
