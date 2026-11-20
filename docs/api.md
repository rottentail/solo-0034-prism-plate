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
| POST /v1/plates/{id}/render?revision=1&format=png | 201成品元数据；format也可为svg；带timeline的稿本必须给time |
| POST /v1/plates/{id}/exposures?revision=1&format=png | 202瞬态任务；可选time，提交即锁定时刻与动画来源 |
| GET /v1/exposures/{id} | queued/running/succeeded/failed/cancelled；定时任务回显time、timeline_digest、scene_digest |
| DELETE /v1/exposures/{id} | 202取消请求 |
| GET /v1/artifacts/{id} | 图片；ETag为成品字节摘要；X-Scene-Digest为稿本摘要；定时成品另有X-Sample-Time与X-Timeline-Digest |
| DELETE /v1/artifacts/{id} | 撤销成品 |
| POST /v1/proofs | id,artifact →201 open校样 |
| PUT /v1/proofs/{id} | state为accepted或rejected，note为说明 |
| GET /v1/plates/{id}/bundle | 含完整稿本历史的封包JSON |
| POST /v1/bundles | 原样封包；201导入，重名409 |

成品响应中的data为null，避免重复传输二进制；GET成品获得实际内容。If-None-Match精确匹配强ETag时返回304。成功校样可从持久化快照确认，当前版本没有校样列表/详情接口。GET版面返回完整历史，服务没有全局列表接口。

## 关键帧时间契约

文档可选携带 `timeline`（随稿本保存、随封包移交、恢复时重新编译准入），由 `duration`（正实数，单位由调用方约定，服务只做无量纲线性插值）和至多32条轨道组成，全部关键帧合计至多128个。每条轨道以形状id为 `target`，`kind` 只能是 `position`、`rotation`、`scale`、`opacity` 之一；每条轨道的关键帧时间必须在 `0..duration` 内严格递增，且每个关键帧只定义本通道字段（跨通道杂散字段按400拒绝），轨道目标必须是存在的形状。

各通道在自身关键帧之间做分段线性插值，时间早于首帧或晚于末帧时保持端点值，不外推：

- `position`：局部原点平移 `(x,y)`。
- `rotation`：绕局部原点的无界有符号角度（度）。角度永不重绕，`350→370` 表示跨越360边界的+20度扫掠，`179→-179` 表示显式的-358度扫掠，中间时刻不存在隐式回跳。
- `scale`：有符号非等比缩放 `(scale_x, scale_y)`，允许负值，线性经过零。负分量是镜像而非跳变；经过零的瞬间变换塌缩、图形面积为零，服务保留已验证可逆的原静态变换并把填充置为完全透明，相邻时刻恢复普通矩阵，该点是唯一被显式定义的不连续。
- `opacity`：0..1线性淡入淡出，实色与渐变stop的alpha同步缩放，透明对象连续显现。

渲染静态校样时在 `render`/`exposures` 上追加 `time=<非负有限数>`。无timeline的稿本不接受time（400），有timeline的稿本必须显式给time（没有隐式帧），time超出域为400。准入在写入/修订时对每个关键帧端点（含0与duration）取样并完整重编译，因此任意时刻的取样稿都满足同一静态契约（可逆变换、1.6亿边遍历预算等），渲染期不会出现未编译的失败路径。

定时成品与任务锁定四元来源：版面、稿本摘要、time、timeline内容摘要；成品稳定身份与持久化谱系校验均包含time和timeline摘要，重启恢复时会重新核对。异步任务在入队前完成取样与校验，工作者只渲染已冻结的取样稿，稿本后续修订不影响在途任务。

误差说明：时间插值本身是精确的分段线性（端点值精确，区间内为一次函数），关键帧端点取样与作者数值一致；剩余误差只来自既有渲染链路——SVG为 `%.9g` 数值输出，PNG为每像素四点覆盖采样与8位量化。连续密取样下矩阵分量与alpha按解析式变化，不存在关键帧端点之间的无解释跳变；塌缩点的语义见上。示例见 `examples/timed-proof.json`，端到端验证见 `walkthrough.py --workflow timeline`。
