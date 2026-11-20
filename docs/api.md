# HTTP契约

所有写入场景的JSON入口拒绝未知字段和尾随内容，最大8MiB。服务返回JSON错误；404为对象不存在，409为版本或生命周期冲突，429为成像容量不足，400为不符合输入契约，504为超时。未知路由由HTTP框架返回404。

| 方法与路径 | 输入及结果 |
|---|---|
| GET /healthz | ready |
| POST /v1/plates | id,title,document →201版面 |
| GET /v1/plates/{id} | 完整稿本历史 |
| PUT /v1/plates/{id} | expected_revision,document →追加稿本 |
| POST /v1/plates/{id}/branch | revision,id,title →201独立版面 |
| POST /v1/plates/{id}/convert-fill?revision=1 | layer_id,shape_id,target_rule →200改稿候选与几何证据；不改写稿本 |
| POST /v1/plates/{id}/archive | 禁止修订；历史仍可读取 |
| POST /v1/plates/{id}/render?revision=1&format=png | 201成品元数据；format也可为svg |
| POST /v1/plates/{id}/exposures?revision=1&format=png | 202瞬态任务 |
| GET /v1/exposures/{id} | queued/running/succeeded/failed/cancelled |
| DELETE /v1/exposures/{id} | 202取消请求 |
| GET /v1/artifacts/{id} | 图片；ETag为成品字节摘要；X-Scene-Digest为稿本摘要 |
| DELETE /v1/artifacts/{id} | 撤销成品 |
| POST /v1/proofs | id,artifact →201 open校样 |
| PUT /v1/proofs/{id} | state为accepted或rejected，note为说明 |
| GET /v1/plates/{id}/bundle | 含完整稿本历史的封包JSON |
| POST /v1/bundles | 原样封包；201导入，重名409 |

成品响应中的data为null，避免重复传输二进制；GET成品获得实际内容。If-None-Match精确匹配强ETag时返回304。成功校样可从持久化快照确认，当前版本没有校样列表/详情接口。GET版面返回完整历史，服务没有全局列表接口。

## 填充规则转换

`POST /v1/plates/{id}/convert-fill` 在不触碰原稿的前提下，为单个形状生成 nonzero 与 evenodd 互转的改稿候选。它不是简单替换 `fill_rule` 字段，而是先把可见填充区域重写为与目标规则等价、仍可编辑的 M/L/Z 闭合路径，因此嵌套孔洞、相反绕向、相切与自交轮廓都能保持可见区域。请求体：

```json
{"layer_id": "cyan-glaze", "shape_id": "cyan-window", "target_rule": "nonzero"}
```

响应包含 `candidate_document`（仅该形状的路径与规则变化，笔刷、变换、图层裁域/蒙版和其他形状逐字节保留）、`fill_rule` 与 `report`。报告是随候选一起给出的几何证据，关键字段：

- `reliability`：`exact`（拓扑干净、全部等价检查通过）、`approx`（存在相切/近重合、窄通道或曲线展开，只能按既有细分契约近似）、或拒绝时不返回候选。
- `source_signed_fill_area` 与 `candidate_signed_fill_area`、`area_absolute_delta`、`area_tolerance`：基于扫描线积分的精确填充面积对照。
- `grid_points_checked`/`grid_mismatches`/`grid_on_edge_skipped`：用渲染器自身的点归属谓词做确定性网格比对；落在轮廓上的采样点跳过并计数，任何不一致都会拒绝候选。
- `min_channel_local_units`/`min_channel_device_units`：最窄通道宽度（经变换换算到设备坐标）；低于 0.25 设备单位时标记近似。
- `coincident_contacts`、`candidate_compiles`、`approximation_required`、`node_budget_blocked`：接触计数、候选是否通过统一编译准入、是否需要近似、是否超出 256 条路径指令的节点限额。
- `warnings`：提交前必须阅读的近似与限额说明。

该接口是只读推导：成功后版面稿本数量与规则保持原样。审阅报告后，把 `candidate_document` 通过普通 `PUT /v1/plates/{id}`（带 `expected_revision`）提交为新稿本；旧稿本依旧不可改写。曲线（Q/C）按共享的 16/24 段细分展开为折线，不会合成新的曲线动词，报告中会明确披露。镜像等负行列式变换在局部坐标完成转换并原样保留，渲染结果在镜像后仍可解释。
