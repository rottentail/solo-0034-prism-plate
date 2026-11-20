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
| POST /v1/plates/{id}/deliveries | revision+region →201净化交付封包与误差报告；preflight:true 只报告可转换性 |

成品响应中的data为null，避免重复传输二进制；GET成品获得实际内容。If-None-Match精确匹配强ETag时返回304。成功校样可从持久化快照确认，当前版本没有校样列表/详情接口。GET版面返回完整历史，服务没有全局列表接口。

## 净化交付导出

`POST /v1/plates/{id}/deliveries` 输入 `region.points`（3..32 个严格凸多边形顶点，画布坐标，可顺时针或逆时针）与 `revision`（或 `preflight:true`）。成功返回 `{envelope, report}`：`envelope` 是单稿本封包，可直接 `POST /v1/bundles` 导入空数据根；`report` 是误差与裁切统计。

- `preflight:true` 不生成几何、不写入任何数据，逐稿本列出 `blockers`（`revision`/`layer`/`shape`/`reason`）。导出前先调用可避免对无法安全转换的稿件做无效转换。
- 交付稿只含所选稿本转换后的单稿本，不含原历史、成品、校样和任务；`X-Delivery-Digest` 为转换文档的稳定摘要，`If-None-Match` 精确匹配时返回 304。
- 区域外轮廓被几何删除而不是被裁域或隐藏属性遮住：所有保留顶点的世界坐标都在区域内，覆盖区域的裁域/蒙版会被替换为区域多边形。接收方移走任何图层属性都无法找回区域外几何。
- 区域内的镂空（evenodd 与反向环）、透明重叠与渐变保持原语义；与边界相交的轮廓在形状局部坐标内裁切，保留题目要求的填充规则、局部变换与渐变轴，完全在区域内的轮廓原样保留以便继续编辑。
- `report.max_png_channel_error` 是原稿与交付稿在同一 PNG 后端下、区域内逐像素的最大通道差（0..255）；`boundary_skipped_pixels` 是跨边界、由亚像素采样决定而不计入容差的像素数；`max_outside_channel_error` 必须为 0，否则导出失败。`curve_tolerance` 是保留曲线按既有 16/24 段展开的最大折线偏差，`boundary_epsilon` 是边界交点容差。

拒绝：区域非凸、退化、越界或顶点数不符为 400；存在自相交或过复杂交叉轮廓时导出为 400 并在 `preflight` 中指名；不存在的稿本为 404；计算名额不足为 429。
