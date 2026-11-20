# HTTP契约

所有写入场景的JSON入口拒绝未知字段和尾随内容，最大8MiB。服务返回JSON错误；404为对象不存在，409为版本或生命周期冲突，429为成像容量不足，400为不符合输入契约，504为超时。未知路由由HTTP框架返回404。

| 方法与路径 | 输入及结果 |
|---|---|
| GET /healthz | ready |
| POST /v1/plates | id,title,document →201版面 |
| GET /v1/plates/{id} | 完整稿本历史 |
| PUT /v1/plates/{id} | expected_revision,document →追加稿本 |
| POST /v1/plates/{id}/branch | revision,id,title →201独立版面 |
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
| POST /v1/plates/{id}/corridor-proposals | revision,moves,channels →201受保护通道改稿提案 |
| GET /v1/corridor-proposals/{pid} | 瞬态提案（候选、通道报告、偏离范围） |
| GET /v1/corridor-proposals/{pid}/previews/{candidate}?format=png | 候选瞬态预览；format也可为svg；ETag为预览字节摘要 |
| POST /v1/corridor-proposals/{pid}/accept | candidate →201追加稿本；陈旧版本或通道复核失败409 |

成品响应中的data为null，避免重复传输二进制；GET成品获得实际内容。If-None-Match精确匹配强ETag时返回304。成功校样可从持久化快照确认，当前版本没有校样列表/详情接口。GET版面返回完整历史，服务没有全局列表接口。

## 受保护通道改稿

`corridor-proposals` 让设计师在已持久化稿本上标记需要保护的负空间通道并提交曲线节点
拖动意图，服务端返回满足保护条件的候选，不直接改历史。语义与几何依据见
[corridor.md](corridor.md)，要点：

- `channels[].centerline` 为画布坐标折线（2..64点，允许弯曲），`min_width` 为必须保留
  的最小画布宽度（0,512]；最多4条通道；
- `moves[]` 按 `shape_id` 加局部坐标 `from` 定位 M/L/Q/C 节点，`to` 为拖动终点；
  共享同一坐标的节点随各动词一起移动；最多16项；
- 响应 `boundary_alpha` 为可行边界（0..1），`feasible=false` 时 `limit` 给出完整意图
  的失败通道报告（最窄点与限制边界），每个候选的 `node_deviations` 给出实际/期望位移
  之比与最大、最小偏离百分比；
- 提案是瞬态对象（最多128条，不进快照、重启失效）。预览通过专用 previews 端点读取，
  其字节摘要与接受候选后正式渲染的成品摘要一致；
- 接受时重新编译候选并重跑全部通道断言，同时校验 `expected_revision`（提案创建时的
  最新稿本数）；任一不满足返回409且不追加稿本。原稿自身不满足标记条件时创建提案即
  返回400，不产生候选。

