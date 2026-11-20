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
| POST /v1/plates/{id}/coverage?revision=1 | families色系列表→201覆盖统计成品（format=coverage，JSON内容经成品接口读取） |
| POST /v1/plates/{id}/coverage/overlay?revision=1 | families→200同画布定位PNG，主要贡献8×8区块以轮廓标记 |
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

## 覆盖统计

`families` 为 1..16 个指定色系，每项含 `name`、`reference`（0..1 sRGB，含a）与可选 `max_delta_e`（默认12，CIE76 L*a*b* 距离）。色系按请求顺序匹配，观测色落入多个容差时归距离最近者；都不匹配时计入 `unmatched_area`，不强行归属。

统计基于与权威 PNG 相同的逐样本成像链（`paint.Evaluate`），因此遮挡、图层裁域/蒙版、组透明度、低透明度与 multiply/screen 混合均按实际成像规则处理，而不是按形状名义面积。

- `basis=supersampled_coverage`：主网格步长0.25（每像素16个样本），另以步长1.0的独立网格交叉检查。
- `area` 为 alpha 加权的可见面积（画布单位²），`area_min/area_max` 为按像素一致性给出的采样包络；`fraction` 系列为占画布比例。
- `artwork_area` 为有图层到达的面积，`background_area` 为无图层到达的面积，二者之和恒等于 `canvas_area`，与背景是否不透明无关。
- `blend_attribution` 按预乘 alpha 质量把观测像素分配给贡献图层，键为 `图层ID|blend`，multiply/screen 保留各自混合身份。
- `layers[].gross_area` 是未扣除上层遮挡的图层足迹，`visible_area` 是完整合成后的净可见 alpha 面积；`geometric_shapes` 是独立的几何（非采样）依据，给出局部与画布多边形面积，不扣除重叠、镂空或遮挡。
- 警告：`resolution_flip` 表示色系结论在两套采样网格间翻转或相对变化≥20%；`tiny_feature` 表示小于8×8定位块的连通边界特征；`invisible_geometry` 表示有几何足迹但无样本达到可见阈值（1/255）。警告只提示置信度，不改写面积数字。
- `regions` 给出该色系权重最高的至多8个8×8区块的画布像素坐标；overlay PNG 在权威渲染图上以彩色轮廓和半透明填色标出这些区块，用于在图上定位主要贡献区。

覆盖统计成品与 PNG/SVG 共用成品谱系、摘要、撤销与校样保护规则；同一稿本同一 families 集合得到稳定标识，families 不同则标识不同。overlay 不单独持久化，为确定性派生图像，成功返回200。
