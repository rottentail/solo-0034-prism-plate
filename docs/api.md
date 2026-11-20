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
| POST /v1/plates/{id}/render?revision=1&format=png | 201成品元数据；format也可为svg；可选width、height、fit选择输出尺寸 |
| POST /v1/plates/{id}/exposures?revision=1&format=png | 202瞬态任务；接受与render相同的尺寸参数 |
| GET /v1/exposures/{id} | queued/running/succeeded/failed/cancelled |
| DELETE /v1/exposures/{id} | 202取消请求 |
| GET /v1/artifacts/{id} | 图片；ETag为成品字节摘要；X-Scene-Digest为稿本摘要 |
| DELETE /v1/artifacts/{id} | 撤销成品 |
| POST /v1/proofs | id,artifact →201 open校样 |
| PUT /v1/proofs/{id} | state为accepted或rejected，note为说明 |
| GET /v1/plates/{id}/bundle | 含完整稿本历史的封包JSON |
| POST /v1/bundles | 原样封包；201导入，重名409 |

成品响应中的data为null，避免重复传输二进制；GET成品获得实际内容。If-None-Match精确匹配强ETag时返回304。成功校样可从持久化快照确认，当前版本没有校样列表/详情接口。GET版面返回完整历史，服务没有全局列表接口。

尺寸适配：渲染与异步任务可附加width、height与fit=contain|cover。width与height必须同时给出，边长1..1024；fit缺省为contain。contain等比缩放并居中，文档之外的留白为透明像素；cover等比填满并居中裁切。局部色刷、裁域、蒙版与镂空按同一仿射布局映射到输出像素。PNG按输出像素计入同一边遍历预算，更大尺寸不会获得额外工作量；SVG无逐像素工作量，仅受边长上限约束。成品元数据的target记录尺寸来源并参与成品标识，GET成品以X-Render-Target回显；不带尺寸的请求保持文档尺寸输出，既有成品标识与校样引用不变。
