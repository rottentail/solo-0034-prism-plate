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
| POST /v1/plates/{id}/reframe | revision,width,height,mode →可审阅预览（不追加） |
| POST /v1/plates/{id}/reframe（commit） | +expected_revision,preview_digest,commit →追加稿本 |
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

## 画布调整

`POST /v1/plates/{id}/reframe` 把某个历史稿本重新装到新画布上，分两步：先预览，再提交。预览是只读的，返回可直接审阅的完整目标document、画布仿射、留白和逐节点的影响清单：

- `mode`：`fit`整体等比适配（留白）；`preserve`保持1:1实物比例（大画布留白、小画布裁掉超出）；`crop`等比铺满（裁掉超出）。
- `placement`：`scale`与`offset_x/offset_y`，即 `p_target = scale*p_source + offset`。
- `margins`：目标画布未使用的边带，单位为目标像素。
- `shapes[]`：每个形状的`effect`（`intact`/`cropped`/`removed`）、目标坐标下的`bounds`和是否`visible`；`detail`汇总完全消失与边缘被裁的细节。
- `layers[]`：图层裁域与蒙版自身的`clip_effect`/`mask_effect`。

坐标语义：裁域与蒙版处于画布坐标，预览会把它们的每个点乘上画布仿射；形状`transform`与画布仿射左乘复合（先局部后画布）；形状`path`与`brush`仍在形状局部坐标，不做改写，因此色刷跟随形状缩放到新画布。三者不能只改宽高或只乘形状矩阵。

提交需同时给出`commit:true`、`expected_revision`和预览返回的`preview_digest`；摘要与重新计算的结果不一致时返回409，避免提交与审阅不同的结果。若预览报告有细节完全消失（`detail.removed_shapes`或`removed_layers`非空），还必须传`acknowledge_removed:true`，否则409。

提交后在稿本追加一条带`origin`元数据的修订：`{kind:"reframe",source_revision,source_digest,mode,target_width,target_height,scale,preview_digest}`。`origin`不参与场景摘要、也不影响渲染；旧稿本没有该字段仍可读写，既有成品与已签样校样保持原摘要、可独立重现。
