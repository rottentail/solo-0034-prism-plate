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
| POST /v1/plates/{id}/edits/preview?revision=1&format=png | operations节点编辑批 →200预览PNG/SVG；format=json返回候选稿本与摘要，不写存储 |
| POST /v1/plates/{id}/edits | expected_revision,operations →201追加稿本；整批非法则400且无半份修改 |
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

## 节点编辑批

一次改稿由 `operations` 数组承载，按数组顺序对同一候选稿本依次生效，定位只使用形状与图层的稳定 id（不使用下标）。操作类型：

| type | 载荷 | 语义 |
|---|---|---|
| `move` | `{"shape","delta_x","delta_y"}` | 在形状现有仿射的平移项上叠加位移；局部轮廓与色刷坐标不变，版面位置整体移动 |
| `replace_brush` | `{"shape","brush"}` | 整体替换色刷（solid或linear），准入规则与建稿一致 |
| `relocate` | `{"shape","layer"}` | 把形状移入目标图层末尾；形状局部坐标、变换和色刷保留，合成改由目标图层的opacity/blend/clip/mask决定 |

预览与提交走同一组操作、同一份候选构造和同一条渲染路径。预览不产生稿本、不占容量，归档版面仍可预览；提交复用 `expected_revision` 冲突约定，旧稿本不可变，整批中任一操作找不到目标或候选稿本未通过编译准入时整体400，内存与快照都不留下部分修改。预览响应头 `X-Scene-Digest` 是候选稿本摘要，提交后新稿本摘要及最终PNG的 `scene_digest`/字节摘要与预览一致，即提交的就是用户确认过的结果。每批1..64个操作；同一形状移到其当前所在图层按非法组合拒绝。
