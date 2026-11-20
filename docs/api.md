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
| POST /v1/templates | id,title,definition →201参数模板 |
| GET /v1/templates/{id} | 模板稿本历史 |
| PUT /v1/templates/{id} | expected_revision,definition →追加模板稿本 |
| POST /v1/templates/{id}/archive | 禁止模板修订；历史稿本仍可解析批量 |
| POST /v1/templates/{id}/batches?revision=1 | id,items[{label,params}] →201批量，逐项resolved/rejected |
| GET /v1/batches/{id} | 批量来源与逐项结果 |

模板定义包含 recipe（当前仅 badge-v1）、画布、调色、核心直径、参数域与联合约束；约束是对参数及派生量（core/ring/span/canvas_min/canvas_width/canvas_height）的线性不等式，引用系数非零，最多16条。批量中每个解析成功项生成以批量id为前缀的草稿版面，revision 1 为冻结解析文档，origin 字段记录模板、模板稿本与参数；拒绝项只在批量记录中保留原因，不占用版面。批量结构性错误（标签重复、稿本不存在、容量不足）整体拒绝，不产生批量记录。草稿版面是普通版面：可渲染、可校样、可修订，其 origin 来源不变。

成品响应中的data为null，避免重复传输二进制；GET成品获得实际内容。If-None-Match精确匹配强ETag时返回304。成功校样可从持久化快照确认，当前版本没有校样列表/详情接口。GET版面返回完整历史，服务没有全局列表接口。
