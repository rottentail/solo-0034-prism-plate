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
| POST /v1/proofs/{id}/annotations | id,region{x,y,w,h},comment →201区域批注；仅open的PNG校样可写 |
| PUT /v1/annotations/{id} | state为resolved或dismissed，handled_by_revision为后续稿本 |
| GET /v1/plates/{id}/annotations?revision=N | open批注投影到稿本N的对照台账（派生视图，不落库） |
| GET /v1/plates/{id}/review-sheet?revision=N | image/svg+xml：原校样原图与新稿并排，含投影区域与状态图例 |
| GET /v1/plates/{id}/bundle | 含完整稿本历史的封包JSON |
| POST /v1/bundles | 原样封包；201导入，重名409 |

成品响应中的data为null，避免重复传输二进制；GET成品获得实际内容。If-None-Match精确匹配强ETag时返回304。成功校样可从持久化快照确认，当前版本没有校样列表/详情接口。GET版面返回完整历史，服务没有全局列表接口。

## 区域批注与跨稿投影

退稿不再只是一段文字。审稿人在open校样上按当时图片的画布坐标记录矩形区域（region）与意见（comment），每条批注在写入时捕获：

- 画布宽高（canvas_width/canvas_height）；
- 若区域中心落在某个形状的绘制范围内，则记录锚点：所在layer/shape的id、该形状当时的仿射矩阵，以及区域在形状局部坐标系中的包围盒（local_region）；中心不在形状上时为无锚点的自由批注。

批注只能写入open校样，且要求PNG成品；校样裁决为accepted或rejected后不可再写，批注随终局校样永久保留。被终局校样引用的成品（含rejected）不可撤销，因此原校样图片与批注坐标始终一致。批注自身有open→resolved/dismissed生命周期，处理时必须指向同一版面更晚的已存在稿本（handled_by_revision），处理记录不可重复、不可回退；原始comment和region不提供改写接口。

`GET .../annotations?revision=N` 返回该版面所有open批注投影到稿本N的派生视图，每条给出显式关联状态，绝不因为id相同就静默重贴：

| status | 触发条件 | 坐标来源 |
|---|---|---|
| anchored | 锚定形状仍在，矩阵与画布均未变 | 局部包围盒按现矩阵重放，与原坐标一致 |
| relocated | 锚定形状仍在但仿射矩阵已变 | 用新矩阵重算，unverified=true需人工确认 |
| rescaled | 仅画布尺寸变化 | 形状锚点重放局部坐标；自由批注按宽高比例缩放 |
| orphaned | 锚定的layer/shape已消失 | 保留原画布坐标，不重贴到任何同名新对象 |
| absolute | 写入时无形状锚点且画布未变 | 原坐标直用 |

投影区域超出新画布时outside_canvas置位，reason用英文给出可审计的具体原因。该接口是按需派生的，不修改任何持久化事实。

`GET .../review-sheet?revision=N` 返回单张SVG审阅单：每个退稿来源一列，内嵌其不可变原始PNG和当时的原始批注框；最右列为稿本N的新渲染图与投影后的批注框。anchored为实线绿框，relocated/rescaled/orphaned为虚线并按状态着色，底部含图例与批注台账。审阅单按需同步渲染，不生成新成品、不改变摘要。示例产物见examples/review/，由scripts/annotation_demo.py经真实HTTP重新生成。
