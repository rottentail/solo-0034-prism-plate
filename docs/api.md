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

成品响应中的data为null，避免重复传输二进制；GET成品获得实际内容。If-None-Match精确匹配强ETag时返回304。成功校样可从持久化快照确认，当前版本没有校样列表/详情接口。GET版面返回完整历史，服务没有全局列表接口。

## 透视映射字段

Shape 新增可选 `projective`：

| 字段 | 含义 |
|---|---|
| `projective.quad` | 展示面四边形，四角按左上、右上、右下、左下 |
| `projective.source` | 图稿侧对应四边形；省略时取 transform 后包围框 |
| `projective.affine` | true 时使用最小二乘仿射近似，用于对照而非真透视 |

规则：四边形必须有限、严格凸、不自交，否则400 `projective quad is degenerate or self-intersecting`；`source` 非四角为400；轮廓跨越投影地平线时为400 `projective mapping produces unbounded coordinates`，不会产生无限坐标。`quad` 也可以包含画布以外的位置，覆盖会被裁到四边形内，画布外部分不出现。

SVG 对透视形状的路径带 `data-prism-map`（`perspective` 或 `affine-approx`）、`data-prism-source="flattened"`、`data-prism-original`（原局部轮廓）以及可选的 `clip-path`；渐变带 `data-prism-gradient="perspective-resampled"`。PNG不改变既有字段与格式。
