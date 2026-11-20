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

## 轮廓路径的两种表示

形状的 `path`、图层的 `clip`/`mask` 各接受且只接受下列一种表示，两者互斥、不能同时出现：

1. 历史表示（继续可读、可写）：M/L/Q/C/Z 对象数组，字段名仍为 `path`、`clip`、`mask`，语义与旧版完全一致。
2. 版本化矢量交换表示：对象 `{"path_format":"svg-path-1","path_text":"<SVG path data>"}`。`path_format` 是版本标签，当前只接受字符串 `svg-path-1`；`path_text` 是单行路径文本。

svg-path-1 的准入边界：

- 动词只接受 `M L Q C Z` 及其小写相对形式；`H/V/A/S/T` 等一律拒绝，因此无法通过该字段传入任何任意 SVG 标记、样式、脚本或外链。
- 数值接受 SVG 路径语法（正负号、前导点、小数点、科学计数法），但必须有限；解析后坐标解析为绝对坐标，再进入与旧数组完全相同的闭合轮廓校验和细分流程。
- 仍要求每个子路径以 M 开始、以 Z 显式闭合、至少三个顶点；相对坐标、隐式重复动词和多子路径不改变填充规则（`nonzero`/`evenodd`）与镂空含义。
- 单个 `path_text` 上限 16KiB，操作数仍受 1..256 约束。
- 语法错误返回 400，错误信息包含字节偏移与列号，例如 `svg-path-1 syntax error at byte 5 (column 6): unsupported verb "H" ...`；结构性错误会标注来源节点（如 `shape s1 path (svg-path-1): ...` 或 `(legacy-op-array)`）。

版面对象响应新增只读的 `path_sources` 摘要数组，逐项给出轮廓角色（path/clip/mask）、节点、来源表示、操作数、子路径数与文本字节数，便于审计两种表示的来源。内容摘要（稿本 digest）按归一化后的绝对轮廓计算，同一几何无论用哪种表示提交，稿本 digest、PNG 与 SVG 成品字节均相同；存储与封包仍保留提交时的原始拼写，旧稿本的封包摘要算法保持不变。
