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
| GET /v1/plates/{id}/compare?base=1&candidate=2 | 两稿本视觉比较报告；format=png返回差异图PNG |
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

## 稿本比较

GET /v1/plates/{id}/compare 比较同一版面的两个稿本（base与candidate，均≥1），是只读计算：不创建或修改稿本、成品与校样，响应由不可变稿本即时渲染得出，相同输入永远得到相同结果。format=report（默认）返回JSON报告，format=png返回差异图PNG（响应头X-Base-Scene-Digest与X-Candidate-Scene-Digest给出两侧稿本摘要）。与渲染共用两个计算名额，拥挤时同样返回429。

比较基于渲染像素而非场景文本：对象改名、轮廓起点轮换、共线边拆分等等价改写渲染结果一致，报告为identical，不会产生告警。比较坐标即版面坐标，原点左上，1单位=1像素；两稿本画布按同一原点对齐，不做缩放或平移。画布尺寸不同时比较画布取两者并集，超出某一稿本自身画布的像素在该侧按完全透明处理；每个稿本的背景只铺满它自己的画布，不外溢。差异图中透明区域衬白色仅供查看，不参与差异度量。

差异度量为预乘8位RGBA逐通道最大绝对差（0..255），预乘使全透明像素的RGB不参与比较。不超过1/255的逐像素差异处于单次8位量化步长之内，判为量化噪声；超过该地板的像素按8邻域聚成连通区域，逐个报告边界、像素数、最大/平均差异、来源（added仅候选侧有内容、removed仅基准侧有内容、modified两侧都有、backdrop仅背景或画布范围差异）及两侧覆盖该区域采样的layer/shape对象。报告最多详列64个区域，超出计入regions_truncated。summary.verdict取值：identical（完全一致）、quantization_noise_only（仅有量化级差异）、design_changes（存在设计变化）。细小镂空与透明渐变的局部变化各自成区，不会被全图平均分数掩盖。
