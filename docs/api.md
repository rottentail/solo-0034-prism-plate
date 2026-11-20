# HTTP契约

所有写入场景的JSON入口拒绝未知字段和尾随内容，最大8MiB。服务返回JSON错误；404为对象不存在，409为版本或生命周期冲突，429为成像容量不足，400为不符合输入契约，504为超时。未知路由由HTTP框架返回404。

| 方法与路径 | 输入及结果 |
|---|---|
| GET /healthz | ready |
| POST /v1/plates | id,title,document →201版面 |
| GET /v1/plates/{id} | 完整稿本历史 |
| PUT /v1/plates/{id} | expected_revision,document →追加稿本 |
| POST /v1/plates/{id}/branch | revision,id,title 及可选 selection/placement/canvas →201独立版面（含plate与preview） |
| POST /v1/plates/{id}/branch/preview | 同分支入参但无需id/title；200只读预览，不创建任何版面或成品 |
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

## 选层分支

分支不再只能复制整稿。请求体在 revision/id/title 之外接受：

- `selection`：引用数组，元素为 `{"layer": 图层id}` 整层或 `{"layer": 图层id, "shape": 形状id}` 单形状；省略或为空表示整稿（兼容旧分支）。同一图层不能既整层选取又选取其中形状，引用不得重复，未知图层/形状返回400。
- `placement`：`original`（默认，保留源版面坐标，超出新画布部分由画布裁切）或 `fit`（按选中几何的可见包围盒等比缩放并居中，几何、裁域、蒙版随版面仿射一起变换，笔刷保持形状局部坐标）。
- `canvas`：可选 `width`/`height`（1..512）与 `background`（0..1 sRGBA）；省略则继承源画布与背景。

预览与正式分支返回同一派生文档、`provenance`（源版面、稿本号、源稿摘要、放置方式与选取引用）和 `notices`。`notices` 必须随结果交给设计人员：`partial-layer` 说明图层结构已不完整；`region-retained` 说明裁域/蒙版仍在裁剪；`blend-context` 与 `opacity-context` 说明脱离原下层叠色后 screen/multiply 与半透明形状的观感变化；`background-changed`、`canvas-crop`、`fit-scaled` 分别说明换底、原位裁切与等比缩放。源稿永不改写，预览不落盘；正式分支产生独立首稿，可经 `/render` 得到与预览文档严格对应的实际PNG，再用摘要核对。
